"""Threat assessment and level classification. Pure (no Home Assistant imports).

Official (RCB/RSO) and OSINT (NEPTUN) evidence are classified separately:
- official can reach ALARM; OSINT is capped at WARNING,
- with `alarm_needs_osint` an official level is lowered one step while no
  tracked threat is heading towards home (RCB alerts cover a whole voivodeship);
  it never drops below WATCH and the raw official signal stays visible,
- the final level is max(official, osint),
- extrapolated positions (CPA/ETA) only ever raise OSINT to WARNING.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from .geo import approach, bearing_deg, distance_km

LEVELS = ["safe", "watch", "warning", "alarm"]
INBOUND_DEG = 45  # course within this angle of the direction to home = "heading my way"


def rank(level: str) -> int:
    return LEVELS.index(level)


def top(*levels: str) -> str:
    return max(levels, key=rank)


@dataclass
class Settings:
    watch_km: float = 300
    warning_km: float = 100
    alarm_km: float = 30  # radius used for ETA ("time until it is this close")
    ua_alert_km: float = 150
    eta_warning_min: float = 30
    downgrade_min: float = 10
    official_ttl_h: float = 3
    alarm_needs_osint: bool = True


class TrackHistory:
    """Positions we observed per NEPTUN track -> observed velocity."""

    WINDOW = timedelta(minutes=20)

    def __init__(self) -> None:
        self._h: dict[str, deque] = {}

    def update(self, threats: list[dict], now: datetime) -> None:
        for t in threats:
            if t.get("areaOnly") or t.get("lat") is None:
                continue
            h = self._h.setdefault(t["id"], deque(maxlen=20))
            if not h or (h[-1][1], h[-1][2]) != (t["lat"], t["lon"]):
                h.append((now, t["lat"], t["lon"]))
        for tid in [k for k, h in self._h.items() if now - h[-1][0] > timedelta(hours=1)]:
            del self._h[tid]

    def velocity(self, tid: str, now: datetime) -> tuple[float, float] | None:
        """(speed km/h, heading) from our own observations, None if not credible."""
        pts = [p for p in self._h.get(tid, ()) if now - p[0] <= self.WINDOW]
        if len(pts) < 2:
            return None
        (t0, la0, lo0), (t1, la1, lo1) = pts[0], pts[-1]
        dt_h = (t1 - t0).total_seconds() / 3600
        dist = distance_km(la0, lo0, la1, lo1)
        if dt_h < 1 / 60 or dist < 1:
            return None
        return dist / dt_h, bearing_deg(la0, lo0, la1, lo1)


def assess_tracks(threats, home, s: Settings, history: TrackHistory, now) -> list[dict]:
    """Positioned NEPTUN threats within the watch radius, nearest first."""
    out = []
    for t in threats:
        if t.get("status") == "resolved" or t.get("areaOnly") or t.get("lat") is None:
            continue  # areaOnly lat/lon is an oblast centroid, not a position
        dist = distance_km(home[0], home[1], t["lat"], t["lon"])
        if dist > s.watch_km:
            continue
        info = {
            "id": t["id"], "type": t.get("type"), "title": t.get("title"),
            "locality": t.get("locality"), "region": t.get("region"),
            "latitude": t["lat"], "longitude": t["lon"],
            "distance_km": round(dist, 1),
            "bearing_deg": round(bearing_deg(home[0], home[1], t["lat"], t["lon"])),
            "heading": t.get("heading"),
            "confidence": t.get("confidenceLevel"), "source_count": t.get("sourceCount"),
            "count": t.get("count"), "uncertainty_km": t.get("uncertaintyKm") or 0,
            "position_quality": t.get("positionQuality"),
            "advisory": bool(t.get("advisory")), "stale": t.get("status") == "stale",
            "updated_at": t.get("updatedAt"),
            "inbound": False, "velocity_source": None, "speed_kmh": None, "approaching": None,
            "closing_kmh": None, "cpa_km": None, "cpa_min": None, "eta_min": None,
        }
        v = t.get("velocity") or {}
        if v.get("speedKmh") and v.get("bearingDeg") is not None:
            vel, info["velocity_source"] = (v["speedKmh"], v["bearingDeg"]), "reported"
        else:
            vel = history.velocity(t["id"], now)
            info["velocity_source"] = "observed" if vel else None
        # heading alone (no speed) is not enough to extrapolate
        if vel:
            a = approach(home, (t["lat"], t["lon"]), vel[1], vel[0], s.alarm_km)
            info.update({k: None if x is None else round(x, 1) for k, x in a.items()})
            info["speed_kmh"] = round(vel[0])
            info["approaching"] = a["closing_kmh"] > 0
        course = vel[1] if vel else t.get("heading")
        if course is not None:
            to_home = bearing_deg(t["lat"], t["lon"], home[0], home[1])
            info["inbound"] = abs((course - to_home + 180) % 360 - 180) <= INBOUND_DEG
        out.append(info)
    return sorted(out, key=lambda i: i["distance_km"])


def osint_level(tracks: list[dict], ua_alerts_near: list[dict], s: Settings) -> tuple[str, list[str]]:
    graded = []
    for t in tracks:
        if t["stale"]:
            continue
        lower_bound = max(0, t["distance_km"] - t["uncertainty_km"])
        warn = not t["advisory"] and (
            lower_bound <= s.warning_km
            or (t["eta_min"] is not None and t["eta_min"] <= s.eta_warning_min)
        )
        graded.append(("warning" if warn else "watch", t))
    level = top("safe", *(lvl for lvl, _ in graded))
    if ua_alerts_near:
        level = top(level, "watch")
    reasons = [f"{t['type']} {t['locality'] or t['region']}: {t['distance_km']} km"
               for lvl, t in graded if lvl == level][:5]
    reasons += [f"UA alert {a['name']} ({a['level']}): {a['distance_km']} km" for a in ua_alerts_near[:5]]
    return level, reasons


def ua_alerts_near(alerts: dict, raion_km: dict, oblast_km: dict, s: Settings) -> list[dict]:
    """Official Ukrainian air-raid alerts for regions within ua_alert_km of home."""
    out = []
    for kind, dists in (("raions", raion_km), ("oblasts", oblast_km)):
        for a in alerts.get(kind) or []:
            d = dists.get(a.get("key"))
            if d is not None and d <= s.ua_alert_km:
                out.append({"name": a.get("name"), "level": a.get("level"),
                            "since": a.get("since"), "distance_km": round(d)})
    return sorted(out, key=lambda a: a["distance_km"])


OFFICIAL_LEVEL = {("alarm", "local"): "alarm", ("warning", "local"): "warning",
                  ("alarm", "unknown"): "watch", ("warning", "unknown"): "watch"}


def official_level(items: list[dict]) -> tuple[str, list[str]]:
    """items: {source, kind, scope, stale, text}. Stale = no update within official_ttl_h:
    validity unknown, so it is kept as WATCH rather than dropped to SAFE."""
    level, reasons = "safe", []
    for i in items:
        lvl = OFFICIAL_LEVEL.get((i["kind"], i["scope"]), "safe")
        if i.get("stale") and lvl != "safe":
            lvl = "watch"
        if lvl != "safe":
            reasons.append(f"{i['source']}: {i['text']}")
        level = top(level, lvl)
    return level, reasons


def confirm_official(level: str, inbound: bool) -> str:
    """Official level after the OSINT direction check: one step lower when nothing
    tracked is heading towards home. Never below WATCH."""
    return level if inbound else {"alarm": "warning", "warning": "watch"}.get(level, level)


def any_inbound(tracks: list[dict]) -> bool:
    return any(t["inbound"] and not t["stale"] and not t["advisory"] for t in tracks)


class LevelHold:
    """Escalate immediately; lower only after the lower level persisted `hold`."""

    def __init__(self, hold: timedelta) -> None:
        self.hold = hold
        self.level = "safe"
        self._lower_since: datetime | None = None

    def update(self, level: str, now: datetime) -> str:
        if rank(level) >= rank(self.level):
            self.level, self._lower_since = level, None
        elif self._lower_since is None:
            self._lower_since = now
        elif now - self._lower_since >= self.hold:
            self.level, self._lower_since = level, None
        return self.level
