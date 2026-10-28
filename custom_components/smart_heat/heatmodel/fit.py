"""Thermal parameter fit over training segments, optionally weighted by age, with the numpy least squares of lsq.py."""
import numpy as np
from .lsq import lm
from .model import resid


def fit(zd, par, free, idx, M, lag=True, cutoff=None, half_life_days=None):
    """Least squares over the segments idx (measured zone temperature M). With cutoff and half_life_days,
    the weight of a segment halves every half_life_days before the cutoff (shadow mode)."""
    w = None
    if cutoff is not None and half_life_days:
        w = np.sqrt(0.5 ** ((cutoff - zd.t[np.asarray(idx) + M.shape[1] - 1]) / 86400.0 / half_life_days))[:, None]
    I, X0, LB, UB = par.I, par.X0, par.LB, par.UB
    mask = np.array([nm in free for nm in par.NAMES])
    def full(x):
        p = X0.copy(); p[mask] = x; p[I['c1']:I['c3'] + 1] = p[I['c0']]
        return p
    if w is None:
        fun = lambda x: resid(zd, par, full(x), idx, M, lag).ravel()
    else:
        fun = lambda x: (w * resid(zd, par, full(x), idx, M, lag)).ravel()
    best = None
    for cr0, h0 in ((10.0, 0.5), (40.0, 2.0)):              # two starting points; keep the better
        x0 = X0.copy(); x0[I['cr']] = cr0; x0[I['h']] = h0; x0[I['gam']] = 0.6 if 'gam' in free else 1.0
        x0 = np.clip(x0, LB + 1e-9, UB - 1e-9)
        r = lm(fun, x0[mask], LB[mask], UB[mask], diff_step=1e-3)
        if best is None or r.cost < best.cost: best = r
    return full(best.x)
