"""Controller model per room: how the Homematic head sets its valve.
    u = clip(b0 + b1*(setpoint - room) + b2*(room rise per hour), 0, 1)"""
import numpy as np
from .lsq import lm


def ctrl_fit(zd, mask_t):
    """Fit the controller of every room on the minutes in mask_t (setpoint known, door closed, valve known).
    Returns (C, off_): coefficients per room and each room's mean offset from the zone temperature."""
    C, off_ = {}, {}
    n = zd.n
    for r in zd.rooms:
        Tr = zd.room_T[r]; rise = np.full(n, np.nan); rise[10:] = (Tr[10:] - Tr[:-10]) * 6.0
        e = zd.SPr[r] - Tr
        ok = ~np.isnan(e) & ~np.isnan(rise) & (zd.SPr[r] > 10) & mask_t & ~zd.DOOR[r] & ~zd.NO_VALVE[r]
        pred = lambda b: np.clip(b[0] + b[1] * e[ok] + b[2] * rise[ok], 0, 1)
        C[r] = lm(lambda b: pred(b) - zd.V[r][ok], np.array([0.1, 0.5, 0.0]), diff_step=1.49e-8).x  # scipy default step
        off_[r] = float(np.nanmean((zd.room_T[r] - zd.A_m)[mask_t]))
    return C, off_
