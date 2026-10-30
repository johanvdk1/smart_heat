"""heatmodel inputs: the internet outdoor estimate while the outdoor sensor is unavailable, and no heat
from the valves while the heating pump draws no power (learning and planner prediction)."""

import numpy as np

from custom_components.smart_heat.heatmodel.config import EVENT_KEYS, entities, validate
from custom_components.smart_heat.heatmodel.data import ZoneData
from custom_components.smart_heat.heatmodel.model import Params, forward

T0 = 1_760_000_000.0
DAY = 86400
CONF = {
    "house": {"outdoor": "sensor.out", "outdoor_internet": "sensor.out_net", "heating_pump": "sensor.pump_w"},
    "data": {"start": "2026-10-09 08:00", "burn_in_h": 2, "events": dict.fromkeys(EVENT_KEYS, 1)},
    "zones": {"z": {"comfort_floor": 19, "worst_late_min": 0, "rooms": {"r": {
        "temperature": "sensor.t", "valve": "sensor.v", "climate": "climate.c"}}}},
}


def _zone(rows):
    base = {"sensor.t": [(T0 - 60, "19.0", None)], "sensor.v": [(T0 - 60, "50", None)],
            "climate.c": [(T0 - 60, "heat", {"temperature": 21.0})]}
    return ZoneData(CONF, "z", lambda e: {**base, **rows}.get(e, []), T0, T0 + DAY)


async def test_heating_pump_and_internet_outdoor_are_required_inputs() -> None:
    assert validate(CONF) == []
    assert {"sensor.pump_w", "sensor.out", "sensor.out_net"} <= set(entities(CONF, "z"))
    bad = {**CONF, "house": {"outdoor": "sensor.out"}}
    assert "house.heating_pump missing" in validate(bad)


async def test_outdoor_falls_back_to_internet_while_unavailable() -> None:
    zd = _zone({
        "sensor.out": [(T0 - 60, "5.0", None), (T0 + 3600, "unavailable", None), (T0 + 7200, "6.0", None)],
        "sensor.out_net": [(T0 - 60, "3.0", None)],
        "sensor.pump_w": [(T0 - 60, "5", None)],
    })
    assert zd.To[0] == 5.0 and zd.To[59] == 5.0
    assert (zd.To[60:120] == 3.0).all()               # sensor unavailable: internet estimate
    assert zd.To[120] == 6.0                          # sensor back


async def test_no_heat_while_heating_pump_has_no_power() -> None:
    zd = _zone({
        "sensor.out": [(T0 - 60, "5.0", None)],
        "sensor.pump_w": [(T0 - 60, "6.5", None), (T0 + 600, "0.0", None), (T0 + 1200, "4.1", None)],
    })
    assert (zd.V["r"] == 0.5).all()                   # the head keeps its valve: controller model input
    assert (zd.U["r"][10:20] == 0).all()              # no hot water: no heat in the heat model
    assert (zd.U["r"][:10] == 0.5).all() and (zd.U["r"][20:] == 0.5).all()   # speed / power do not matter

    par = Params(zd.rooms)
    P = par.as_dict(par.X0)
    C, off_ = {"r": np.array([0.5, 0.0, 0.0])}, {"r": 0.0}
    i = zd.BURN + 10
    warm = forward(zd, P, i, 120, C, off_, "held")
    zd.HOT[i:i + 60] = False                          # pump stops during the prediction
    cold = forward(zd, P, i, 120, C, off_, "held")
    assert cold[-1] < warm[-1] - 0.5


async def test_lost_valve_or_temperature_is_no_measurement_beyond_the_bridge() -> None:
    zd = _zone({
        "sensor.out": [(T0 - 60, "5.0", None)],
        "sensor.pump_w": [(T0 - 60, "5", None)],
        "sensor.v": [(T0 - 60, "50", None), (T0 + 600, "unavailable", None), (T0 + 1800, "40", None),
                     (T0 + 7200, "unavailable", None), (T0 + 14400, "30", None)],
        "sensor.t": [(T0 - 60, "19.0", None)],         # no change for a day: still a measurement
    })
    assert not np.isnan(zd.A_m[: 120]).any()          # 20 min lost (a restart): bridged
    assert zd.V["r"][15] == 0.5                       # bridged with the value before
    assert np.isnan(zd.A_m[120:240]).all()            # 2 h lost: no measurement
    assert (zd.V["r"][120:240] == 0.4).all()          # model input: last value held
    assert zd.NO_VALVE["r"][120:240].all() and not zd.NO_VALVE["r"][:120].any()
    assert zd.A_m[-1] == 19.0                         # the 3 h cut-off on a stable temperature is gone


def _week(k):
    """Seven days, valves closed, room steady at 19 with 5 outside; parameters with loss rate k."""
    rows = {"sensor.t": [(T0 - 60, "19.0", None)], "sensor.v": [(T0 - 60, "0", None)],
            "climate.c": [(T0 - 60, "heat", {"temperature": 19.0})],
            "sensor.out": [(T0 - 60, "5.0", None)], "sensor.pump_w": [(T0 - 60, "5", None)]}
    zd = ZoneData(CONF, "z", lambda e: rows.get(e, []), T0, T0 + 7 * DAY)
    par = Params(zd.rooms); p = par.X0.copy(); p[par.I["k"]] = k; p[par.I["h"]] = 0.5
    return zd, par, p


async def test_check_fit_night_cooling_and_bounds() -> None:
    from datetime import timezone
    from custom_components.smart_heat.heatmodel.checks import check_fit
    utc = timezone.utc
    zd, par, p = _week(0.2)                                     # model cools fast, the room did not
    chk = check_fit(zd, par, p, T0 + 7 * DAY, utc)
    assert chk["night"]["nights"] >= 5 and chk["night"]["median_k"] < -0.5
    assert chk["status"] == "unreliable" and chk["reasons"][0].startswith("night cooling off")
    zd, par, p = _week(0.002)                                   # plausible: almost no loss, as measured
    chk = check_fit(zd, par, p, T0 + 7 * DAY, utc)
    assert chk["status"] == "ok" and abs(chk["night"]["median_k"]) < 0.5
    chk = check_fit(zd, par, p, T0 + 2 * DAY, utc)              # too few nights: not judged
    assert chk["status"] == "ok" and chk["night"]["nights"] < 5
    p[par.I["k"]] = 0.0; p[par.I["h"]] = 0.0
    chk = check_fit(zd, par, p, T0 + 2 * DAY, utc)
    assert chk["status"] == "unreliable" and "k=0 (no loss to outside)" in chk["reasons"]
    assert chk["warnings"] == ["h=0 (no wall node, cr unidentified)"]


async def test_outside_experience() -> None:
    from custom_components.smart_heat.heatmodel.checks import outside
    exp = {"warm_ups": 12, "outdoor_min": 6.5, "gap_max": 3.2}
    assert outside(exp, 8.0, 2.0) == ""
    assert outside(exp, 2.0, 2.0) == "colder"
    assert outside(exp, 2.0, 4.0) == "colder larger gap"
    assert outside({"warm_ups": 0}, 8.0, 1.0) == "no warm-ups"


async def test_log_gets_new_column_without_losing_rows(tmp_path) -> None:
    from custom_components.smart_heat.heatmodel.learn import _append, _read
    f = str(tmp_path / "params.csv")
    _append(f, ["zone", "rmse"], [{"zone": "a", "rmse": "0.3"}])
    _append(f, ["zone", "rmse", "checks"], [{"zone": "b", "rmse": "0.4", "checks": "{}"}])
    rows = _read(f)
    assert [r["zone"] for r in rows] == ["a", "b"] and rows[0]["checks"] == "" and rows[1]["checks"] == "{}"
    assert open(f).readline().strip() == "zone,rmse,checks"
