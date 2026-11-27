"""Smart Heat runtime (decision 28): the owner's equation per zone, learned after every warm-up; Smart Heat's
own schedule and one-off requests; the planner, every minute, which raises the setpoint early enough
for the room to be within reached_tol of the target at the desired time. A zone only switches when its
control is on; otherwise the planner runs in shadow and only logs what it would do."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
import logging
import os
import shutil
from typing import Any

import yaml

from homeassistant.components.recorder import get_instance, history
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import OUTPUT_DIR, SIGNAL_UPDATED
from .heatmodel import config as hm_config, equation as hm_eq, pump as hm_pump, schedule as hm_sched, warmups as hm_warm
from .heatmodel.data import PUMP_MIN_W, ZoneData
from .heatmodel.learn import start_ts

_LOGGER = logging.getLogger(__name__)
STORE_VERSION, STORE_KEY = 1, "smart_heat.state"
WINDOW_H = 8          # history read for recording: warm window (5 h) + 30 min + burn-in, with room to spare
BURN_MIN = 60         # minutes before a warm-up start that must be measured (instead of the model's 36 h)
MATCH_MIN = 10        # a recorded warm-up belongs to a pre-heat decision started within this many minutes
QCOLS = ['at', 'zone', 'warmup', 'n', 'dead_time', 'per_k', 'per_k_out', 'valve_saving']   # params.csv: every learned set
PCOLS = ['zone', 'ready_at', 'target', 'source', 'control', 'decided_at', 'start_temp', 'outdoor', 'valve_open',
         'pred_min', 'capped', 'warmup_start', 'minutes', 'reached_at', 'late_min', 'result']


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


def _read(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _append(path: str, cols: list[str], rows: list[dict]) -> None:
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, cols, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)


def _ensure_cols(path: str, cols: list[str]) -> None:
    """Rewrite a CSV log once when its header lacks columns added since (old rows kept, new fields empty)."""
    if not os.path.exists(path):
        return
    with open(path, newline="", encoding="utf-8") as fh:
        r = csv.DictReader(fh)
        if r.fieldnames == cols:
            return
        rows = list(r)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class SmartHeat:
    """One per config entry."""

    def __init__(self, hass: HomeAssistant, zones_file: str) -> None:
        self.hass = hass
        self.zones_file = zones_file
        self.out_dir = hass.config.path(OUTPUT_DIR)
        self.warm_file = os.path.join(self.out_dir, "warmups.csv")
        self.pred_file = os.path.join(self.out_dir, "predictions.csv")
        self.param_file = os.path.join(self.out_dir, "params.csv")
        self.conf: dict = {}
        self.zones: list[str] = []
        self.store: Store = Store(hass, STORE_VERSION, STORE_KEY)
        # learned: per zone what was learned so far (numbers and their uncertainty), carried from warm-up to warm-up;
        # params: per zone every learned set, newest last (version history, decision 23)
        self.data: dict[str, dict] = {"schedules": {}, "requests": {}, "control": {}, "pending": {}, "learned": {},
                                      "params": {}}
        self.params: dict[str, dict] = {}
        self.errors: dict[str, list[float]] = {}   # per zone the errors of the learned warm-ups, oldest first
        self.last_warmup: dict[str, dict] = {}
        self.plans: dict[str, dict] = {}
        self.applied: dict[str, tuple[str, float]] = {}   # per zone the pre-heat last sent: (ready_at, target)
        self.recorded: set[tuple[str, str]] = set()
        self.recorded_ts: list[tuple[str, float]] = []
        self.seeding = False
        self.bad_since: dict[str, datetime | None] = {"power": None, "spread": None}   # heating pump watch

    # ---- loading ------------------------------------------------------------------------------------
    async def async_load(self) -> None:
        """Validate zones.yaml, restore the schedule/requests/control, the learned numbers per zone."""
        self.conf = await self.hass.async_add_executor_job(load_zones, self.zones_file)
        self.zones = list(self.conf["zones"])
        stored = await self.store.async_load() or {}
        for k in self.data:
            self.data[k] = {z: v for z, v in (stored.get(k) or {}).items() if z in self.zones}
        await self.hass.async_add_executor_job(self._load_warmups)

    @property
    def tol(self) -> float:
        return float(self.conf["data"]["events"]["reached_tol"])

    def needs_seed(self) -> bool:
        return not os.path.exists(self.warm_file)

    def _load_warmups(self) -> None:
        """Archive the v4 B log once; read warmups.csv; each zone's learned state from the store (played through
        warmups.csv once if the store has none, or one of another version of the equation, but the file exists)."""
        os.makedirs(self.out_dir, exist_ok=True)
        if not os.path.exists(self.warm_file):    # before the first 0.5.0 run: the files are v4 B's log
            for name in ("predictions.csv", "params.csv"):
                f = os.path.join(self.out_dir, name)
                if os.path.exists(f):
                    shutil.move(f, os.path.join(self.out_dir, f"v4b_{name}"))
        _ensure_cols(self.param_file, QCOLS)
        rows = _read(self.warm_file)
        self.recorded = {(r["zone"], r["start"]) for r in rows}
        self.recorded_ts = [(r["zone"], float(r["ts"])) for r in rows]
        for z in self.zones:
            zr = [r for r in rows if r["zone"] == z]
            if not hm_eq.current(self.data["learned"].get(z)):
                again = z in self.data["learned"]
                st = hm_eq.new_state()
                for r in hm_warm.learn_rows(zr):
                    st, _ = hm_eq.learn(st, r)
                self.data["learned"][z] = st
                if st["n"]:
                    self._set_params(z, hm_eq.params(st), "re-learned from warmups.csv (new equation)" if again
                                     else "replayed from warmups.csv")
            self.params[z] = hm_eq.params(self.data["learned"][z])
            self.errors[z] = [float(r["err_min"]) for r in zr if r["learn"] in ("True", True) and r.get("err_min") not in (None, "")]
            if zr:
                self.last_warmup[z] = zr[-1]

    async def _history(self, since: datetime, start_state: bool) -> dict[str, list[tuple]]:
        ids = sorted({e for z in self.zones for e in hm_config.entities(self.conf, z)})
        # every row, also attribute-only changes (the setpoint is an attribute of the climate entity)
        states = await get_instance(self.hass).async_add_executor_job(
            history.get_significant_states, self.hass, since, None, ids, None, start_state, False, False, False, False
        )
        return await self.hass.async_add_executor_job(history_rows, states)

    # ---- recording: warm-ups as results, learning after each ---------------------------------------
    async def async_seed(self) -> None:
        """First run: record every warm-up since data.start from the recorder (decision 28)."""
        self.seeding = True
        try:
            tz = dt_util.get_default_time_zone()
            since = datetime.fromtimestamp(start_ts(self.conf, tz) - 86400, tz)
            rows = await self._history(since, False)
            await self.hass.async_add_executor_job(self._record, rows, start_ts(self.conf, tz), None)
        finally:
            self.seeding = False
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)

    async def async_record(self) -> None:
        """Every 15 min: record the warm-ups that became final, refit, close pre-heat decisions."""
        if self.seeding:
            return
        now = dt_util.now()
        t0 = (now - timedelta(hours=WINDOW_H)).replace(second=0, microsecond=0)
        rows = await self._history(t0, True)
        added = await self.hass.async_add_executor_job(self._record, rows, t0.timestamp(), BURN_MIN)
        if added:
            async_dispatcher_send(self.hass, SIGNAL_UPDATED)

    def _record(self, rows: dict[str, list[tuple]], t0: float, burn: int | None) -> int:
        tz = dt_util.get_default_time_zone()
        now = dt_util.now().timestamp()
        conf = self.conf if burn is None else dict(self.conf, data=dict(self.conf["data"], burn_in_h=burn / 60))
        hist = lambda e: rows.get(e, [])
        added = 0
        for z in self.zones:
            zd = ZoneData(conf, z, hist, t0, now)
            H = self.conf["house"]
            fault = hm_pump.fault_mask(zd.t, hist(H["heating_pump"]), hist(H["heating_pump_spread"]) if H.get("heating_pump_spread") else [])
            known = [k for zz, k in self.recorded_ts if zz == z]
            new = [r for r in hm_warm.final_warmups(zd, tz, now, known=known, fault=fault) if (z, r["start"]) not in self.recorded]
            for r in sorted(new, key=lambda r: r["ts"]):
                p = hm_eq.params(self.data["learned"].setdefault(z, hm_eq.new_state()))
                if r["minutes"] is not None:                 # the error of the current numbers on this warm-up
                    pred = hm_eq.minutes(p, r["start_temp"], r["target"], r["outdoor"], r["valve_open"])
                    r.update(pred_min=round(pred, 1), err_min=round(pred - r["minutes"], 1))
                _append(self.warm_file, hm_warm.COLS, [r])
                self.recorded.add((z, r["start"]))
                self.recorded_ts.append((z, r["ts"]))
                self.last_warmup[z] = r
                added += 1
                if r["learn"]:                                  # the error corrects the numbers; they carry over
                    st, err = hm_eq.learn(self.data["learned"][z], hm_warm.learn_rows([r])[0])
                    self.data["learned"][z] = st
                    self.errors.setdefault(z, []).append(round(err, 1))
                    self._set_params(z, hm_eq.params(st), r["start"])
                self._close_decision(z, r)
        return added

    def _set_params(self, zone: str, p: dict, warmup: str) -> None:
        """A newly learned set: in use at once, kept with its history in the store and copied to params.csv."""
        self.params[zone] = p
        row = dict(p, at=dt_util.now().isoformat(timespec="seconds"), zone=zone, warmup=warmup)
        self.data["params"].setdefault(zone, []).append({k: row[k] for k in QCOLS if k != "zone"})
        _append(self.param_file, QCOLS, [row])
        self.hass.loop.call_soon_threadsafe(self.store.async_delay_save, self._store_data, 1)

    def _close_decision(self, zone: str, w: dict) -> None:
        """Write the pre-heat decision this warm-up belongs to, with its result."""
        d = self.data["pending"].get(zone)
        if not d:
            return
        decided = datetime.fromisoformat(d["decided_at"]).timestamp()
        ready = datetime.fromisoformat(d["ready_at"]).timestamp()
        near = abs(w["ts"] - decided) <= MATCH_MIN * 60 if d["control"] else decided - 3600 <= w["ts"] <= ready + 1800
        if not near or abs(float(w["target"]) - float(d["target"])) > 0.05:
            return
        late = None
        if w["minutes"] is not None:
            late = round((w["ts"] + w["minutes"] * 60 - ready) / 60)
        result = ("shadow" if not d["control"] else "not reached" if w["minutes"] is None
                  else "late" if late > 0 else "on time")
        _append(self.pred_file, PCOLS, [dict(d, warmup_start=w["start"], minutes=w["minutes"] if w["minutes"] is not None else "",
                                             reached_at=w["reached_at"], late_min="" if late is None else late, result=result)])
        self.data["pending"].pop(zone, None)
        self.hass.loop.call_soon_threadsafe(self.store.async_delay_save, self._store_data, 1)

    def _store_data(self) -> dict:
        return self.data

    # ---- planning, every minute --------------------------------------------------------------------
    def _measured(self, zone: str) -> tuple[float, float, bool] | None:
        """Reference room temperature, outdoor temperature and valve open, from the current states."""
        H, Z = self.conf["house"], self.conf["zones"][zone]
        ref = Z.get("reference_room", next(iter(Z["rooms"])))
        st = lambda e: _num(self.hass.states.get(e).state) if e and self.hass.states.get(e) else None
        room = st(Z["rooms"][ref]["temperature"])
        out = st(H["outdoor"])
        if out is None:
            out = st(H.get("outdoor_internet"))
        valve = st(Z["rooms"][ref]["valve"])
        if room is None or out is None:
            return None
        return room, out, bool(valve is not None and valve / 100.0 >= hm_eq.VALVE_OPEN)

    # ---- heating pump watch (the rules of infra_pump_not_running / infra_pump_blocked) -------------------
    @callback
    def async_start_pump_watch(self) -> Any:
        """Track how long the pump has been bad; replan when a fault starts or ends. Returns the unsubscriber."""
        H = self.conf["house"]
        ids = {"power": H["heating_pump"], "spread": H.get("heating_pump_spread")}
        pending: dict[str, Any] = {}   # which -> cancel of its replan timer (at most one per signal)

        def bad(which: str, state: Any) -> bool:
            v = _num(state.state) if state is not None else None
            if which == "power":
                return v is None or v < PUMP_MIN_W
            return v is not None and v > hm_pump.SPREAD_MAX_W

        def schedule(which: str, hold: int) -> None:
            # a coroutine function, so HA runs it in the event loop (a plain lambda runs in a worker thread)
            async def fire(_now: datetime) -> None:
                pending.pop(which, None)
                await self.async_plan()
            pending[which] = async_call_later(self.hass, hold * 60 + 5, fire)

        @callback
        def update(which: str, state: Any) -> None:
            was = self.pump_fault()
            if bad(which, state):
                if self.bad_since[which] is None:
                    self.bad_since[which] = dt_util.now()
                    hold = hm_pump.POWER_FOR_MIN if which == "power" else hm_pump.SPREAD_FOR_MIN
                    schedule(which, hold)
            else:
                self.bad_since[which] = None
                if (cancel := pending.pop(which, None)) is not None:
                    cancel()   # recovered before the hold ran out: no fault, nothing to replan
                if was and not self.pump_fault():
                    self.hass.async_create_task(self.async_plan())

        for which, e in ids.items():
            if e:
                update(which, self.hass.states.get(e))

        @callback
        def changed(event: Event[EventStateChangedData]) -> None:
            for which, e in ids.items():
                if e == event.data["entity_id"]:
                    update(which, event.data["new_state"])

        unsub = async_track_state_change_event(self.hass, [e for e in ids.values() if e], changed)

        def stop() -> None:
            unsub()
            for cancel in pending.values():
                cancel()
            pending.clear()
        return stop

    def pump_fault(self) -> bool:
        now = dt_util.now()
        p, s = self.bad_since["power"], self.bad_since["spread"]
        return bool(p and now - p >= timedelta(minutes=hm_pump.POWER_FOR_MIN)
                    or s and now - s >= timedelta(minutes=hm_pump.SPREAD_FOR_MIN))

    async def async_plan(self, *_: Any) -> None:
        now = dt_util.now()
        changed = False
        if self.pump_fault():
            # no pre-heat starts anywhere and nothing is sent: Smart Heat only ever raises a setpoint
            for z in self.zones:
                control = bool(self.data["control"].get(z))
                d = self.data["pending"].pop(z, None)
                if d:
                    await self.hass.async_add_executor_job(_append, self.pred_file, PCOLS, [dict(d, result="pump fault")])
                    changed = True
                self.plans[z] = {"preheat": False, "ready_at": None, "setpoint": None, "control": control,
                                 "reason": "heating pump fault: no pre-heat"}
            if changed:
                self.store.async_delay_save(self._store_data, 1)
            async_dispatcher_send(self.hass, SIGNAL_UPDATED)
            return
        for z in self.zones:
            sched = self.data["schedules"].get(z)
            req = self.data["requests"].get(z)
            control = bool(self.data["control"].get(z))
            if req and sched and hm_sched.request_done(sched, req, now):
                self.data["requests"].pop(z); req = None; changed = True
            if req and not sched and datetime.fromisoformat(req["ready_at"]) + timedelta(hours=12) < now:
                self.data["requests"].pop(z); req = None; changed = True
            s = sched or {"base": None, "days": {}}
            m = self._measured(z)
            if m is None:
                self.plans[z] = {"preheat": False, "ready_at": None, "reason": "no room or outdoor temperature",
                                 "control": control}
                continue
            room, out, vopen = m
            if sched is None and not req:
                self.plans[z] = {"preheat": False, "ready_at": None, "reason": "no schedule", "control": control}
                continue
            if sched is None:
                s = {"base": room, "days": {}}       # a request without a schedule: hold whatever is set
            p = hm_sched.plan(s, req, now, room, out, vopen, self.params.get(z) or hm_eq.DEFAULT, self.tol)
            d = self.data["pending"].get(z)
            if d and datetime.fromisoformat(d["ready_at"]) > now and p.get("ready_at") and d["ready_at"] == p["ready_at"].isoformat():
                p.update(setpoint=float(d["target"]), preheat=True, start_at=datetime.fromisoformat(d["decided_at"]))
            elif p["preheat"] and (not d or datetime.fromisoformat(d["ready_at"]) <= now):
                if d:                             # the previous decision's warm-up was never recorded
                    await self.hass.async_add_executor_job(
                        _append, self.pred_file, PCOLS, [dict(d, result="no warm-up recorded")])
                self.data["pending"][z] = d = {
                    "zone": z, "ready_at": p["ready_at"].isoformat(), "target": p["target"], "source": p["source"],
                    "control": control, "decided_at": now.isoformat(), "start_temp": room, "outdoor": out,
                    "valve_open": vopen, "pred_min": p["minutes"], "capped": p["capped"]}
                changed = True
            if d and datetime.fromisoformat(d["ready_at"]) + timedelta(minutes=self.conf["data"]["events"]["warm_window_min"] + 30) < now:
                await self.hass.async_add_executor_job(
                    _append, self.pred_file, PCOLS, [dict(d, result="no warm-up recorded")])
                self.data["pending"].pop(z); changed = True
            p["control"] = control
            self.plans[z] = p
            if control:
                await self._apply(z, p)
        if changed:
            self.store.async_delay_save(self._store_data, 1)
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)

    async def _apply(self, zone: str, p: dict) -> None:
        """Send a due pre-heat's target to the zone's thermostats, once per pre-heat (a manual change after it
        stands). Nothing else is ever sent: Smart Heat only raises a setpoint, at a pre-heat start."""
        if not p.get("preheat") or p.get("setpoint") is None or not p.get("ready_at"):
            return
        key = (p["ready_at"].isoformat(), float(p["setpoint"]))
        if self.applied.get(zone) == key:
            return
        ids = [rc["climate"] for rc in self.conf["zones"][zone]["rooms"].values()]
        _LOGGER.info("%s: pre-heat, setpoint %.1f on %s", zone, p["setpoint"], ", ".join(ids))
        await self.hass.services.async_call("climate", "set_temperature",
                                            {"entity_id": ids, "temperature": p["setpoint"]}, blocking=True)
        self.applied[zone] = key

    # ---- changes from the cards, services and switches ----------------------------------------------
    async def async_set_control(self, zone: str, on: bool) -> None:
        self.data["control"][zone] = on
        self.applied.pop(zone, None)              # on: a pre-heat already due is sent at once
        if zone in self.data["pending"]:
            self.data["pending"][zone]["control"] = on
        self.store.async_delay_save(self._store_data, 1)
        await self.async_plan()

    async def async_set_schedule(self, zone: str, sched: dict) -> None:
        problems = hm_sched.validate(sched)
        if problems:
            raise ValueError("; ".join(problems))
        self.data["schedules"][zone] = {"base": float(sched["base"]),
                                        "days": {d: sorted(sched.get("days", {}).get(d, []), key=lambda p: p["start"])
                                                 for d in hm_sched.DAYS}}
        self.data["pending"].pop(zone, None)
        self.store.async_delay_save(self._store_data, 1)
        await self.async_plan()

    async def async_set_request(self, zone: str, temperature: float, ready_at: datetime) -> None:
        self.data["requests"][zone] = {"temperature": float(temperature), "ready_at": ready_at.isoformat()}
        self.data["pending"].pop(zone, None)
        self.store.async_delay_save(self._store_data, 1)
        await self.async_plan()

    async def async_clear_request(self, zone: str) -> None:
        self.data["requests"].pop(zone, None)
        self.data["pending"].pop(zone, None)
        self.store.async_delay_save(self._store_data, 1)
        await self.async_plan()

    def zone_info(self, zone: str) -> dict:
        """Everything the cards show for a zone."""
        Z = self.conf["zones"][zone]
        p = dict(self.plans.get(zone) or {})
        for k in ("ready_at", "start_at"):
            if isinstance(p.get(k), datetime):
                p[k] = p[k].isoformat()
        return {"zone": zone, "reference_room": Z.get("reference_room", next(iter(Z["rooms"]))),
                "rooms": [{"room": r, "climate": rc["climate"], "temperature": rc["temperature"]} for r, rc in Z["rooms"].items()],
                "control": bool(self.data["control"].get(zone)), "schedule": self.data["schedules"].get(zone),
                "request": self.data["requests"].get(zone), "plan": p, "params": self.params.get(zone),
                "last_warmup": self.last_warmup.get(zone), "tolerance": self.tol}
