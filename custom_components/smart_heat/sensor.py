"""Smart Heat sensors per zone: the next pre-heat start, and the learned equation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SmartHeatConfigEntry
from .const import DOMAIN, SIGNAL_UPDATED
from .runtime import SmartHeat


def zone_device(hass: HomeAssistant, entry_id: str, zone: str) -> DeviceInfo:
    # the main device first: the zone devices refer to it by its registry id
    main = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry_id, identifiers={(DOMAIN, entry_id)}, name="Smart Heat", entry_type=DeviceEntryType.SERVICE)
    return DeviceInfo(identifiers={(DOMAIN, f"{entry_id}_{zone}")}, name=f"Smart Heat {zone}",
                      entry_type=DeviceEntryType.SERVICE, via_device_id=main.id)


async def async_setup_entry(hass: HomeAssistant, entry: SmartHeatConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    rt = entry.runtime_data
    ents: list[SensorEntity] = []
    for z in rt.zones:
        dev = zone_device(hass, entry.entry_id, z)
        ents += [NextStartSensor(entry.entry_id, rt, z, dev), EquationSensor(entry.entry_id, rt, z, dev)]
    async_add_entities(ents)


class ZoneEntity:
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry_id: str, rt: SmartHeat, zone: str, dev: DeviceInfo, key: str) -> None:
        self.rt, self.zone = rt, zone
        self._attr_unique_id = f"{entry_id}_{zone}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = dev

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self._updated))

    @callback
    def _updated(self) -> None:
        self.async_write_ha_state()


class NextStartSensor(ZoneEntity, SensorEntity):
    """When the setpoint goes up for the coming rise of the schedule or a request (empty: none within 24 h)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, entry_id: str, rt: SmartHeat, zone: str, dev: DeviceInfo) -> None:
        super().__init__(entry_id, rt, zone, dev, "next_start")

    @property
    def native_value(self) -> datetime | None:
        return (self.rt.plans.get(self.zone) or {}).get("start_at")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        p = self.rt.plans.get(self.zone) or {}
        return {"ready_at": p.get("ready_at"), "target": p.get("target"), "source": p.get("source"),
                "minutes": p.get("minutes"), "capped": p.get("capped"), "preheat": p.get("preheat"),
                "setpoint": p.get("setpoint"), "reason": p.get("reason"), "control": p.get("control")}


class EquationSensor(ZoneEntity, SensorEntity):
    """The zone's learned numbers; the state is the number of warm-ups learned from. The running error shows
    whether the mistakes get smaller: mean absolute error of the last 10 learned warm-ups, and of the 10 before."""

    _attr_native_unit_of_measurement = "warm-ups"

    def __init__(self, entry_id: str, rt: SmartHeat, zone: str, dev: DeviceInfo) -> None:
        super().__init__(entry_id, rt, zone, dev, "equation")

    @property
    def native_value(self) -> int | None:
        p = self.rt.params.get(self.zone)
        return p["n"] if p else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        p = self.rt.params.get(self.zone) or {}
        w = self.rt.last_warmup.get(self.zone) or {}
        e = [abs(v) for v in self.rt.errors.get(self.zone, [])]
        mean = lambda x: round(sum(x) / len(x), 1) if x else None
        return {"dead_time_min": p.get("dead_time"), "min_per_k": p.get("per_k"), "min_per_k_outside": p.get("per_k_out"),
                "error_last_10_min": mean(e[-10:]), "error_10_before_min": mean(e[-20:-10]),
                "seeding": self.rt.seeding,
                "last_warmup": {k: w.get(k) for k in ("start", "target", "start_temp", "outdoor", "minutes", "pred_min",
                                                      "err_min", "learn", "reason")} if w else None}
