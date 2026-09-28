"""Config flow: pick the location (default HA home), confirm the voivodeship, tune radii."""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.const import CONF_LATITUDE, CONF_LOCATION, CONF_LONGITUDE
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    LocationSelector,
    LocationSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import CONF_VOIVODESHIP, DOMAIN, NOMINATIM, OPTION_DEFAULTS, USER_AGENT
from .sources import VOIVODESHIPS, voivodeship_from_text


class AirAlertConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._location: dict[str, float] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._location = user_input[CONF_LOCATION]
            return await self.async_step_area()
        home = {CONF_LATITUDE: self.hass.config.latitude, CONF_LONGITUDE: self.hass.config.longitude}
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_LOCATION, default=home): LocationSelector(LocationSelectorConfig(radius=False)),
            }),
        )

    async def async_step_area(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        lat, lon = self._location[CONF_LATITUDE], self._location[CONF_LONGITUDE]
        if user_input is not None:
            await self.async_set_unique_id(f"{lat:.3f}_{lon:.3f}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"Air Alert ({VOIVODESHIPS[user_input[CONF_VOIVODESHIP]][0]})",
                data={CONF_LATITUDE: lat, CONF_LONGITUDE: lon, **user_input},
            )
        guess = await self._guess_voivodeship(lat, lon)
        key = vol.Required(CONF_VOIVODESHIP, default=guess) if guess else vol.Required(CONF_VOIVODESHIP)
        return self.async_show_form(
            step_id="area",
            data_schema=vol.Schema({
                key: SelectSelector(SelectSelectorConfig(
                    options=list(VOIVODESHIPS), translation_key=CONF_VOIVODESHIP,
                    mode=SelectSelectorMode.DROPDOWN,
                )),
            }),
        )

    async def _guess_voivodeship(self, lat: float, lon: float) -> str | None:
        """Reverse-geocode once (OpenStreetMap Nominatim); the user can correct it."""
        params = {"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 5, "accept-language": "pl"}
        try:
            async with asyncio.timeout(10), async_get_clientsession(self.hass).get(
                NOMINATIM, params=params, headers={"User-Agent": USER_AGENT}
            ) as r:
                data = await r.json(content_type=None)
            return voivodeship_from_text(data.get("address", {}).get("state", ""))
        except (aiohttp.ClientError, TimeoutError, ValueError, AttributeError):
            return None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> AirAlertOptionsFlow:
        return AirAlertOptionsFlow()


class AirAlertOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            # tracks outside watch_km are dropped before grading, so the radii must nest
            if user_input["alarm_km"] <= user_input["warning_km"] <= user_input["watch_km"]:
                return self.async_create_entry(data=user_input)
            errors["base"] = "radii_order"
        current = {**OPTION_DEFAULTS, **self.config_entry.options, **(user_input or {})}
        return self.async_show_form(
            step_id="init",
            errors=errors,
            data_schema=vol.Schema({
                vol.Required(k, default=current[k]): BooleanSelector()
                if isinstance(v, bool)
                else NumberSelector(NumberSelectorConfig(min=0, max=2000, step=1, mode=NumberSelectorMode.BOX))
                for k, v in OPTION_DEFAULTS.items()
            }),
        )
