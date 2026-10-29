"""Zone data on a one-minute grid, built from raw state history."""
import datetime as dt, json
import numpy as np

SEG, STEP = 720, 60                                 # training segments: 12 h, one starting every hour
PUMP_MIN_W = 2.0                                    # heating pump below this power: not running


def points(rows, attr=None, unavailable=False):
    """(ts, value) pairs from raw history rows: an attribute, on/off as 1/0, or a numeric state.
    Attributes may be a dict or a JSON string (parsed only when an attribute is asked for).
    unavailable=True: unavailable/unknown become NaN (no value until the next one) instead of being skipped."""
    out = []
    for ts, st, at in rows:
        if unavailable and st in ('unavailable', 'unknown'):
            out.append((ts, np.nan))
        elif attr:
            if isinstance(at, str): at = json.loads(at) if at else None
            if st in ('unavailable', 'unknown') or not at: continue
            v = at.get(attr)
            if v is not None: out.append((ts, float(v)))
        elif st in ('on', 'off'):
            out.append((ts, 1.0 if st == 'on' else 0.0))
        else:
            try: out.append((ts, float(st)))
            except (TypeError, ValueError): pass
    return out


def grid(pts, t, max_age=None):
    """Last value at or before each grid time; NaN before the first point or when older than max_age (s)."""
    n = len(t)
    if not pts: return np.full(n, np.nan)
    ts = np.array([x[0] for x in pts]); vs = np.array([x[1] for x in pts])
    idx = np.searchsorted(ts, t, side='right') - 1
    col = np.where(idx >= 0, vs[np.clip(idx, 0, None)], np.nan)
    if max_age:
        age = t - np.where(idx >= 0, ts[np.clip(idx, 0, None)], -1e12)
        col[age > max_age] = np.nan
    return col


def bridge(col, maxgap):
    """Fill NaN runs of at most maxgap minutes with the value before them."""
    n = len(col); out = col.copy(); isn = np.isnan(out); i = 0
    while i < n:
        if isn[i]:
            j = i
            while j < n and isn[j]: j += 1
            if i > 0 and j - i <= maxgap: out[i:j] = out[i - 1]
            i = j
        else:
            i += 1
    return out


def ffill(col, default):
    out = col.copy()
    for i in range(1, len(out)):
        if np.isnan(out[i]): out[i] = out[i - 1]
    return np.where(np.isnan(out), default, out)


class ZoneData:
    """Everything the model needs for one zone of zones.yaml, from t0 to t_end (unix seconds).

    history(entity_id) returns raw rows (ts, state, attributes) from t0 - 1 day on."""

    def __init__(self, conf, zone, history, t0, t_end):
        H, Z, D = conf['house'], conf['zones'][zone], conf.get('data', {})
        self.conf, self.zone, self.H, self.Z, self.D = conf, zone, H, Z, D
        self.T0 = t0
        self.DEAD = int(D.get('dead_time_min', 15))
        self.BURN = int(D.get('burn_in_h', 36)) * 60
        self.GAP_MAX = int(D.get('bridge_gap_min', 60))
        self.FLOOR = float(Z.get('comfort_floor', 19.0))
        self.EV = D['events']                       # warm-up / cool-down rules (no defaults)
        self.rooms = ROOMS = list(Z['rooms'])
        t = self.t = np.arange(t0, t_end, 60.0); n = self.n = len(t)
        g = lambda e, attr=None, max_age=None, unavailable=False: grid(points(history(e), attr, unavailable), t, max_age)

        self.room_T = {r: bridge(g(rc['temperature'], max_age=3 * 3600), self.GAP_MAX) for r, rc in Z['rooms'].items()}
        area = np.array([float(Z['rooms'][r].get('area_m2', 1.0)) for r in ROOMS]); wts = area / area.sum()
        self.A_m = np.tensordot(wts, np.stack([self.room_T[r] for r in ROOMS]), axes=1)
        # heating pump: speed and power vary and do not matter; without power the radiators get no hot water,
        # so the valves give no heat (the head still sets them: the controller model uses V, the heat model U)
        self.HOT = np.nan_to_num(g(H['heating_pump']), nan=PUMP_MIN_W) >= PUMP_MIN_W
        self.V = {r: np.nan_to_num(g(rc['valve']) / 100.0) for r, rc in Z['rooms'].items()}
        self.U = {r: np.where(self.HOT, v, 0.0) for r, v in self.V.items()}
        self.SPr = {r: ffill(g(rc['climate'], attr='temperature'), np.nan) for r, rc in Z['rooms'].items()}

        # outdoor: the real sensor (battery device, can fail on cold mornings); while it is unavailable or
        # before its first value the internet estimate, else 10
        To = g(H['outdoor'], unavailable=True)
        if H.get('outdoor_internet'):
            To = np.where(np.isnan(To), ffill(g(H['outdoor_internet']), np.nan), To)
        self.To = np.where(np.isnan(To), 10.0, To)
        in_use = Z.get('in_use') or []
        pc = sum(np.nan_to_num(g(e)) for e in in_use) if in_use else np.zeros(n)
        self.USE = np.clip((pc - 15.0) / 30.0, 0, 1) if in_use else np.zeros(n)
        self.NBS = Z.get('neighbours') or []
        self.TN = ffill(g(self.NBS[0]['temperature']), 15.0) if self.NBS else np.zeros(n)

        # Door contact (rooms.<room>.door): while the door is open the thermostat drops to its door-open
        # setpoint (e.g. 12 degC), which is not a wish for that temperature. Events and the controller model use the
        # setpoint from before the door opened; the controller model skips the door-open minutes (the planner
        # assumes a closed door). The thermal model uses the measured valves. Rooms without a door: no change.
        self.DOOR_HOLD_MIN = 5                      # minutes after closing until the thermostat is back
        self.DOOR = {}
        for r in ROOMS:
            self.DOOR[r] = np.zeros(n, bool)
            if Z['rooms'][r].get('door'):
                op = np.nan_to_num(g(Z['rooms'][r]['door'])) > 0.5
                SPr = self.SPr[r]
                for k in range(1, self.DOOR_HOLD_MIN + 1): op[k:] |= op[:-k] & (SPr[k:] < SPr[:-k] + 1e-6)
                self.DOOR[r] = op
                sp_ = SPr.copy(); sp_[op] = np.nan; self.SPr[r] = ffill(sp_, np.nan)

        self.day = ((t - t0) // 86400).astype(int)
        if len(ROOMS) > 1 and 'reference_room' not in Z:
            raise ValueError(f"zones.yaml: zone {zone} has several rooms and needs reference_room")
        self.REF = Z.get('reference_room', ROOMS[0])  # in a zone of several rooms, the room whose target and comfort floor count
        self.T_REF, self.SP_REF = self.room_T[self.REF], self.SPr[self.REF]
        A_m, BURN = self.A_m, self.BURN
        self.starts = [i for i in range(BURN, n - SEG, STEP)
                       if not np.isnan(A_m[i - BURN]) and np.isnan(A_m[i:i + SEG]).mean() <= 0.1]

    def describe(self):
        t = self.t
        s = (f"zone {self.zone}: rooms {', '.join(self.rooms)}; data {self.n} minutes, "
             f"{dt.datetime.fromtimestamp(t[0]):%Y-%m-%d %H:%M} .. {dt.datetime.fromtimestamp(t[-1]):%Y-%m-%d %H:%M}")
        for r in self.rooms:
            if self.DOOR[r].any(): s += f"\ndoor {r}: open {self.DOOR[r].mean() * 100:.1f}% of minutes"
        return s
