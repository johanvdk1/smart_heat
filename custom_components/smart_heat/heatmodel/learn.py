"""The learning routine of the learning phase: per finished day D, fit the thermal and
controller model on data before D 00:00 (training segments weighted by age), predict every warm-up and
cool-down of D the way a planner would, and log predicted against actual.
Output (appended; days already in the files are skipped):
  predictions.csv  one row per event        params.csv  one row per fit
Every fit is checked for plausibility (checks.py, decision 27): params.csv 'checks'; each predicted warm-up is
marked 'outside' when it lies beyond the warm-ups that fit was trained on (colder, larger gap).
"""
import csv, json, os, datetime as dt
import numpy as np
from .data import ZoneData, SEG
from .model import Params, forward, rmse
from .controller import ctrl_fit
from .events import find_events, warm_outcome, warm_pred, coast_len
from .fit import fit
from .checks import check_fit, outside

PCOLS = ['run_at', 'zone', 'date', 'kind', 'event', 'reference_room', 'setpoint', 'start_temp', 'outdoor',
         'interrupted', 'pred_min', 'meas_min', 'err_min', 'margin_min', 'late', 'err_1h', 'err_2h', 'err_3h',
         'err_4h', 'err_5h', 'coast_min', 'pred_end', 'meas_end', 'floor', 'allowed', 'stayed_above', 'outside']
QCOLS = ['run_at', 'zone', 'fit_until', 'segments', 'rmse', 'params', 'jumps', 'checks']


def _read(f):
    if not os.path.exists(f): return []
    with open(f, newline='') as fh: return list(csv.DictReader(fh))


def _append(f, cols, rows):
    """Append rows to the CSV f with columns cols; a file written before a column was added is rewritten once with
    the new header (old rows keep their values, the new columns stay empty)."""
    if os.path.exists(f):
        with open(f, newline='') as fh: head = next(csv.reader(fh), None)
        if head and head != cols:
            old = _read(f)
            with open(f, 'w', newline='') as fh:
                wr = csv.DictWriter(fh, cols, extrasaction='ignore'); wr.writeheader(); wr.writerows(old)
    with open(f, 'a', newline='') as fh:
        wr = csv.DictWriter(fh, cols)
        if fh.tell() == 0: wr.writeheader()
        wr.writerows(rows)


def start_ts(conf, tz):
    return dt.datetime.fromisoformat(str(conf.get('data', {}).get('start', '2026-10-09 08:00'))).replace(tzinfo=tz).timestamp()


def learn_zone(conf, zone, history, out_dir, tz, now, start_day=None, log=print):
    """Process every finished day of `zone` from start_day (default data.shadow_start) not yet in out_dir.
    history(entity_id) -> rows (ts, state, attributes) from data.start - 1 day on; now: aware datetime.
    Returns a list of per-day summaries."""
    Z, D = conf['zones'][zone], conf.get('data', {})
    zd = ZoneData(conf, zone, history, start_ts(conf, tz), now.timestamp())
    log(zd.describe())
    par = Params(zd.rooms)
    t, A_m, To, T_REF, SP_REF, EV, REF, FLOOR, ROOMS = zd.t, zd.A_m, zd.To, zd.T_REF, zd.SP_REF, zd.EV, zd.REF, zd.FLOOR, zd.rooms
    os.makedirs(out_dir, exist_ok=True)
    PRED, PARF = os.path.join(out_dir, 'predictions.csv'), os.path.join(out_dir, 'params.csv')
    done = {r['date'] for r in _read(PRED) if r['zone'] == zone} | {r['fit_until'] for r in _read(PARF) if r['zone'] == zone}
    prev = [json.loads(r['params']) for r in _read(PARF) if r['zone'] == zone]
    HALF = float(D.get('half_life_days', 21)); CM = float(D.get('coast_margin', 0.5))
    LEAD = Z.get('lead_time_min')                  # zone with a fixed lead time: warm-ups are scored on it, not on the model
    # start margin: the zone's worst late start minus the lateness that is accepted (a little late beats a lot early)
    MARGIN = 0.0 if LEAD else max(0.0, float(Z['worst_late_min']) - float(D.get('allowed_late_min', 0)))
    FREE_S = par.FREE + ['gam'] + (['kn'] if zd.NBS else [])
    TN_MODE = 'held'
    run_at = now.strftime('%Y-%m-%d %H:%M')
    d = start_day or dt.date.fromisoformat(str(D.get('shadow_start', now.date())))
    fmt = lambda v, n=2: '' if v is None or (isinstance(v, float) and np.isnan(v)) else (f'{v:.{n}f}' if isinstance(v, float) else v)
    out = []
    while True:
        cut = dt.datetime.combine(d, dt.time(), tzinfo=tz).timestamp(); end = cut + 86400
        if end + EV['coast_window_min'] * 60 > t[-1]: break  # day not over yet, or its last coast not observed
        key = d.isoformat()
        if key in done: d += dt.timedelta(days=1); continue
        seg = np.array([i for i in zd.starts if t[i + SEG - 1] < cut], int)
        if len(seg) == 0:                          # no training data yet (new zone): nothing to fit on
            log(f"{zone} {key}: no training segments before this day, skipped")
            d += dt.timedelta(days=1); continue
        Mtr = np.stack([A_m[i:i + SEG] for i in seg])
        p = fit(zd, par, FREE_S, seg, Mtr, cutoff=cut, half_life_days=HALF); P = par.as_dict(p)
        C, off_ = ctrl_fit(zd, t < cut)
        chk = check_fit(zd, par, p, cut, tz)
        keys = [f'a_{r}' for r in ROOMS] + ['k', 'h', 'cr', 'tau_rad', 'c0', 'gam', 'kn']
        pr = {k: round(float(P[k]), 4) for k in keys}
        # a jump: moved more than 50 % of the previous value; floor 0.02, for c0 0.05 K/h (c0 often hovers
        # around 0, where small moves would otherwise be flagged)
        jumps = [k for k in [f'a_{r}' for r in ROOMS] + ['k', 'c0', 'kn'] if prev and
                 abs(pr[k] - prev[-1].get(k, pr[k])) > 0.5 * max(abs(prev[-1].get(k, 0)), 0.05 if k == 'c0' else 0.02)]
        i0 = int((cut - t[0]) // 60); i1 = int((end - t[0]) // 60)
        warm, cool = find_events(zd, i0, i1); rows = []
        for i in warm:
            tgt, L, mm, intr = warm_outcome(zd, i)
            r_ = {'kind': 'warm', 'event': i, 'setpoint': tgt, 'margin_min': MARGIN, 'interrupted': intr,
                  'outside': outside(chk['experience'], float(To[i]), tgt - float(T_REF[i]))}
            if not intr:
                pm = int(LEAD) if LEAD else warm_pred(zd, forward(zd, P, i, L, C, off_, TN_MODE), tgt, off_)
                # late: the room reached the target later than a planner starting pred + margin minutes early expects
                r_.update(pred_min=pm, meas_min=mm, err_min=pm - mm if pm is not None and mm is not None else None,
                          late=pm is not None and (mm is None or mm > pm + MARGIN))
            rows.append(r_)
        for i in cool:
            L = coast_len(zd, i)
            if L < 60: continue
            zone_T = forward(zd, P, i, L, C, off_, TN_MODE)
            r_ = {'kind': 'cool', 'event': i, 'setpoint': float(SP_REF[i]), 'coast_min': L, 'floor': FLOOR}
            for h in (1, 2, 3, 4, 5):
                if h * 60 + 5 < L: r_[f'err_{h}h'] = float(np.nanmean(A_m[i + h * 60 - 5:i + h * 60 + 5])) - zone_T[h * 60]
            pe = float(zone_T[-1] + off_[REF])         # reference room = zone temperature + that room's mean offset
            me = float(np.nanmean(T_REF[i + L - 10:i + L]))
            r_.update(pred_end=pe, meas_end=me, allowed=pe >= FLOOR + CM, stayed_above=float(np.nanmin(T_REF[i:i + L])) >= FLOOR)
            rows.append(r_)
        written = []
        for r_ in sorted(rows, key=lambda r: r['event']):
            i = r_['event']
            r_.update(run_at=run_at, zone=zone, date=key, event=dt.datetime.fromtimestamp(t[i], tz).strftime('%Y-%m-%d %H:%M'),
                      reference_room=REF, start_temp=float(T_REF[i]), outdoor=float(To[i]))
            written.append({k: fmt(r_.get(k), 1 if k in ('start_temp', 'outdoor', 'setpoint') else 2) for k in PCOLS})
        _append(PRED, PCOLS, written)
        q = {'run_at': run_at, 'zone': zone, 'fit_until': key, 'segments': len(seg),
             'rmse': f'{rmse(zd, par, p, seg, Mtr, True):.3f}', 'params': json.dumps(pr), 'jumps': ' '.join(jumps),
             'checks': json.dumps(chk)}
        _append(PARF, QCOLS, [q])
        prev.append(pr)
        log(f"{zone} {key}: fitted on {len(seg)} segments; {len(warm)} warm-ups, {len(cool)} cool-downs"
            + (f"; parameter jumps: {' '.join(jumps)}" if jumps else '')
            + (f"; model unreliable: {'; '.join(chk['reasons'])}" if chk['reasons'] else '')
            + (f"; warning: {'; '.join(chk['warnings'])}" if chk['warnings'] else ''))
        out.append({'date': key, 'rmse': float(q['rmse']), 'params': pr, 'jumps': jumps, 'checks': chk, 'events': written})
        d += dt.timedelta(days=1)
    return out
