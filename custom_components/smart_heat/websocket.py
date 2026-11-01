"""Websocket commands for the Smart Heat cards."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL_UPDATED


def _rt(hass: HomeAssistant):
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    return entries[0].runtime_data if entries else None


def _zone(conn, msg, rt) -> bool:
    if rt is None:
        conn.send_error(msg["id"], "not_loaded", "Smart Heat is not loaded")
        return False
    if msg["zone"] not in rt.zones:
        conn.send_error(msg["id"], "unknown_zone", f"unknown zone {msg['zone']}")
        return False
    return True


@callback
def async_register(hass: HomeAssistant) -> None:
    for cmd in (ws_zones, ws_subscribe, ws_schedule_set, ws_request_set, ws_request_clear, ws_control_set):
        websocket_api.async_register_command(hass, cmd)


@websocket_api.websocket_command({vol.Required("type"): "smart_heat/zones"})
@callback
def ws_zones(hass: HomeAssistant, conn: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    rt = _rt(hass)
    conn.send_result(msg["id"], {"zones": [rt.zone_info(z) for z in rt.zones] if rt else []})


@websocket_api.websocket_command({vol.Required("type"): "smart_heat/subscribe"})
@callback
def ws_subscribe(hass: HomeAssistant, conn: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Push the zones to the card whenever something changes."""
    @callback
    def push() -> None:
        rt = _rt(hass)
        conn.send_message(websocket_api.event_message(msg["id"], {"zones": [rt.zone_info(z) for z in rt.zones] if rt else []}))
    conn.subscriptions[msg["id"]] = async_dispatcher_connect(hass, SIGNAL_UPDATED, push)
    conn.send_result(msg["id"])
    push()


@websocket_api.websocket_command({vol.Required("type"): "smart_heat/schedule/set", vol.Required("zone"): str,
                                  vol.Required("schedule"): dict})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_schedule_set(hass: HomeAssistant, conn: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    rt = _rt(hass)
    if not _zone(conn, msg, rt):
        return
    try:
        await rt.async_set_schedule(msg["zone"], msg["schedule"])
    except (ValueError, KeyError, TypeError) as err:
        conn.send_error(msg["id"], "invalid_schedule", str(err))
        return
    conn.send_result(msg["id"], rt.zone_info(msg["zone"]))


@websocket_api.websocket_command({vol.Required("type"): "smart_heat/request/set", vol.Required("zone"): str,
                                  vol.Required("temperature"): vol.All(vol.Coerce(float), vol.Range(min=5, max=23)),
                                  vol.Required("ready_at"): str})
@websocket_api.async_response
async def ws_request_set(hass: HomeAssistant, conn: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    from . import parse_ready
    rt = _rt(hass)
    if not _zone(conn, msg, rt):
        return
    try:
        at = parse_ready(msg["ready_at"], dt_util.now())
    except Exception as err:  # ServiceValidationError
        conn.send_error(msg["id"], "invalid_time", str(err))
        return
    await rt.async_set_request(msg["zone"], msg["temperature"], at)
    conn.send_result(msg["id"], rt.zone_info(msg["zone"]))


@websocket_api.websocket_command({vol.Required("type"): "smart_heat/request/clear", vol.Required("zone"): str})
@websocket_api.async_response
async def ws_request_clear(hass: HomeAssistant, conn: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    rt = _rt(hass)
    if not _zone(conn, msg, rt):
        return
    await rt.async_clear_request(msg["zone"])
    conn.send_result(msg["id"], rt.zone_info(msg["zone"]))


@websocket_api.websocket_command({vol.Required("type"): "smart_heat/control/set", vol.Required("zone"): str,
                                  vol.Required("on"): bool})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_control_set(hass: HomeAssistant, conn: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    rt = _rt(hass)
    if not _zone(conn, msg, rt):
        return
    await rt.async_set_control(msg["zone"], msg["on"])
    conn.send_result(msg["id"], rt.zone_info(msg["zone"]))
