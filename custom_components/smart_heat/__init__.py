"""Smart Heat: a smart thermostat per heating zone (AUTO_HEAT_DESIGN.md, decision 28).

Per zone the owner's equation (dead time + minutes per K + minutes per K outside) is learned from every
warm-up, 30 min after it reached its target. Smart Heat keeps its own schedule per zone and one-off
"target by time" requests; every minute it decides when to raise the setpoint so the room is within
the tolerance of the target at the desired time. A zone only switches when its control switch is on;
otherwise everything runs in shadow and is only logged. Two cards come with it (frontend/)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from . import websocket
from .const import (CARDS_URL, CONF_HEATING_DIR, CONF_ZONES_FILE, DEFAULT_ZONES_FILE, DOMAIN, PLAN_INTERVAL,
                    RECORD_INTERVAL)
from .runtime import SmartHeat, SmartHeatError

PLATFORMS = [Platform.SENSOR, Platform.SWITCH]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
type SmartHeatConfigEntry = ConfigEntry[SmartHeat]


def runtime_of(hass: HomeAssistant) -> SmartHeat:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("Smart Heat is not loaded")
    return entries[0].runtime_data


def parse_ready(value: str, now: datetime) -> datetime:
    """'HH:MM' (the next time it comes) or a date and time (local when no zone is given)."""
    t = dt_util.parse_time(value) if len(value) <= 8 else None
    if t is not None:
        at = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        return at if at > now else at + timedelta(days=1)
    at = dt_util.parse_datetime(value)
    if at is None:
        raise ServiceValidationError(f"not a time: {value}")
    return dt_util.as_local(at if at.tzinfo else at.replace(tzinfo=dt_util.get_default_time_zone()))


def zone_of(rt: SmartHeat, zone: str) -> str:
    if zone not in rt.zones:
        raise ServiceValidationError(f"unknown zone {zone}; zones: {', '.join(rt.zones)}")
    return zone


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Actions, the websocket commands of the cards, and the cards themselves."""

    async def request(call: ServiceCall) -> None:
        rt = runtime_of(hass)
        await rt.async_set_request(zone_of(rt, call.data["zone"]), call.data["temperature"],
                                   parse_ready(call.data["ready_at"], dt_util.now()))

    async def cancel_request(call: ServiceCall) -> None:
        rt = runtime_of(hass)
        await rt.async_clear_request(zone_of(rt, call.data["zone"]))

    hass.services.async_register(DOMAIN, "request", request, vol.Schema({
        vol.Required("zone"): cv.string,
        vol.Required("temperature"): vol.All(vol.Coerce(float), vol.Range(min=5, max=23)),
        vol.Required("ready_at"): cv.string}))
    hass.services.async_register(DOMAIN, "cancel_request", cancel_request, vol.Schema({vol.Required("zone"): cv.string}))
    websocket.async_register(hass)
    if "frontend" in hass.config.components and hass.http is not None:
        from homeassistant.components.frontend import add_extra_js_url
        from homeassistant.components.http import StaticPathConfig
        version = (await async_get_integration(hass, DOMAIN)).version
        path = Path(__file__).parent / "frontend" / "smart-heat-cards.js"
        await hass.http.async_register_static_paths([StaticPathConfig(CARDS_URL, str(path), False)])
        add_extra_js_url(hass, f"{CARDS_URL}?v={version}")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SmartHeatConfigEntry) -> bool:
    """Load zones.yaml and the stored state; plan every minute, record every 15 min."""
    if CONF_ZONES_FILE in entry.data:
        zones_file = entry.data[CONF_ZONES_FILE]
    elif CONF_HEATING_DIR in entry.data:  # 0.1.0 entries stored the folder
        zones_file = f"{entry.data[CONF_HEATING_DIR]}/zones.yaml"
    else:
        zones_file = DEFAULT_ZONES_FILE
    rt = SmartHeat(hass, hass.config.path(zones_file))
    try:
        await rt.async_load()
    except SmartHeatError as err:
        raise ConfigEntryError(str(err)) from err
    entry.runtime_data = rt
    # the v4 B sensors of 0.4.0 and before are gone (decision 28)
    reg = er.async_get(hass)
    for e in er.async_entries_for_config_entry(reg, entry.entry_id):
        if e.unique_id.endswith("_learning") or e.unique_id.endswith("_learning_run"):
            reg.async_remove(e.entity_id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if rt.needs_seed():
        entry.async_create_background_task(hass, rt.async_seed(), "smart_heat seed warm-ups")

    async def _record(now=None) -> None:
        entry.async_create_background_task(hass, rt.async_record(), "smart_heat record warm-ups")

    entry.async_on_unload(rt.async_start_pump_watch())
    entry.async_on_unload(async_track_time_interval(hass, rt.async_plan, PLAN_INTERVAL))
    entry.async_on_unload(async_track_time_interval(hass, _record, RECORD_INTERVAL))
    entry.async_create_background_task(hass, rt.async_plan(), "smart_heat first plan")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SmartHeatConfigEntry) -> bool:
    await entry.runtime_data.store.async_save(entry.runtime_data.data)   # do not lose a delayed save
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
