"""v4 B: air node T, wall node M, loss to outside, radiator lag, dead time, heat a_r * u**gam.

    dP/dt = (p - P) / tau_rad,   p = sum_r a_r * u_r(t - dead)**gam
    dT/dt = P - h*(T - M) - k*(T - Tout) + c0 + gp*use + kn*(T_neighbour - T)
    dM/dt = (h / cr) * (T - M)
"""
import numpy as np

DT = 1 / 60.0                                       # one minute in hours
BND = {'a': (0.0, 20.0, 3.0), 'k': (0.0, 1.0, 0.05), 'h': (0.0, 30.0, 1.0), 'cr': (1.0, 200.0, 20.0),
       'tau_rad': (0.05, 3.0, 0.5), 'gw': (0.0, 10.0, 0.0), 'c': (-2.0, 2.0, 0.0)}
EXTRA = {'gam': (0.2, 1.0, 1.0), 'gp': (0.0, 3.0, 0.0), 'kn': (0.0, 2.0, 0.0)}


class Params:
    """Parameter names, bounds and start values for a zone's rooms."""

    def __init__(self, rooms):
        self.rooms = list(rooms)
        self.NAMES = [f'a_{r}' for r in rooms] + ['k', 'h', 'cr', 'tau_rad', 'gw', 'c0', 'c1', 'c2', 'c3']
        base = lambda nm: nm.split('_')[0] if nm.split('_')[0] in ('a',) else (nm if nm in BND else nm[0])
        self.LB = np.array([BND[base(nm)][0] for nm in self.NAMES])
        self.UB = np.array([BND[base(nm)][1] for nm in self.NAMES])
        self.X0 = np.array([BND[base(nm)][2] for nm in self.NAMES])
        self.NAMES = self.NAMES + list(EXTRA)
        self.LB = np.append(self.LB, [v[0] for v in EXTRA.values()])
        self.UB = np.append(self.UB, [v[1] for v in EXTRA.values()])
        self.X0 = np.append(self.X0, [v[2] for v in EXTRA.values()])
        self.I = {nm: k for k, nm in enumerate(self.NAMES)}
        self.CORE = [f'a_{r}' for r in rooms] + ['k', 'h', 'cr', 'c0']
        self.FREE = self.CORE + ['tau_rad']        # v4 B

    def as_dict(self, p):
        return dict(zip(self.NAMES, p))


def step(P, T, M, Pr, pw, toj, gj, lag):
    """One minute forward. Returns new (T, M, Pr)."""
    Pr = Pr + DT * (pw - Pr) / P['tau_rad'] if lag else pw
    dT = Pr - P['h'] * (T - M) - P['k'] * (T - toj) + gj
    M = M + DT * (P['h'] / P['cr']) * (T - M)
    return T + DT * dT, M, Pr


def simulate(zd, par, p, idx, length, lag, burn=None):
    """Zone temperature for `length` minutes from each start in idx, with the measured valves; the
    `burn` minutes before each start are replayed with the air pinned to the measurement."""
    burn = zd.BURN if burn is None else burn
    P = par.as_dict(p)
    pin = sum(P[f'a_{r}'] * zd.U[r] ** P['gam'] for r in zd.rooms)
    j0 = np.asarray(idx) - burn
    T = zd.A_m[j0].copy(); M = T.copy(); Pr = np.zeros(len(j0))
    out = np.empty((len(j0), length))
    for s_ in range(burn + length):
        j = j0 + s_
        if s_ <= burn:
            am = zd.A_m[j]; T = np.where(np.isnan(am), T, am)
        if s_ >= burn:
            out[:, s_ - burn] = T
        g = P['c0'] + P['gp'] * zd.USE[j] + P['kn'] * (zd.TN[j] - T)
        T, M, Pr = step(P, T, M, Pr, pin[np.maximum(j - zd.DEAD, 0)], zd.To[j], g, lag)
    return out


def resid(zd, par, p, idx, M, lag):
    return np.where(np.isnan(M), 0.0, simulate(zd, par, p, idx, M.shape[1], lag) - np.nan_to_num(M))


def rmse(zd, par, p, idx, M, lag):
    return np.sqrt((resid(zd, par, p, idx, M, lag) ** 2).sum() / (~np.isnan(M)).sum())


def forward(zd, P, i, length, C, off_, tn_mode):
    """Planner prediction from minute i: replay the BURN minutes before i with the measured valves, then
    `length` minutes with valves from the controller model C (per room) at the real setpoints.
    tn_mode 'held': the neighbour stays at its value at i; 'meas': measured."""
    BURN, DEAD, rooms = zd.BURN, zd.DEAD, zd.rooms
    A_m, U, To, USE, TN, SPr, pump, room_T = zd.A_m, zd.U, zd.To, zd.USE, zd.TN, zd.SPr, zd.pump, zd.room_T
    pin = sum(P[f'a_{r}'] * U[r] ** P['gam'] for r in rooms)
    T = A_m[i - BURN]; M = T; Pr = 0.0
    for j in range(i - BURN, i):
        if not np.isnan(A_m[j]): T = A_m[j]
        T, M, Pr = step(P, T, M, Pr, pin[max(j - DEAD, 0)], To[j], P['c0'] + P['gp'] * USE[j] + P['kn'] * (TN[j] - T), True)
    T = A_m[i] if not np.isnan(A_m[i]) else T
    hist = {r: list(U[r][i - DEAD:i]) for r in rooms}
    buf = {r: list(room_T[r][i - 10:i]) for r in rooms}
    zone = np.empty(length)
    for s in range(length):
        j = i + s; pw = 0.0
        for r in rooms:
            room = T + off_[r]; b = C[r]
            prev = buf[r][0] if not np.isnan(buf[r][0]) else room
            u = float(np.clip(b[0] + b[1] * (SPr[r][j] - room) + b[2] * (room - prev) * 6.0, 0, 1)) if pump[j] > 0.5 else 0.0
            buf[r].append(room); buf[r].pop(0)
            hist[r].append(u); pw += P[f'a_{r}'] * hist[r].pop(0) ** P['gam']
        tn = TN[j] if tn_mode == 'meas' else TN[i]
        T, M, Pr = step(P, T, M, Pr, pw, To[j], P['c0'] + P['gp'] * USE[j] + P['kn'] * (tn - T), True)
        zone[s] = T
    return zone
