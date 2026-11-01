"""Smart Heat control switch per zone: off (default) = shadow, only logged; on = Smart Heat sets the setpoints."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SmartHeatConfigEntry
from .sensor import ZoneEntity, zone_device


async def async_setup_entry(hass: HomeAssistant, entry: SmartHeatConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    rt = entry.runtime_data
    async_add_entities([ControlSwitch(entry.entry_id, rt, z, zone_device(hass, entry.entry_id, z)) for z in rt.zones])


class ControlSwitch(ZoneEntity, SwitchEntity):

    def __init__(self, entry_id, rt, zone, dev) -> None:
        super().__init__(entry_id, rt, zone, dev, "control")

    @property
    def is_on(self) -> bool:
        return bool(self.rt.data["control"].get(self.zone))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.rt.async_set_control(self.zone, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.rt.async_set_control(self.zone, False)
