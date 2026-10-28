"""Warm-ups and cool-downs of the reference room, and how they are scored (zones.yaml data.events)."""
import numpy as np

first = lambda x, f: next((k for k, v in enumerate(x) if f(v)), None)


def find_events(zd, a, b):
    """Warm-ups and cool-downs of the reference room starting in minutes [a, b)."""
    EV, SP_REF, T_REF, A_m, BURN = zd.EV, zd.SP_REF, zd.T_REF, zd.A_m, zd.BURN
    warm, cool = [], []
    for i in range(max(a, BURN + 1), min(b, zd.n - 1)):
        if np.isnan(A_m[i - BURN]): continue
        if SP_REF[i] - SP_REF[i - 1] >= EV['warm_step'] and SP_REF[i] - T_REF[i] >= EV['warm_gap']:
            if not warm or i - warm[-1] > EV['min_spacing_min']: warm.append(i)
        if SP_REF[i - 1] - SP_REF[i] >= EV['cool_step'] and T_REF[i] - SP_REF[i] >= EV['cool_gap']:
            if not cool or i - cool[-1] > EV['min_spacing_min']: cool.append(i)
    return warm, cool


def warm_outcome(zd, i):
    """(target, window, measured minutes to target or None, interrupted). Interrupted: the setpoint dropped
    below the target before the reference room reached it, or the target fell back to the room temperature."""
    EV, SP_REF, T_REF = zd.EV, zd.SP_REF, zd.T_REF
    L = min(int(EV['warm_window_min']), zd.n - i)
    tgt = float(np.nanmin(SP_REF[i:i + int(EV['target_window_min'])]))
    mm = first(T_REF[i:i + L], lambda v: v >= tgt - EV['reached_tol'])
    upto = L if mm is None else mm + 1
    intr = tgt - T_REF[i] < EV['warm_gap'] or float(np.nanmin(SP_REF[i:i + upto])) < tgt - 1e-6
    return tgt, L, mm, bool(intr)


def warm_pred(zd, zone, tgt, off_):
    """Predicted minutes until the reference room (zone + its mean offset) reaches the target."""
    return first(zone + off_[zd.REF], lambda v: v >= tgt - zd.EV['reached_tol'])


def coast_len(zd, i):
    """Minutes until the setpoint rises again (coast_end_rise) or coast_window_min."""
    lim = min(zd.n, i + int(zd.EV['coast_window_min']))
    return next((j for j in range(i + 1, lim) if zd.SP_REF[j] > zd.SP_REF[i] + zd.EV['coast_end_rise']), lim) - i
