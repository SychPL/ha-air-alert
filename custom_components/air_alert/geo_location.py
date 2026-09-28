"""One geo_location entity per positioned NEPTUN track (shows up on the HA map)."""
from __future__ import annotations

from homeassistant.components.geo_location import GeolocationEvent
from homeassistant.const import UnitOfLength
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AirAlertConfigEntry
from .const import ATTRIBUTION, DOMAIN
from .coordinator import AirAlertCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: AirAlertConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    known: dict[str, ThreatLocation] = {}

    @callback
    def sync() -> None:
        ids = {t["id"] for t in coordinator.data["tracks"]}
        for tid in known.keys() - ids:
            hass.async_create_task(known.pop(tid).async_remove(force_remove=True))
        new = [ThreatLocation(coordinator, tid) for tid in ids - known.keys()]
        known.update((e.track_id, e) for e in new)
        async_add_entities(new)

    sync()
    entry.async_on_unload(coordinator.async_add_listener(sync))


class ThreatLocation(CoordinatorEntity[AirAlertCoordinator], GeolocationEvent):
    _attr_source = DOMAIN
    _attr_attribution = ATTRIBUTION
    _attr_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_icon = "mdi:airplane-alert"

    def __init__(self, coordinator: AirAlertCoordinator, track_id: str) -> None:
        super().__init__(coordinator)
        self.track_id = track_id
        self._handle_coordinator_update_values()

    def _track(self) -> dict | None:
        return next((t for t in self.coordinator.data["tracks"] if t["id"] == self.track_id), None)

    def _handle_coordinator_update_values(self) -> None:
        if t := self._track():
            self._attr_name = f"{t['title'] or t['type']} {t['locality'] or ''}".strip()
            self._attr_latitude, self._attr_longitude = t["latitude"], t["longitude"]
            self._attr_distance = t["distance_km"]
            self._attr_extra_state_attributes = {
                k: t[k] for k in ("type", "region", "heading", "confidence", "source_count",
                                  "uncertainty_km", "position_quality", "advisory", "stale",
                                  "inbound", "approaching", "eta_min", "updated_at")
            }

    @callback
    def _handle_coordinator_update(self) -> None:
        if self._track():  # vanished tracks are removed by sync()
            self._handle_coordinator_update_values()
            super()._handle_coordinator_update()
