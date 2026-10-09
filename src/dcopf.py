"""DC optimal power flow (B-theta formulation) with cvxpy.

Units: Pg and flows in MW, theta in rad, cost in $/h, LMP in $/MWh.
Numbering in results: generators G1.., branches L1.., buses by file number.
"""
from __future__ import annotations

import time

import cvxpy as cp
import numpy as np

from src.network import Network

BINDING_PCT = 99.9
# Tight settings so the first-order OSQP cross-check reaches LP-level accuracy.
SOLVER_OPTIONS = {"OSQP": {"eps_abs": 1e-8, "eps_rel": 1e-8, "max_iter": 1000000, "polishing": True}}


def incidence(net: Network, in_service_only: bool = True):
    """Branch-bus incidence C (n_branch x n_bus): +1 at from bus, -1 at to bus.

    Rows of out-of-service branches are zero when in_service_only is True.
    """
    C = np.zeros((net.n_branch, net.n_bus))
    for k in range(net.n_branch):
        if in_service_only and not net.br_status[k]:
            continue
        C[k, net.bus_index(int(net.br_from[k]))] = 1.0
        C[k, net.bus_index(int(net.br_to[k]))] = -1.0
    return C


def gen_bus_matrix(net: Network):
    """Generator-bus map Cg (n_bus x n_gen): 1 where generator k sits."""
    Cg = np.zeros((net.n_bus, net.n_gen))
    Cg[net.gen_bus_index(), np.arange(net.n_gen)] = 1.0
    return Cg


def branch_flows_mw(net: Network, theta: np.ndarray) -> np.ndarray:
    """Flow from->to in MW for bus angles theta (rad, file bus order).

    flow = baseMVA * b * (theta_from - theta_to - shift_rad); 0 for branches out.
    """
    C = incidence(net)
    shift = np.deg2rad(net.br_shift)
    return net.baseMVA * net.susceptance() * (C @ theta - shift) * net.br_status


def generation_cost(net: Network, pg: np.ndarray) -> float:
    """Total cost in $/h for pg (MW, G1..Gn order); out-of-service units cost 0."""
    on = net.gen_status == 1
    return float(np.sum((net.c2 * pg ** 2 + net.c1 * pg + net.c0)[on]))


def loading_pct(net: Network, flow: np.ndarray) -> np.ndarray:
    """|flow| / RATE_A * 100 per branch; NaN where RATE_A = 0 (no limit) or out."""
    pct = np.full(net.n_branch, np.nan)
    lim = (net.rate_a > 0) & (net.br_status == 1)
    pct[lim] = np.abs(flow[lim]) / net.rate_a[lim] * 100.0
    return pct


def solve_dcopf(net: Network, solver: str | None = None) -> dict:
    """Solve the DC-OPF. Never raises.

    Variables Pg (MW) and theta (rad); nodal balance Cg Pg - Pd - Gs = C^T flow
    at every bus; |flow| <= RATE_A on in-service branches with RATE_A > 0;
    Pmin <= Pg <= Pmax for in-service units (Pg = 0 for units out);
    theta_ref = 0. Objective: sum c2 Pg^2 + c1 Pg + c0 ($/h).

    Returns a dict with status ('optimal' | 'infeasible' | 'error'), cost ($/h),
    pg {G: MW}, theta {bus: rad}, flow {L: MW}, loading_pct {L: % or None},
    max_loading_pct, binding [L..], lmp {bus: $/MWh}, balance_residual_mw,
    solver, solve_time_s.
    """
    solver = solver or "CLARABEL"
    t0 = time.perf_counter()
    out = {"status": "error", "solver": solver}
    try:
        C = incidence(net)
        Cg = gen_bus_matrix(net)
        b = net.susceptance() * net.br_status
        shift = np.deg2rad(net.br_shift)
        ref = net.bus_index(net.ref_bus)
        on = net.gen_status == 1

        # Internally in p.u. (MW / baseMVA): this keeps the problem well scaled,
        # which the first-order OSQP cross-check needs. Results are in MW.
        base = net.baseMVA
        pg = cp.Variable(net.n_gen)  # p.u.
        th = cp.Variable(net.n_bus)  # rad
        flow = cp.multiply(b, C @ th - shift)  # p.u.
        balance = Cg @ pg - C.T @ flow == (net.pd + net.gs) / base
        cons = [balance, th[ref] == 0]
        lo = np.where(on, net.pmin, 0.0) / base
        hi = np.where(on, net.pmax, 0.0) / base
        cons += [pg >= lo, pg <= hi]
        lim = np.flatnonzero((net.rate_a > 0) & (net.br_status == 1))
        if len(lim):
            cons += [flow[lim] <= net.rate_a[lim] / base, flow[lim] >= -net.rate_a[lim] / base]
        pg_mw = base * pg
        cost = (net.c1 * on) @ pg_mw + float(np.sum(net.c0[on]))
        if np.any(net.c2[on] != 0):  # a zero quadratic term only hurts the solvers
            cost = cost + cp.sum(cp.multiply(net.c2 * on, cp.square(pg_mw)))
        prob = cp.Problem(cp.Minimize(cost), cons)
        opts = SOLVER_OPTIONS.get(solver, {})
        prob.solve(solver=solver, **opts)
        out["solve_time_s"] = time.perf_counter() - t0
        if prob.status in ("infeasible", "infeasible_inaccurate"):
            out["status"] = "infeasible"
            return out
        if prob.status not in ("optimal", "optimal_inaccurate") or pg.value is None:
            out["status"] = "error"
            out["error"] = f"solver status {prob.status}"
            return out

        pg_v = base * pg.value
        pg_v = np.where(np.abs(pg_v) < 1e-7, 0.0, pg_v)
        th_v = th.value
        f_v = branch_flows_mw(net, th_v)
        pct = loading_pct(net, f_v)
        resid = Cg @ pg_v - C.T @ f_v - net.pd - net.gs
        # cvxpy's dual of "lhs == rhs" has the sign such that raising the rhs
        # (Pd, in p.u.) by 1 raises the cost by -dual $/h. Per MW: divide by
        # baseMVA. LMP = -dual / baseMVA is then positive ($/MWh).
        lmp = -np.asarray(balance.dual_value, dtype=float) / base
        out.update(
            status="optimal",
            cost=generation_cost(net, pg_v),
            pg={g: float(v) for g, v in zip(net.gen_ids, pg_v)},
            theta={int(n): float(v) for n, v in zip(net.bus_num, th_v)},
            flow={l: float(v) for l, v in zip(net.line_ids, f_v)},
            loading_pct={l: (None if np.isnan(v) else float(v)) for l, v in zip(net.line_ids, pct)},
            max_loading_pct=float(np.nanmax(pct)) if np.any(~np.isnan(pct)) else 0.0,
            binding=[l for l, v in zip(net.line_ids, pct) if not np.isnan(v) and v >= BINDING_PCT],
            lmp={int(n): float(v) for n, v in zip(net.bus_num, lmp)},
            balance_residual_mw=float(np.max(np.abs(resid))),
        )
    except Exception as exc:  # never crash: the agent records the error
        out["status"] = "error"
        out["error"] = f"{type(exc).__name__}: {exc}"
        out.setdefault("solve_time_s", time.perf_counter() - t0)
    return out
