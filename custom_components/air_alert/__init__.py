"""Air Alert - air-threat level for the home location (RCB, RSO, NEPTUN)."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import AirAlertCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.GEO_LOCATION]

type AirAlertConfigEntry = ConfigEntry[AirAlertCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: AirAlertConfigEntry) -> bool:
    coordinator = AirAlertCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AirAlertConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
