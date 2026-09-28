"""Sensors."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import UnitOfLength, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AirAlertConfigEntry
from .classifier import LEVELS
from .entity import AirAlertEntity


def _live(d: dict) -> list[dict]:
    return [t for t in d["tracks"] if not t["stale"]]


def _nearest_eta(d: dict) -> dict | None:
    with_eta = [t for t in _live(d) if t["eta_min"] is not None]
    return min(with_eta, key=lambda t: t["eta_min"]) if with_eta else None


def _summary(t: dict) -> dict:
    return {k: t[k] for k in ("id", "type", "locality", "distance_km", "bearing_deg", "approaching", "eta_min")}


@dataclass(frozen=True, kw_only=True)
class AirAlertSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict], Any]
    attrs_fn: Callable[[dict], dict] = lambda d: {}


SENSORS = (
    AirAlertSensorDescription(
        key="level",
        device_class=SensorDeviceClass.ENUM,
        options=LEVELS,
        value_fn=lambda d: d["level"],
        attrs_fn=lambda d: {
            "official_level": d["official_level"],
            "osint_level": d["osint_level"],
            "reasons": d["reasons"],
            "official_alerts": d["official"],
            "ua_alerts_near": d["ua_alerts_near"],
            "sources": d["sources"],
        },
    ),
    AirAlertSensorDescription(
        key="closest_threat",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        value_fn=lambda d: _live(d)[0]["distance_km"] if _live(d) else None,
        attrs_fn=lambda d: _live(d)[0] if _live(d) else {},
    ),
    AirAlertSensorDescription(
        key="active_threats",
        native_unit_of_measurement="threats",
        value_fn=lambda d: len(_live(d)),
        attrs_fn=lambda d: {"threats": [_summary(t) for t in _live(d)[:20]]},
    ),
    AirAlertSensorDescription(
        key="approaching_threats",
        native_unit_of_measurement="threats",
        value_fn=lambda d: sum(1 for t in _live(d) if t["approaching"]),
        attrs_fn=lambda d: {"threats": [_summary(t) for t in _live(d) if t["approaching"]][:20]},
    ),
    AirAlertSensorDescription(
        key="eta",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda d: (t := _nearest_eta(d)) and t["eta_min"],
        attrs_fn=lambda d: {
            "estimate": "straight-line extrapolation, not a confirmed future position",
            **(_summary(t) if (t := _nearest_eta(d)) else {}),
        },
    ),
    AirAlertSensorDescription(
        key="last_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: d["updated"],
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: AirAlertConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(AirAlertSensor(entry.runtime_data, desc) for desc in SENSORS)


class AirAlertSensor(AirAlertEntity, SensorEntity):
    entity_description: AirAlertSensorDescription

    def __init__(self, coordinator, description: AirAlertSensorDescription) -> None:
        super().__init__(coordinator, "sensor", description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict:
        return self.entity_description.attrs_fn(self.coordinator.data)
