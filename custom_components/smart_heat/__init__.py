"""Smart Heat: a smart thermostat per heating zone that learns how each zone heats up and cools down.

Step 3 (read-only): the nightly learning routine of the learning phase runs here, in HA, with the model
package heatmodel/ of this integration; the house is described in zones.yaml in the HA config; results go
to /config/smart_heat/ and to one sensor per zone."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.typing import ConfigType

from .const import CONF_HEATING_DIR, CONF_ZONES_FILE, DEFAULT_ZONES_FILE, DOMAIN, LEARN_TIME
from .runtime import SmartHeat, SmartHeatError

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
type SmartHeatConfigEntry = ConfigEntry[SmartHeat]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions."""

    async def learn_now(call: ServiceCall) -> None:
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            entry.async_create_background_task(hass, entry.runtime_data.async_run(), "smart_heat learn_now")

    hass.services.async_register(DOMAIN, "learn_now", learn_now)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SmartHeatConfigEntry) -> bool:
    """Load zones.yaml; schedule the nightly learning run."""
    if CONF_ZONES_FILE in entry.data:
        zones_file = entry.data[CONF_ZONES_FILE]
    elif CONF_HEATING_DIR in entry.data:  # 0.1.0 entries stored the folder
        zones_file = f"{entry.data[CONF_HEATING_DIR]}/zones.yaml"
    else:
        zones_file = DEFAULT_ZONES_FILE
    runtime = SmartHeat(hass, hass.config.path(zones_file))
    try:
        await runtime.async_load()
    except SmartHeatError as err:
        raise ConfigEntryError(str(err)) from err
    entry.runtime_data = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _nightly(now) -> None:
        entry.async_create_background_task(hass, runtime.async_run(), "smart_heat nightly learning")

    entry.async_on_unload(
        async_track_time_change(hass, _nightly, hour=LEARN_TIME[0], minute=LEARN_TIME[1], second=0)
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SmartHeatConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
