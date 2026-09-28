import json
from datetime import datetime, timedelta

from conftest import FIXTURES

from air_alert.classifier import (
    LevelHold,
    Settings,
    TrackHistory,
    any_inbound,
    assess_tracks,
    confirm_official,
    official_level,
    osint_level,
    ua_alerts_near,
)

HOME = (51.2465, 22.5684)  # Lublin
NOW = datetime(2026, 9, 28, 18, 0)
S = Settings()


def threat(**kw):
    t = {"id": "t1", "type": "uav", "lat": 51.25, "lon": 24.0, "status": "active",
         "uncertaintyKm": 4, "locality": "X", "region": "R"}
    t.update(kw)
    return t


def test_neptun_fixture_is_out_of_range_for_lublin():
    data = json.loads((FIXTURES / "neptun_threats.json").read_text(encoding="utf-8"))
    tracks = assess_tracks(data["threats"], HOME, S, TrackHistory(), NOW)
    assert tracks == []  # all tracks are around Kyiv / Sumy, > 500 km away
    assert osint_level(tracks, [], S) == ("safe", [])


def test_levels_by_distance():
    far = assess_tracks([threat(lon=26.0)], HOME, S, TrackHistory(), NOW)  # ~240 km
    assert osint_level(far, [], S)[0] == "watch"
    near = assess_tracks([threat(lon=23.9)], HOME, S, TrackHistory(), NOW)  # ~93 km
    assert osint_level(near, [], S)[0] == "warning"


def test_osint_never_alarm_and_advisory_capped():
    on_top = assess_tracks([threat(lat=HOME[0], lon=HOME[1])], HOME, S, TrackHistory(), NOW)
    assert osint_level(on_top, [], S)[0] == "warning"
    adv = assess_tracks([threat(lon=23.0, advisory=True)], HOME, S, TrackHistory(), NOW)
    assert osint_level(adv, [], S)[0] == "watch"


def test_area_only_stale_resolved_ignored():
    ts = [threat(id="a", areaOnly=True, lat=HOME[0], lon=HOME[1]),
          threat(id="b", status="resolved", lon=23.0),
          threat(id="c", status="stale", lon=23.0)]
    tracks = assess_tracks(ts, HOME, S, TrackHistory(), NOW)
    assert [t["id"] for t in tracks] == ["c"]
    assert osint_level(tracks, [], S)[0] == "safe"


def test_heading_only_gives_no_eta():
    t = assess_tracks([threat(heading=270)], HOME, S, TrackHistory(), NOW)[0]
    assert t["eta_min"] is None and t["approaching"] is None


def test_reported_velocity_eta_triggers_warning():
    # ~200 km east, flying west at 600 km/h -> enters 30 km radius in ~17 min
    t = threat(lon=25.43, velocity={"bearingDeg": 270, "speedKmh": 600})
    tracks = assess_tracks([t], HOME, S, TrackHistory(), NOW)
    assert tracks[0]["approaching"] and 15 < tracks[0]["eta_min"] < 19
    assert osint_level(tracks, [], S)[0] == "warning"


def test_observed_velocity_from_history():
    h = TrackHistory()
    h.update([threat(lon=24.0)], NOW - timedelta(minutes=5))
    h.update([threat(lon=23.9)], NOW)  # ~7 km west in 5 min = ~84 km/h
    t = assess_tracks([threat(lon=23.9)], HOME, S, h, NOW)[0]
    assert t["velocity_source"] == "observed"
    assert 75 < t["speed_kmh"] < 95 and t["approaching"]


def test_ua_alerts_near():
    alerts = {"oblasts": [{"key": "волинська", "name": "Волинська область", "level": "red"}],
              "raions": [{"key": "київський", "name": "Київський район", "level": "red"}]}
    near = ua_alerts_near(alerts, {"київський": 500}, {"волинська": 90}, S)
    assert [a["name"] for a in near] == ["Волинська область"]
    assert osint_level([], near, S)[0] == "watch"


def test_official_levels_and_stale():
    items = [{"source": "RCB", "kind": "alarm", "scope": "local", "text": "x"}]
    assert official_level(items)[0] == "alarm"
    assert official_level([dict(items[0], stale=True)])[0] == "watch"
    assert official_level([dict(items[0], scope="unknown")])[0] == "watch"
    assert official_level([dict(items[0], scope="other")])[0] == "safe"
    assert official_level([dict(items[0], kind="cancelled")])[0] == "safe"


def test_level_hold():
    h = LevelHold(timedelta(minutes=10))
    assert h.update("warning", NOW) == "warning"
    assert h.update("safe", NOW + timedelta(minutes=1)) == "warning"
    assert h.update("safe", NOW + timedelta(minutes=5)) == "warning"
    assert h.update("safe", NOW + timedelta(minutes=11)) == "safe"
    assert h.update("alarm", NOW + timedelta(minutes=12)) == "alarm"  # escalation is immediate


def test_inbound_by_course():
    # east of home: flying west = towards home, flying north = not
    towards = assess_tracks([threat(heading=265)], HOME, S, TrackHistory(), NOW)
    past = assess_tracks([threat(heading=0)], HOME, S, TrackHistory(), NOW)
    unknown = assess_tracks([threat(heading=None)], HOME, S, TrackHistory(), NOW)
    assert towards[0]["inbound"] and any_inbound(towards)
    assert not past[0]["inbound"] and not unknown[0]["inbound"]
    assert not any_inbound(assess_tracks([threat(heading=265, advisory=True)], HOME, S, TrackHistory(), NOW))
    assert not any_inbound(assess_tracks([threat(heading=265, status="stale")], HOME, S, TrackHistory(), NOW))


def test_official_needs_inbound_threat():
    assert confirm_official("alarm", inbound=True) == "alarm"
    assert confirm_official("alarm", inbound=False) == "warning"
    assert confirm_official("warning", inbound=False) == "watch"
    assert confirm_official("watch", inbound=False) == "watch"  # stale/unknown scope stays visible
    assert confirm_official("safe", inbound=False) == "safe"
