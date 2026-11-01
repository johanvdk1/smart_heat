"""The owner's start-time equation (AUTO_HEAT_DESIGN.md, decision 28).

    minutes = dead time * w + minutes per K * (target - room) + minutes per K outside * (target - outdoor)

w = 1/2 when the reference room's valve is already open at the start (the radiator is not cold), else 1.
Learned per zone warm-up by warm-up (decision 28): the error of the current numbers corrects them, and the
corrected numbers carry over to the next warm-up (recursive least squares). Every warm-up counts equally,
however old (owner, 2026-11-01): what was learned stays in the numbers, so nothing is recalculated. Pure numpy."""
import numpy as np

NAMES = ('dead_time', 'per_k', 'per_k_out')
LB = np.array([0.0, 2.0, 0.0])            # dead time 0-90 min, 2-90 min per K, 0-5 min per K outside
UB = np.array([90.0, 90.0, 5.0])
DEFAULT = {'dead_time': 30.0, 'per_k': 20.0, 'per_k_out': 0.5}   # the starting numbers of a zone
VALVE_OPEN = 0.05                          # reference room's valve open at the start: the radiator is warm
OUTDOOR_RANGE, INSIDE_RANGE = (-15.0, 21.0), (5.0, 23.0)
MAX_LEAD_MIN = 240


def minutes(p, room, target, outdoor, valve_open, tol=0.0):
    """Predicted minutes until the room is within tol of target (0 when it already is)."""
    if room >= target - tol:
        return 0.0
    return (p['dead_time'] * (0.5 if valve_open else 1.0) + p['per_k'] * (target - room)
            + p['per_k_out'] * max(0.0, target - outdoor))


def out_of_range(room, target, outdoor):
    """Why a warm-up lies outside the working range ('' if inside)."""
    why = []
    if not OUTDOOR_RANGE[0] <= outdoor <= OUTDOOR_RANGE[1]: why.append(f'outdoor {outdoor:.1f}')
    if not INSIDE_RANGE[0] <= target <= INSIDE_RANGE[1]: why.append(f'target {target:.1f}')
    if not INSIDE_RANGE[0] <= room <= INSIDE_RANGE[1]: why.append(f'room {room:.1f}')
    return ', '.join(why)


NOISE_MIN = 10.0                           # typical scatter of a warm-up's minutes
P0 = np.diag([30.0, 10.0, 1.0]) ** 2       # uncertainty of the starting numbers (min, min per K, min per K outside)


def regressors(r):
    return np.array([0.5 if r['valve_open'] else 1.0, r['target'] - r['start_temp'], max(0.0, r['target'] - r['outdoor'])])


def new_state():
    """A zone without experience: the starting numbers, fully uncertain."""
    return {'x': [DEFAULT[k] for k in NAMES], 'P': P0.tolist(), 'n': 0, 'ts': None}


def params(state):
    return dict({k: round(float(v), 3) for k, v in zip(NAMES, state['x'])}, n=state['n'])


def learn(state, r):
    """One warm-up (dict with ts, start_temp, target, outdoor, valve_open, minutes): the error of the current
    numbers, and the numbers corrected by it (recursive least squares): a number moves more the more this
    warm-up says about it and the less certain it still is. Returns (new state, error in minutes: predicted -
    measured)."""
    x, P = np.array(state['x'], float), np.array(state['P'], float)
    a = regressors(r)
    err = float(a @ x - r['minutes'])
    k = P @ a / (a @ P @ a + NOISE_MIN ** 2)
    x = np.clip(x - k * err, LB, UB)
    P = P - np.outer(k, a @ P)
    return {'x': x.tolist(), 'P': ((P + P.T) / 2).tolist(), 'n': state['n'] + 1, 'ts': r['ts']}, err
