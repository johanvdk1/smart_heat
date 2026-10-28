"""Smart Heat runtime: loads the house description (zones.yaml) and runs the learning routine
(heatmodel.learn) in HA with history from the recorder (decisions 22, 23, step 3).
Read-only: nothing here switches anything."""

from __future__ import annotations

import asyncio
import csv
from datetime import date, datetime, timedelta
import json
import logging
import os
import time
from typing import Any

import yaml

from homeassistant.components.recorder import get_instance, history
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import OUTPUT_DIR, SIGNAL_UPDATED
from .heatmodel import config as hm_config, learn as hm_learn

_LOGGER = logging.getLogger(__name__)


class SmartHeatError(Exception):
    """zones.yaml is not usable."""


def load_zones(path: str) -> dict:
    """Read and validate zones.yaml (blocking)."""
    try:
        with open(path, encoding="utf-8") as fh:
            conf = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError) as err:
        raise SmartHeatError(f"cannot read {path}: {err}") from err
    problems = hm_config.validate(conf)
    if problems:
        raise SmartHeatError(f"{path}: " + "; ".join(problems))
    return conf


def history_rows(states: dict[str, list]) -> dict[str, list[tuple]]:
    """Recorder states -> heatmodel history rows (ts, state, attributes), oldest first."""
    return {
        e: [(s.last_updated_timestamp, s.state, s.attributes) for s in lst]
        for e, lst in states.items()
    }


class SmartHeat:
    """One per config entry."""

    def __init__(self, hass: HomeAssistant, zones_file: str) -> None:
        self.hass = hass
        self.zones_file = zones_file
        self.out_dir = hass.config.path(OUTPUT_DIR)
        self.zones: list[str] = []
        self.zone_state: dict[str, dict] = {}  # last processed day per zone (for the sensors)
        self.run_state: dict[str, Any] = {"status": "never"}
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        """Validate zones.yaml, restore the last results from the learning log."""
        conf = await self.hass.async_add_executor_job(load_zones, self.zones_file)
        self.zones = list(conf["zones"])
        self.zone_state = await self.hass.async_add_executor_job(self._read_last)

    def _read_last(self) -> dict[str, dict]:
        """Last fit and that day's events per zone, from the CSV log."""
        out: dict[str, dict] = {}
        pf, ef = os.path.join(self.out_dir, "params.csv"), os.path.join(self.out_dir, "predictions.csv")
        if os.path.exists(pf):
            with open(pf, newline="", encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    out[r["zone"]] = {"date": r["fit_until"], "rmse": float(r["rmse"]),
                                      "params": json.loads(r["params"]),
                                      "jumps": r["jumps"].split() if r["jumps"] else [], "events": []}
        if os.path.exists(ef):
            with open(ef, newline="", encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    z = out.get(r["zone"])
                    if z and r["date"] == z["date"]:
                        z["events"].append(r)
        return out

    async def async_run(self, start_day: date | None = None) -> None:
        """One learning run over every zone: process each finished day not yet in the log."""
        if self._lock.locked():
            _LOGGER.info("learning run already in progress")
            return
        async with self._lock:
            t0 = time.monotonic()
            self.run_state = {"status": "running", "started": dt_util.now().isoformat()}
            async_dispatcher_send(self.hass, SIGNAL_UPDATED)
            try:
                days = await self._run(start_day)
                self.run_state = {"status": "ok", "last_run": dt_util.now().isoformat(),
                                  "duration_s": round(time.monotonic() - t0), "days_processed": days}
            except Exception as err:  # report, never break HA
                _LOGGER.exception("learning run failed")
                self.run_state = {"status": "error", "last_run": dt_util.now().isoformat(),
                                  "duration_s": round(time.monotonic() - t0), "error": str(err)}
            async_dispatcher_send(self.hass, SIGNAL_UPDATED)

    async def _run(self, start_day: date | None) -> int:
        hass = self.hass
        conf = await hass.async_add_executor_job(load_zones, self.zones_file)  # re-read: edits apply
        self.zones = list(conf["zones"])
        tz = dt_util.get_default_time_zone()
        now = dt_util.now()
        since = datetime.fromtimestamp(hm_learn.start_ts(conf, tz) - 86400, tz)
        ids = sorted({e for z in self.zones for e in hm_config.entities(conf, z)})
        # every row, also attribute-only changes (the setpoint is an attribute of the climate entity):
        # significant_changes_only=False; no start-time state, as the recorder query on the Mac
        states = await get_instance(hass).async_add_executor_job(
            history.get_significant_states, hass, since, None, ids, None, False, False, False, False, False
        )
        rows = await hass.async_add_executor_job(history_rows, states)
        hist = lambda e: rows.get(e, [])
        days = 0
        for zone in self.zones:
            res = await hass.async_add_executor_job(
                hm_learn.learn_zone, conf, zone, hist, self.out_dir, tz, now, start_day,
                lambda s: _LOGGER.info("%s", s),
            )
            days += len(res)
            if res:
                last = res[-1]
                self.zone_state[zone] = {"date": last["date"], "rmse": last["rmse"], "params": last["params"],
                                         "jumps": last["jumps"], "events": last["events"]}
                async_dispatcher_send(hass, SIGNAL_UPDATED)
        return days
