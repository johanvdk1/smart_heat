"""Bounded nonlinear least squares in numpy (Levenberg-Marquardt), replacing scipy.optimize.least_squares
(scipy is not in Home Assistant 2026.10: not in requirements_all.txt; numpy 2.3.2 is; decision 22, step 2).

Minimises 0.5 * sum(fun(x)**2) with lb <= x <= ub. Jacobian by forward differences with the step of
scipy's diff_step (h = diff_step * max(1, |x|), away from the nearer bound). Marquardt scaling by the
diagonal of J^T J (as scipy's x_scale='jac'). A parameter on a bound whose gradient points outwards is
held there for that iteration (active set), so a bound does not stall the others."""
import numpy as np


class Result:
    def __init__(self, x, cost, nfev, nit):
        self.x, self.cost, self.nfev, self.nit = x, cost, nfev, nit


def lm(fun, x0, lb=None, ub=None, diff_step=1e-3, max_iter=100, ftol=1e-8, xtol=1e-8, gtol=1e-8):
    x = np.asarray(x0, float).copy(); m = len(x)
    lb = np.full(m, -np.inf) if lb is None else np.asarray(lb, float)
    ub = np.full(m, np.inf) if ub is None else np.asarray(ub, float)
    x = np.clip(x, lb, ub)
    r = fun(x); cost = 0.5 * r @ r; nfev = 1
    lam = 1e-3
    for nit in range(1, max_iter + 1):
        # forward-difference Jacobian; step towards the interior when x is close to its upper bound
        J = np.empty((len(r), m))
        for k in range(m):
            h = diff_step * max(1.0, abs(x[k]))
            if x[k] + h > ub[k]: h = -h
            xk = x.copy(); xk[k] += h
            J[:, k] = (fun(xk) - r) / h; nfev += 1
        g = J.T @ r
        free = ~(((x <= lb) & (g > 0)) | ((x >= ub) & (g < 0)))
        if not free.any() or np.max(np.abs(g[free])) < gtol * max(1.0, cost): break
        A = J[:, free].T @ J[:, free]; d = np.diag(A).copy(); d[d <= 0] = 1.0
        gf = g[free]
        while True:
            try:
                step = -np.linalg.solve(A + lam * np.diag(d), gf)
            except np.linalg.LinAlgError:
                lam *= 10.0; continue
            xn = x.copy(); xn[free] = np.clip(x[free] + step, lb[free], ub[free])
            rn = fun(xn); cn = 0.5 * rn @ rn; nfev += 1
            if cn < cost:
                dx = np.linalg.norm(xn - x); dc = cost - cn
                x, r = xn, rn; cost_old, cost = cost, cn
                lam = max(lam / 3.0, 1e-12)
                break
            lam *= 4.0
            if lam > 1e12:
                return Result(x, cost, nfev, nit)
        if dc < ftol * cost_old or dx < xtol * (np.linalg.norm(x) + xtol): break
    return Result(x, cost, nfev, nit)
