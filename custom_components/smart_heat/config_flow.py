"""Config flow for Smart Heat: one entry; asks for the house description (zones.yaml)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import CONF_ZONES_FILE, DEFAULT_ZONES_FILE, DOMAIN
from .runtime import SmartHeatError, load_zones


class SmartHeatConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders = {"problem": ""}
        if user_input is not None:
            path = self.hass.config.path(user_input[CONF_ZONES_FILE])
            try:
                await self.hass.async_add_executor_job(load_zones, path)
            except SmartHeatError as err:
                errors["base"] = "invalid_zones_file"
                placeholders["problem"] = str(err)
            else:
                return self.async_create_entry(title="Smart Heat", data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ZONES_FILE, default=DEFAULT_ZONES_FILE): str}),
            errors=errors,
            description_placeholders=placeholders,
        )
