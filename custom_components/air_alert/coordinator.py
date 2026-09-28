"""Fetches all sources and turns them into one threat snapshot."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import date, datetime, timedelta
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .classifier import (
    LevelHold,
    Settings,
    TrackHistory,
    any_inbound,
    assess_tracks,
    confirm_official,
    official_level,
    osint_level,
    top,
    ua_alerts_near,
)
from .const import (
    CONF_VOIVODESHIP,
    DOMAIN,
    EVENT_LEVEL_CHANGED,
    NEPTUN,
    OFFICIAL_INTERVAL,
    OPTION_DEFAULTS,
    RCB_BASE,
    RCB_LIST,
    RSO_FEED,
    SCAN_INTERVAL,
    STALE_AFTER,
    USER_AGENT,
)
from .geo import region_distances
from .sources import ParseError, parse_rcb_detail, parse_rcb_list, parse_rso, rcb_article_status

_LOGGER = logging.getLogger(__name__)
PL_TZ = dt_util.get_time_zone("Europe/Warsaw")  # RCB/RSO publish Polish local times


def _pl_now() -> datetime:
    return dt_util.now(PL_TZ)


class AirAlertCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.settings = Settings(**{**OPTION_DEFAULTS, **entry.options})
        self.home = (entry.data[CONF_LATITUDE], entry.data[CONF_LONGITUDE])
        self.voivodeship = entry.data[CONF_VOIVODESHIP]
        self.session = async_get_clientsession(hass)
        self.history = TrackHistory()
        self.hold = LevelHold(timedelta(minutes=self.settings.downgrade_min))
        # first time we saw each RCB article revision; survives restarts so a
        # restart does not re-arm the official_ttl_h window
        self.store: Store[dict[str, str]] = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self._seen: dict[str, str] = {}
        self._raion_km: dict[str, float] | None = None
        self._oblast_km: dict[str, float] | None = None
        self._official_at: datetime | None = None
        self._last_level: str | None = None
        self._inbound_until: datetime | None = None
        self._src = {n: {"ok": None, "last_success": None, "error": None} for n in ("neptun", "rcb", "rso")}
        self._neptun: dict[str, Any] = {"threats": [], "alerts": {}}
        self._rcb: dict | None = None
        self._rcb_revs: dict[str, int] = {}  # article url -> most revisions seen
        self._rso: dict[str, Any] = {}

    async def _async_setup(self) -> None:
        self._seen = await self.store.async_load() or {}

    async def _get(self, url: str, *, text: bool = False) -> Any:
        async with asyncio.timeout(20), self.session.get(url, headers={"User-Agent": USER_AGENT}) as r:
            r.raise_for_status()
            return await r.text() if text else await r.json(content_type=None)

    async def _run(self, name: str, job) -> None:
        """One source failing must not take the others down; its last data is kept."""
        try:
            await job
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError) as err:
            self._src[name].update(ok=False, error=str(err) or type(err).__name__)
            _LOGGER.warning("%s fetch failed: %r", name, err)
        else:
            self._src[name].update(ok=True, error=None, last_success=dt_util.utcnow())

    async def _fetch_neptun(self) -> None:
        threats = await self._get(f"{NEPTUN}/api/v1/threats")
        alerts = await self._get(f"{NEPTUN}/api/v1/alerts")
        if self._raion_km is None:
            raions = await self._get(f"{NEPTUN}/raions.geojson")
            oblasts = await self._get(f"{NEPTUN}/oblasts.geojson")
            self._raion_km, self._oblast_km = await self.hass.async_add_executor_job(
                lambda: (region_distances(self.home, raions), region_distances(self.home, oblasts))
            )
        # drop malformed records so one bad track cannot break the whole evaluation
        self._neptun = {
            "threats": [
                t for t in threats.get("threats") or []
                if isinstance(t, dict) and t.get("id")
                and all(isinstance(t.get(k), (int, float)) for k in ("lat", "lon"))
            ],
            "alerts": alerts if isinstance(alerts, dict) else {},
        }

    async def _fetch_rcb(self) -> None:
        """The newest air-threat article that applies to us defines the RCB state
        (list is newest first), so a later all-clear article supersedes an older alarm."""
        today = _pl_now().date()
        for it in parse_rcb_list(await self._get(RCB_LIST, text=True)):
            if (today - date.fromisoformat(it["date"])).days > 1:
                break
            revs = parse_rcb_detail(await self._get(RCB_BASE + it["url"], text=True))
            # gov.pl sits behind a cache (max-age=600) that sometimes serves an older copy;
            # never step back to fewer revisions, or a cancelled alert briefly comes back
            if len(revs) < self._rcb_revs.get(it["url"], 0):
                _LOGGER.debug("Stale copy of %s (%d revisions), keeping previous state", it["url"], len(revs))
                return
            self._rcb_revs[it["url"]] = len(revs)
            st = rcb_article_status(revs, self.voivodeship)
            if st["kind"] in ("alarm", "warning", "cancelled") and st["scope"] != "other":
                self._rcb = {**it, **st}
                return
        self._rcb = None

    async def _fetch_rso(self) -> None:
        # parsed at evaluation time so valid_to is honoured even if RSO stops answering
        data = await self._get(RSO_FEED.format(self.voivodeship))
        if not isinstance(data, dict):
            raise ParseError("RSO: unexpected payload")
        self._rso = data

    async def _async_update_data(self) -> dict[str, Any]:
        now = dt_util.utcnow()
        jobs = [self._run("neptun", self._fetch_neptun())]
        if self._official_at is None or now - self._official_at >= OFFICIAL_INTERVAL:
            self._official_at = now
            jobs += [self._run("rcb", self._fetch_rcb()), self._run("rso", self._fetch_rso())]
        await asyncio.gather(*jobs)
        last = [s["last_success"] for s in self._src.values() if s["last_success"]]
        if not last or now - max(last) > STALE_AFTER:
            raise UpdateFailed("No source reachable")
        return self._evaluate(now)

    def _official_items(self, now: datetime) -> list[dict]:
        s, items, new = self.settings, [], False
        if it := self._rcb:
            rev_id = f"{it['url']}#{hashlib.sha1(it['text'].encode()).hexdigest()[:12]}"
            if rev_id not in self._seen:
                new = True
                day = date.fromisoformat(it["date"])
                known_article = any(k.startswith(it["url"] + "#") for k in self._seen)
                # unknown publish time: a revision we see appear counts as new; a whole
                # article from a previous day seen for the first time counts as old
                self._seen[rev_id] = (
                    now if known_article or day == _pl_now().date()
                    else datetime.combine(day, datetime.min.time(), PL_TZ)
                ).isoformat()
            first = datetime.fromisoformat(self._seen[rev_id])
            items.append({
                "source": "RCB", "kind": it["kind"], "scope": it["scope"], "title": it["title"],
                "text": it["text"], "url": RCB_BASE + it["url"], "first_seen": self._seen[rev_id],
                "stale": now - first > timedelta(hours=s.official_ttl_h),
            })
        if new:
            cutoff = now - timedelta(days=3)
            self._seen = {k: v for k, v in self._seen.items() if datetime.fromisoformat(v) >= cutoff}
            self.store.async_delay_save(lambda: self._seen, 5)
        items += [{"source": "RSO", "scope": "local", "stale": False, **it}
                  for it in parse_rso(self._rso, _pl_now().replace(tzinfo=None))]
        return items

    def _evaluate(self, now: datetime) -> dict[str, Any]:
        s = self.settings
        threats = self._neptun["threats"]
        self.history.update(threats, now)
        tracks = assess_tracks(threats, self.home, s, self.history, now)
        ua_near = ua_alerts_near(self._neptun["alerts"], self._raion_km or {}, self._oblast_km or {}, s)
        osint_raw, osint_reasons = osint_level(tracks, ua_near, s)
        osint = self.hold.update(osint_raw, now)
        official_items = self._official_items(now)
        official, official_reasons = official_level(official_items)
        # hold "heading my way" like the OSINT level, so ALARM does not flap when a track blinks out
        if any_inbound(tracks):
            self._inbound_until = now + timedelta(minutes=s.downgrade_min)
        inbound = self._inbound_until is not None and now <= self._inbound_until
        official_eff = confirm_official(official, inbound or not s.alarm_needs_osint)
        if official_eff != official:
            official_reasons = [f"{r} (no tracked threat heading here: {official} -> {official_eff})"
                                for r in official_reasons]
        level = top(official_eff, osint)
        reasons = official_reasons + osint_reasons
        if self._last_level is not None and level != self._last_level:
            self.hass.bus.async_fire(EVENT_LEVEL_CHANGED, {
                "entry_id": self.config_entry.entry_id, "from": self._last_level, "to": level,
                "official_level": official, "official_effective": official_eff,
                "osint_level": osint, "reasons": reasons,
            })
        self._last_level = level
        return {
            "level": level,
            "official_level": official,  # raw, before the direction check
            "official_effective": official_eff,
            "inbound": inbound,
            "osint_level": osint,
            "reasons": reasons,
            "official": [i for i in official_items
                         if i["kind"] in ("alarm", "warning") and i["scope"] != "other"],
            "tracks": tracks,
            "ua_alerts_near": ua_near,
            "sources": {n: {**v, "last_success": v["last_success"] and v["last_success"].isoformat()}
                        for n, v in self._src.items()},
            "updated": now,
        }
