"""Heating pump fault (owner, 2026-11-01), with the rules of the automations infra_pump_not_running and
infra_pump_blocked: power below 2 W or unavailable for 10 min, or the power spread above 5 W for 5 min.
During a fault no warm-up is attempted, and a warm-up that saw one is not learned from."""
import numpy as np
from .data import PUMP_MIN_W, grid, points

POWER_FOR_MIN = 10
SPREAD_MAX_W, SPREAD_FOR_MIN = 5.0, 5


def _runs(bad, k):
    """True on every minute of a run of at least k True minutes."""
    out = np.zeros(len(bad), bool); i = 0; n = len(bad)
    while i < n:
        if bad[i]:
            j = i
            while j < n and bad[j]: j += 1
            if j - i >= k: out[i:j] = True
            i = j
        else:
            i += 1
    return out


def fault_mask(t, power_rows, spread_rows=()):
    """Per minute of the grid t: was the pump in fault."""
    p = grid(points(power_rows, unavailable=True), t)
    s = grid(points(spread_rows), t)
    return _runs(np.isnan(p) | (p < PUMP_MIN_W), POWER_FOR_MIN) | _runs(np.nan_to_num(s) > SPREAD_MAX_W, SPREAD_FOR_MIN)
