"""Smart Heat sensors (step 3, read-only): per zone the last learned day; one sensor for the learning run."""

from __future__ import annotations

from datetime import date
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SmartHeatConfigEntry
from .const import DOMAIN, SIGNAL_UPDATED
from .runtime import SmartHeat


async def async_setup_entry(hass: HomeAssistant, entry: SmartHeatConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    rt = entry.runtime_data
    async_add_entities([LearningRunSensor(entry.entry_id, rt)] +
                       [ZoneLearningSensor(entry.entry_id, rt, z) for z in rt.zones])


class _Base(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, rt: SmartHeat) -> None:
        self.rt = rt

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self._updated))

    @callback
    def _updated(self) -> None:
        self.async_write_ha_state()


class LearningRunSensor(_Base):
    """Status of the last learning run: never / running / ok / error."""

    _attr_translation_key = "learning_run"

    def __init__(self, entry_id: str, rt: SmartHeat) -> None:
        super().__init__(rt)
        self._attr_unique_id = f"{entry_id}_learning_run"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry_id)}, name="Smart Heat")

    @property
    def native_value(self) -> str:
        return self.rt.run_state["status"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {k: v for k, v in self.rt.run_state.items() if k != "status"}


class ZoneLearningSensor(_Base):
    """Per zone: the last day the learning routine processed, with that fit and that day's warm-ups."""

    _attr_translation_key = "learning"
    _attr_device_class = SensorDeviceClass.DATE

    def __init__(self, entry_id: str, rt: SmartHeat, zone: str) -> None:
        super().__init__(rt)
        self.zone = zone
        self._attr_unique_id = f"{entry_id}_{zone}_learning"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, f"{entry_id}_{zone}")},
                                            name=f"Smart Heat {zone}", via_device=(DOMAIN, entry_id))

    @property
    def native_value(self) -> date | None:
        z = self.rt.zone_state.get(self.zone)
        return date.fromisoformat(z["date"]) if z else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        z = self.rt.zone_state.get(self.zone)
        if not z:
            return {}
        warm = [{k: e.get(k) for k in ("event", "setpoint", "pred_min", "meas_min", "err_min", "late", "interrupted")}
                for e in z["events"] if e.get("kind") == "warm"]
        cool = [{k: e.get(k) for k in ("event", "coast_min", "err_5h", "pred_end", "meas_end")}
                for e in z["events"] if e.get("kind") == "cool"]
        return {"rmse": z["rmse"], "jumps": z["jumps"], "params": z["params"], "warm_ups": warm, "cool_downs": cool}
