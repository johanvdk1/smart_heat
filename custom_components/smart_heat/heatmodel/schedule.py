"""Smart Heat's own schedule per zone, the one-off "target by time" request, and the planner (decision 28).

Schedule of a zone: {'base': 16.0, 'days': {'mon': [{'start': '07:00', 'end': '22:30', 'temperature': 20.0}], ...}}
(base: the setpoint outside the periods). Request: {'temperature': 21.0, 'ready_at': ISO datetime}; it holds
from ready_at until the next switch point of the schedule. Times are local; pure Python."""
import datetime as dt
from .equation import MAX_LEAD_MIN, minutes, out_of_range

DAYS = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
HORIZON_H = 24


def _hm(s):
    h, m = s.split(':')
    return dt.time(int(h), int(m))


def validate(s):
    """Problems in one zone's schedule (empty = usable)."""
    err = []
    if not isinstance(s, dict) or 'base' not in s:
        return ['schedule needs base']
    for d, periods in (s.get('days') or {}).items():
        if d not in DAYS:
            err.append(f'unknown day {d}'); continue
        last = None
        for p in periods:
            try:
                a, b = _hm(p['start']), _hm(p['end']); float(p['temperature'])
            except (KeyError, ValueError, TypeError, AttributeError):
                err.append(f'{d}: bad period {p}'); continue
            if b <= a: err.append(f"{d}: {p['start']}-{p['end']} ends before it starts")
            if last and a < last: err.append(f"{d}: {p['start']} overlaps the period before")
            last = b
    return err


def switch_points(s, frm, to):
    """(local datetime, temperature, 'schedule') for every period start (its temperature) and end (base)
    between frm and to (aware datetimes), in time order."""
    tz, out = frm.tzinfo, []
    d = (frm - dt.timedelta(days=1)).date()
    while d <= to.date():
        for p in (s.get('days') or {}).get(DAYS[d.weekday()], []):
            for t, temp in ((p['start'], float(p['temperature'])), (p['end'], float(s['base']))):
                at = dt.datetime.combine(d, _hm(t), tzinfo=tz)
                if frm <= at <= to: out.append((at, temp, 'schedule'))
        d += dt.timedelta(days=1)
    return sorted(out, key=lambda x: x[0])


def setpoint_at(s, req, now):
    """The schedule's setpoint at now, or the request's after its ready_at until the next switch point."""
    past = switch_points(s, now - dt.timedelta(days=8), now)
    sp, last = (past[-1][1], past[-1][0]) if past else (float(s['base']), None)
    if req:
        r = dt.datetime.fromisoformat(req['ready_at'])
        if r <= now and (last is None or r > last):
            sp = float(req['temperature'])
    return sp


def request_done(s, req, now):
    """A request is done once a switch point of the schedule has passed after its ready_at."""
    r = dt.datetime.fromisoformat(req['ready_at'])
    return r <= now and bool(switch_points(s, r + dt.timedelta(seconds=1), now))


def rises(s, req, now):
    """Every moment within HORIZON_H at which the setpoint rises: [(ready_at, target, source)]."""
    ev = switch_points(s, now + dt.timedelta(seconds=1), now + dt.timedelta(hours=HORIZON_H))
    if req:
        r = dt.datetime.fromisoformat(req['ready_at'])
        if now < r <= now + dt.timedelta(hours=HORIZON_H):
            ev = sorted(ev + [(r, float(req['temperature']), 'request')], key=lambda x: (x[0], x[2] != 'request'))
    prev, out = setpoint_at(s, req, now), []
    for at, temp, src in ev:
        if temp > prev:
            out.append((at, temp, src))
        prev = temp
    return out


def next_rise(s, req, now):
    """The first coming rise, or None."""
    r = rises(s, req, now)
    return r[0] if r else None


def plan(s, req, now, room, outdoor, valve_open, params, tol):
    """The coming pre-heat start, and the setpoint to send now: the target while its pre-heat is due, else None
    (Smart Heat only raises a setpoint, at a pre-heat start; the schedule's ends and base only tell where the
    next rise is). Of all rises within HORIZON_H the one that needs the earliest start decides (a later, higher
    target may have to start first); of those already due, the highest target."""
    best = None
    for ready, target, src in rises(s, req, now):
        rng = out_of_range(room, target, outdoor)
        need = minutes(params, room, target, outdoor, valve_open, tol)
        lead = 0.0 if rng else min(need, MAX_LEAD_MIN)
        start = ready - dt.timedelta(minutes=lead)
        c = {'ready_at': ready, 'target': target, 'source': src, 'minutes': round(need, 1), 'start_at': start,
             'capped': need > MAX_LEAD_MIN and not rng, 'need': need,
             'reason': f'outside the working range ({rng}): no pre-heat' if rng else ''}
        due = need > 0 and start <= now
        key = (0, -target) if due else (1, start)
        if best is None or key < best['key']:
            best = dict(c, key=key)
    if best is None:
        return {'setpoint': None, 'preheat': False, 'ready_at': None}
    best.pop('key')
    pre = best.pop('need') > 0 and best['start_at'] <= now < best['ready_at']
    return dict(best, setpoint=best['target'] if pre else None, preheat=pre)
