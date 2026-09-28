"""Official (RCB / RSO) alert binary sensor."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AirAlertConfigEntry
from .entity import AirAlertEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: AirAlertConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([OfficialAlert(entry.runtime_data)])


class OfficialAlert(AirAlertEntity, BinarySensorEntity):
    """On while an official, non-cancelled air-threat message applies to our area."""

    _attr_device_class = BinarySensorDeviceClass.SAFETY

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "binary_sensor", "rcb")

    @property
    def is_on(self) -> bool:
        return self.coordinator.data["official_level"] != "safe"

    @property
    def extra_state_attributes(self) -> dict:
        return {"level": self.coordinator.data["official_level"], "alerts": self.coordinator.data["official"]}
