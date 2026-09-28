import pytest

from air_alert.geo import approach, bearing_deg, distance_km

LUBLIN = (51.2465, 22.5684)
WARSAW = (52.2297, 21.0122)


def test_distance_lublin_warsaw():
    assert distance_km(*LUBLIN, *WARSAW) == pytest.approx(153, abs=2)


def test_bearing_cardinal():
    assert bearing_deg(50, 20, 51, 20) == pytest.approx(0, abs=0.01)
    assert bearing_deg(50, 20, 50, 21) == pytest.approx(90, abs=0.5)


def test_head_on_track():
    # 100 km due east of home, flying west at 120 km/h
    pos = (LUBLIN[0], LUBLIN[1] + 100 / (111.2 * 0.6258))
    a = approach(LUBLIN, pos, heading=270, speed_kmh=120, radius_km=20)
    assert a["closing_kmh"] == pytest.approx(120, rel=0.01)
    assert a["cpa_km"] == pytest.approx(0, abs=1)
    assert a["cpa_min"] == pytest.approx(50, rel=0.02)
    assert a["eta_min"] == pytest.approx(40, rel=0.02)  # (100-20) km / 120 km/h


def test_passing_track_misses_radius():
    # 100 km east, flying north: never closer than ~100 km
    pos = (LUBLIN[0], LUBLIN[1] + 100 / (111.2 * 0.6258))
    a = approach(LUBLIN, pos, heading=0, speed_kmh=150, radius_km=20)
    assert a["cpa_km"] == pytest.approx(100, rel=0.02)
    assert a["eta_min"] is None


def test_receding_track():
    pos = (LUBLIN[0], LUBLIN[1] + 1)
    a = approach(LUBLIN, pos, heading=90, speed_kmh=150, radius_km=20)
    assert a["closing_kmh"] < 0
    assert a["eta_min"] is None


def test_inside_radius_eta_zero():
    a = approach(LUBLIN, (LUBLIN[0] + 0.05, LUBLIN[1]), heading=0, speed_kmh=150, radius_km=20)
    assert a["eta_min"] == 0.0
