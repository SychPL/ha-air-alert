"""Pure geodesy helpers (no Home Assistant imports, unit-testable)."""
from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0088


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2, 0 = north, clockwise."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def region_distances(home: tuple[float, float], geojson: dict) -> dict[str, float]:
    """Feature `key` -> distance (km) from home to the nearest boundary vertex."""
    # ponytail: vertex distance, not point-in-polygon; fine for a home outside the regions
    # (Poland vs Ukrainian raions), would need containment for a home inside one
    out = {}
    for f in geojson.get("features", []):
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        out[f["properties"]["key"]] = min(
            distance_km(home[0], home[1], lat, lon)
            for poly in polys for ring in poly for lon, lat in ring
        )
    return out


def _to_xy(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    # ponytail: equirectangular around home, fine for the few hundred km we care about
    x = math.radians(lon - lon0) * math.cos(math.radians(lat0)) * EARTH_RADIUS_KM
    y = math.radians(lat - lat0) * EARTH_RADIUS_KM
    return x, y


def approach(
    home: tuple[float, float],
    pos: tuple[float, float],
    heading: float,
    speed_kmh: float,
    radius_km: float,
) -> dict[str, float | None]:
    """Straight-line extrapolation of a track relative to home.

    Returns closing speed (km/h, >0 = approaching), closest point of approach
    (km) and the time to it (min), and the time until the track enters
    `radius_km` around home (min, None if it never does).
    These are estimates of a constant-course assumption, not future positions.
    """
    px, py = _to_xy(pos[0], pos[1], home[0], home[1])
    h = math.radians(heading)
    vx, vy = speed_kmh * math.sin(h), speed_kmh * math.cos(h)
    dist = math.hypot(px, py)
    pv, vv = px * vx + py * vy, vx * vx + vy * vy
    closing = -pv / dist if dist else 0.0
    if vv == 0 or pv >= 0:  # stationary or moving away
        return {"closing_kmh": closing, "cpa_km": dist, "cpa_min": 0.0,
                "eta_min": 0.0 if dist <= radius_km else None}
    t_cpa = -pv / vv
    cpa = math.hypot(px + vx * t_cpa, py + vy * t_cpa)
    eta = None
    if dist <= radius_km:
        eta = 0.0
    elif cpa <= radius_km:
        # smallest t with |p + v t| = r
        c = dist * dist - radius_km * radius_km
        eta = (-pv - math.sqrt(pv * pv - vv * c)) / vv * 60
    return {"closing_kmh": closing, "cpa_km": cpa, "cpa_min": t_cpa * 60, "eta_min": eta}
