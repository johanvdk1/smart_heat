"""Plausibility of a fit and the range of experience behind it (AUTO_HEAT_DESIGN.md, decision 27).

Unreliable (at switching time the zone falls back to a fixed lead time):
  - a parameter on a bound that removes physics: no loss to outside (k = 0) or no heat from a radiator (a_r = 0);
  - night cooling: on nights with the valves closed (00:30-04:30) the fitted model, replayed on the measured inputs,
    ends more than NIGHT_MAX_ERR_K away from the measured zone temperature (median over at least NIGHT_MIN nights;
    fewer nights: not judged).
Warning only (the model is simpler than designed, its predictions can still be fine):
  - h = 0: no wall node (cr is then unidentified); tau_rad on its lower bound: no radiator lag.
  gam = 1 is the design bound (decision 15) and is not reported.
Experience: the coldest outdoor temperature and the largest gap of the warm-ups trained on; a prediction outside that
range is an extrapolation (the model has no saturation of the radiator output, so it is probably too optimistic there).
"""
import datetime as dt
import numpy as np
from .model import simulate
from .events import find_events, warm_outcome

NIGHT_START = dt.time(0, 30)
NIGHT_MIN_LEN = 240                 # minutes
NIGHT_VALVE_MAX = 0.02              # all valves of the zone at most this open, from 1 h before the window
NIGHT_MIN = 5                       # nights needed to judge
NIGHT_MAX_ERR_K = 0.5


def _on(par, name, v, side):
    lo, hi = par.LB[par.I[name]], par.UB[par.I[name]]
    return abs(v - (lo if side == 'lo' else hi)) <= 1e-3 * (hi - lo)


def night_windows(zd, cut, tz):
    """Start minutes of the closed-valve night windows that end before cut (unix seconds)."""
    t = zd.t; U = sum(zd.U[r] for r in zd.rooms); out = []
    day = dt.datetime.fromtimestamp(t[0], tz).date()
    while True:
        s = dt.datetime.combine(day, NIGHT_START, tzinfo=tz).timestamp()
        if s + NIGHT_MIN_LEN * 60 > cut: break
        i = int((s - t[0]) // 60)
        if i - zd.BURN >= 0 and i - 60 >= 0 and i + NIGHT_MIN_LEN < zd.n \
                and U[i - 60:i + NIGHT_MIN_LEN].max() <= NIGHT_VALVE_MAX and not np.isnan(zd.A_m[i:i + NIGHT_MIN_LEN]).any():
            out.append(i)
        day += dt.timedelta(days=1)
    return out


def experience(zd, i_cut):
    """Coldest outdoor and largest gap (target - reference room) of the reached warm-ups before minute i_cut."""
    warm, _ = find_events(zd, 0, i_cut)
    seen = []
    for i in warm:
        tgt, _, mm, intr = warm_outcome(zd, i)
        if not intr and mm is not None and i + mm < i_cut:
            seen.append((float(zd.To[i]), tgt - float(zd.T_REF[i])))
    if not seen:
        return {'warm_ups': 0}
    return {'warm_ups': len(seen), 'outdoor_min': round(min(o for o, _ in seen), 1), 'gap_max': round(max(g for _, g in seen), 2)}


def outside(exp, outdoor, gap):
    """Why a warm-up at this outdoor temperature and gap lies outside the experience ('' if inside)."""
    if not exp.get('warm_ups'):
        return 'no warm-ups'
    why = []
    if outdoor < exp['outdoor_min']: why.append('colder')
    if gap > exp['gap_max']: why.append('larger gap')
    return ' '.join(why)


def check_fit(zd, par, p, cut, tz):
    """Plausibility of the parameter vector p fitted on data before cut. Returns a JSON-able dict."""
    P = par.as_dict(p)
    fail, warn = [], []
    if _on(par, 'k', P['k'], 'lo'): fail.append('k=0 (no loss to outside)')
    fail += [f'{n}=0 (no heat)' for n in par.NAMES if n.startswith('a_') and _on(par, n, P[n], 'lo')]
    if _on(par, 'h', P['h'], 'lo'): warn.append('h=0 (no wall node, cr unidentified)')
    if _on(par, 'tau_rad', P['tau_rad'], 'lo'): warn.append('tau_rad on lower bound (no radiator lag)')
    wins = night_windows(zd, cut, tz)
    night = {'nights': len(wins)}
    if wins:
        sim = simulate(zd, par, p, wins, NIGHT_MIN_LEN, True)
        err = sim[:, -1] - zd.A_m[np.asarray(wins) + NIGHT_MIN_LEN - 1]
        night.update(median_k=round(float(np.median(err)), 2), worst_k=round(float(err[np.argmax(np.abs(err))]), 2))
        if len(wins) >= NIGHT_MIN and abs(night['median_k']) > NIGHT_MAX_ERR_K:
            fail.append(f"night cooling off by {night['median_k']:+.2f} K")
    exp = experience(zd, int((cut - zd.t[0]) // 60))
    return {'status': 'unreliable' if fail else 'ok', 'reasons': fail, 'warnings': warn,
            'night': night, 'experience': exp}
