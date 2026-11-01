"""Decision 28: the owner's equation, warm-ups as results, the schedule and the planner."""

import datetime as dt
from zoneinfo import ZoneInfo

import numpy as np

from custom_components.smart_heat.heatmodel import equation as E, schedule as S, warmups as W
from custom_components.smart_heat.heatmodel.config import EVENT_KEYS
from custom_components.smart_heat.heatmodel.data import ZoneData

TZ = ZoneInfo("Europe/Brussels")
P = {"dead_time": 30.0, "per_k": 20.0, "per_k_out": 0.5}


def test_minutes_halves_dead_time_on_a_warm_radiator_and_stops_at_the_tolerance() -> None:
    assert E.minutes(P, 16.0, 21.0, 8.0, False) == 30 + 100 + 6.5
    assert E.minutes(P, 16.0, 21.0, 8.0, True) == 15 + 100 + 6.5
    assert E.minutes(P, 20.8, 21.0, 8.0, False, tol=0.3) == 0.0       # already within 0.3 K
    assert E.minutes(P, 26.0, 21.0, 8.0, False) == 0.0                # warmer than the target


def test_working_range() -> None:
    assert E.out_of_range(16.0, 21.0, 8.0) == ""
    assert "outdoor -16.0" in E.out_of_range(16.0, 21.0, -16.0)
    assert "target 24.0" in E.out_of_range(16.0, 24.0, 8.0)
    assert "room 4.0" in E.out_of_range(4.0, 21.0, 8.0)


def _rows(p, n, t_end, rng, noise=0.0):
    out = []
    for k in range(n):
        start, tgt, outd, vo = rng.uniform(16, 20), rng.uniform(20, 22), rng.uniform(0, 15), bool(k % 4 == 0)
        out.append({"ts": t_end - (n - k) * 6 * 3600, "start_temp": start, "target": tgt, "outdoor": outd, "valve_open": vo,
                    "minutes": E.minutes(p, start, tgt, outd, vo) + rng.normal(0, noise)})
    return out


def _learn(st, rows):
    errs = []
    for r in rows:
        st, e = E.learn(st, r)
        errs.append(e)
    return st, errs


def test_each_warm_up_corrects_the_numbers_and_they_carry_over() -> None:
    rng = np.random.default_rng(1)
    true = {"dead_time": 10.0, "per_k": 32.0, "per_k_out": 2.0}                 # far from the starting numbers
    st, errs = _learn(E.new_state(), _rows(true, 40, 1e9, rng, noise=3.0))
    assert st["n"] == 40
    assert np.mean(np.abs(errs[-10:])) < np.mean(np.abs(errs[:10])) / 2       # the mistakes get smaller
    p = E.params(st)
    for k, tol in (("dead_time", 8.0), ("per_k", 3.0), ("per_k_out", 0.6)):
        assert abs(p[k] - true[k]) < tol, (k, p)
    r = _rows(true, 1, 2e9, rng)[0]                                            # the next warm-up starts from these
    st2, e = E.learn(st, r)
    assert abs(e) < 6 and st2["n"] == 41


def test_a_warm_up_moves_the_numbers_towards_its_own_result_within_the_limits() -> None:
    st = E.new_state()
    r = {"ts": 0.0, "start_temp": 17.0, "target": 20.0, "outdoor": 8.0, "valve_open": False, "minutes": 150.0}
    before = E.minutes(E.params(st), 17.0, 20.0, 8.0, False)
    st, e = E.learn(st, r)
    after = E.minutes(E.params(st), 17.0, 20.0, 8.0, False)
    assert e == before - 150.0 and before < after < 150.0                     # the same mistake is smaller now
    neg = {"dead_time": 25.0, "per_k": 18.0, "per_k_out": -2.0}               # colder faster: impossible
    st, _ = _learn(E.new_state(), _rows(neg, 40, 1e9, np.random.default_rng(3)))
    assert all(x >= lo for x, lo in zip(st["x"], E.LB))                          # within the limits after every step


def test_every_warm_up_counts_equally_however_old() -> None:
    rng = np.random.default_rng(2)
    true = {"dead_time": 20.0, "per_k": 25.0, "per_k_out": 1.0}
    rows = _rows(true, 30, 1e9, rng, noise=4.0)
    a, _ = _learn(E.new_state(), rows)
    b, _ = _learn(E.new_state(), [dict(r, ts=r["ts"] + k * 86400 * 30) for k, r in enumerate(rows[::-1])])  # other order, months apart
    for x, y in zip(a["x"], b["x"]):
        assert abs(x - y) < 1e-6
    assert np.all(np.diag(np.array(a["P"])) < np.diag(E.P0))                  # more certain with experience


def test_final_warmups_waits_30_min_after_reaching_and_marks_what_cannot_be_learned() -> None:
    T0 = dt.datetime(2026, 11, 2, 4, 0, tzinfo=TZ).timestamp()
    ev = dict.fromkeys(EVENT_KEYS, 1)
    ev.update(warm_step=0.5, warm_gap=0.4, target_window_min=30, reached_tol=0.3, warm_window_min=300, min_spacing_min=30)
    conf = {"house": {"outdoor": "sensor.out", "heating_pump": "sensor.pump"}, "data": {"start": "x", "burn_in_h": 1, "events": ev},
            "zones": {"z": {"comfort_floor": 19, "rooms": {"r": {"temperature": "sensor.t", "valve": "sensor.v", "climate": "climate.c"}}}}}
    start = T0 + 2 * 3600                                                 # 06:00: setpoint 17 -> 21
    temps = [(T0 - 60, "17.0", None)] + [(start + m * 60, f"{17 + m * 0.05:.2f}", None) for m in range(1, 80)]
    rows = {"sensor.t": temps, "sensor.v": [(T0 - 60, "0", None), (start, "100", None)],
            "climate.c": [(T0 - 60, "heat", {"temperature": 17.0}), (start, "heat", {"temperature": 21.0})],
            "sensor.out": [(T0 - 60, "8.0", None)], "sensor.pump": [(T0 - 60, "5", None)]}
    zd = lambda now: ZoneData(conf, "z", lambda e: rows.get(e, []), T0, now)
    reach = start + 74 * 60                                               # 17 + 74*0.05 = 20.7: within 0.3 K
    assert W.final_warmups(zd(reach + 29 * 60), TZ, reach + 29 * 60) == []
    (w,) = W.final_warmups(zd(reach + 31 * 60), TZ, reach + 31 * 60)
    assert w["minutes"] == 74 and w["target"] == 21.0 and w["start_temp"] == 17.0 and w["learn"]
    assert w["valve_open"] is False and w["start"] == "2026-11-02 06:00"
    assert W.final_warmups(zd(reach + 31 * 60), TZ, reach + 31 * 60, known=[start - 10 * 60]) == []   # same warm-up
    rows["sensor.out"] = [(T0 - 60, "-20.0", None)]
    (w,) = W.final_warmups(zd(reach + 31 * 60), TZ, reach + 31 * 60)
    assert not w["learn"] and w["reason"].startswith("outside the working range")
    assert W.learn_rows([w]) == []


SCHED = {"base": 16.0, "days": {d: [{"start": "07:00", "end": "22:30", "temperature": 20.0}] for d in S.DAYS}}


def _t(h, m=0, day=2):
    return dt.datetime(2026, 11, day, h, m, tzinfo=TZ)


def test_schedule_setpoint_and_next_rise() -> None:
    assert S.validate(SCHED) == []
    assert S.validate({"base": 16, "days": {"mon": [{"start": "09:00", "end": "08:00", "temperature": 20}]}})
    assert S.setpoint_at(SCHED, None, _t(6)) == 16.0 and S.setpoint_at(SCHED, None, _t(8)) == 20.0
    assert S.next_rise(SCHED, None, _t(5)) == (_t(7), 20.0, "schedule")
    assert S.next_rise(SCHED, None, _t(23)) == (_t(7, day=3), 20.0, "schedule")
    req = {"temperature": 21.5, "ready_at": _t(19).isoformat()}
    assert S.next_rise(SCHED, req, _t(12)) == (_t(19), 21.5, "request")
    assert S.setpoint_at(SCHED, req, _t(20)) == 21.5                      # holds until the next switch point
    assert not S.request_done(SCHED, req, _t(22)) and S.request_done(SCHED, req, _t(23))
    assert S.setpoint_at(SCHED, req, _t(23)) == 16.0


def test_plan_starts_when_now_plus_minutes_reaches_the_desired_time() -> None:
    need = E.minutes(P, 16.0, 20.0, 8.0, False, 0.3)                      # 30 + 80 + 6 = 116 min: start 05:04
    early = S.plan(SCHED, None, _t(4, 55), 16.0, 8.0, False, P, 0.3)
    assert not early["preheat"] and early["setpoint"] == 16.0 and early["minutes"] == need
    assert early["start_at"] == _t(7) - dt.timedelta(minutes=need)
    on = S.plan(SCHED, None, _t(5, 5), 16.0, 8.0, False, P, 0.3)
    assert on["preheat"] and on["setpoint"] == 20.0
    warm = S.plan(SCHED, None, _t(6, 50), 19.8, 8.0, False, P, 0.3)       # already within 0.3 K: nothing to do
    assert not warm["preheat"] and warm["setpoint"] == 16.0
    cold_out = S.plan(SCHED, None, _t(5, 5), 16.0, -20.0, False, P, 0.3)  # outside the working range
    assert not cold_out["preheat"] and cold_out["start_at"] == _t(7) and "working range" in cold_out["reason"]
    far = S.plan(SCHED, None, _t(3, 0), 8.0, 0.0, False, P, 0.3)          # 30 + 240 + 10 min: at most 4 h
    assert far["capped"] and far["preheat"] and far["start_at"] == _t(3)


def test_pump_fault_mask_uses_the_rules_of_the_two_automations() -> None:
    from custom_components.smart_heat.heatmodel.pump import fault_mask
    t = np.arange(0.0, 120 * 60, 60.0)
    power = [(0, "6.0", None), (10 * 60, "0.0", None), (19 * 60, "6.0", None),     # 9 min under 2 W: no fault
             (40 * 60, "unavailable", None), (52 * 60, "6.0", None)]               # 12 min unavailable: fault
    spread = [(0, "0.1", None), (70 * 60, "8.0", None), (74 * 60, "0.2", None),    # 4 min above 5 W: no fault
              (90 * 60, "9.0", None), (96 * 60, "0.3", None)]                       # 6 min: fault
    f = fault_mask(t, power, spread)
    assert not f[10:19].any() and f[40:52].all() and not f[70:74].any() and f[90:96].all()
    assert f.sum() == 12 + 6


def test_a_later_higher_target_that_needs_an_earlier_start_decides() -> None:
    req = {"temperature": 22.0, "ready_at": _t(7, 30).isoformat()}       # 20 at 07:00, 22 at 07:30
    p = S.plan(SCHED, req, _t(4, 50), 16.0, 8.0, False, P, 0.3)          # 22 needs 157 min: start 04:53
    assert p["source"] == "request" and p["start_at"] == _t(7, 30) - dt.timedelta(minutes=E.minutes(P, 16.0, 22.0, 8.0, False, 0.3))
    assert not p["preheat"]
    assert S.plan(SCHED, req, _t(4, 55), 16.0, 8.0, False, P, 0.3)["setpoint"] == 22.0
