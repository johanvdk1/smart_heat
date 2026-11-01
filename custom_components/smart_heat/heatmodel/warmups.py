"""Warm-ups as results (decision 28): one row per warm-up of the reference room, the learning material of
the equation. A warm-up is final RECORD_AFTER_MIN after the room reached its target (within reached_tol),
or warm_window_min after its start when it never did."""
import datetime as dt
import numpy as np
from .events import find_events, warm_outcome
from .equation import VALVE_OPEN, out_of_range

RECORD_AFTER_MIN = 30
COLS = ['zone', 'start', 'ts', 'reference_room', 'target', 'start_temp', 'outdoor', 'valve_open', 'minutes',
        'reached_at', 'interrupted', 'door_open', 'learn', 'reason', 'pred_min', 'err_min']


def final_warmups(zd, tz, now_ts, since_ts=None, known=(), fault=None):
    """Rows for every warm-up of zd that is final at now_ts (and starts at or after since_ts). known: start
    times already recorded; a rise within min_spacing_min after one of them is the same warm-up (as in
    find_events), also when a shorter history window no longer holds the first rise. fault: per minute of zd.t,
    heating pump in fault (pump.fault_mask); a warm-up that saw one, from the minute before its start, is not
    learned from."""
    warm, _ = find_events(zd, 0, zd.n)
    REF, gap = zd.REF, zd.EV['min_spacing_min'] * 60
    known = sorted(known)
    V, T, To, DOOR = zd.V[REF], zd.T_REF, zd.To, zd.DOOR[REF]
    out = []
    for i in warm:
        ts = float(zd.t[i])
        if since_ts is not None and ts < since_ts:
            continue
        if any(0 < ts - k <= gap for k in known):
            continue
        tgt, L, mm, intr = warm_outcome(zd, i)
        wait = (mm + RECORD_AFTER_MIN) if mm is not None else zd.EV['warm_window_min']
        if ts + wait * 60 > now_ts:
            continue                                  # not final yet: under way, or reached too recently
        start_temp, outdoor = float(T[i]), float(To[i])
        door = bool(DOOR[i:i + (L if mm is None else mm + 1)].any())
        rng = out_of_range(start_temp, tgt, outdoor)
        pump = fault is not None and bool(fault[max(i - 1, 0):i + (L if mm is None else mm + 1)].any())
        why = ('heating pump fault' if pump else 'interrupted' if intr else 'not reached' if mm is None else 'door open' if door
               else f'outside the working range ({rng})' if rng else '')
        out.append({'zone': zd.zone, 'start': dt.datetime.fromtimestamp(ts, tz).strftime('%Y-%m-%d %H:%M'),
                    'ts': ts, 'reference_room': REF, 'target': tgt, 'start_temp': round(start_temp, 2),
                    'outdoor': round(outdoor, 1), 'valve_open': bool(i > 0 and V[i - 1] >= VALVE_OPEN),
                    'minutes': mm,
                    'reached_at': '' if mm is None else dt.datetime.fromtimestamp(ts + mm * 60, tz).strftime('%Y-%m-%d %H:%M'),
                    'interrupted': bool(intr), 'door_open': door, 'learn': not why, 'reason': why})
    return out


def learn_rows(rows):
    """The rows the equation is fitted on, as numbers."""
    def b(v): return v in (True, 'True', 'true', '1')
    return [{'ts': float(r['ts']), 'start_temp': float(r['start_temp']), 'target': float(r['target']),
             'outdoor': float(r['outdoor']), 'minutes': float(r['minutes']), 'valve_open': b(r['valve_open'])}
            for r in rows if b(r['learn'])]
