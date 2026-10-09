"""DC power flow for a fixed generator dispatch (used to judge Mode 1 and to
re-check any dispatch on the true network).

Units: MW, rad, $/h. Generators G1.., branches L1.., buses by file number.
"""
from __future__ import annotations

import networkx as nx
import numpy as np

from src.dcopf import branch_flows_mw, gen_bus_matrix, generation_cost, incidence, loading_pct
from src.network import Network

LIMIT_TOL_MW = 1e-3
BALANCE_TOL_MW = 1e-4


def slack_gen(net: Network) -> int | None:
    """0-based index of the slack generator.

    The in-service unit at the reference bus with the largest Pmax; if there is
    none, the in-service unit with the largest Pmax anywhere. None if no unit.
    """
    on = np.flatnonzero(net.gen_status == 1)
    if len(on) == 0:
        return None
    at_ref = [k for k in on if net.gen_bus[k] == net.ref_bus and net.pmax[k] > 0]
    pool = at_ref or list(on)
    return int(max(pool, key=lambda k: net.pmax[k]))


def is_connected(net: Network) -> bool:
    """True if every bus is reachable over in-service branches."""
    g = nx.Graph()
    g.add_nodes_from(int(b) for b in net.bus_num)
    g.add_edges_from((int(f), int(t)) for f, t, s in zip(net.br_from, net.br_to, net.br_status) if s)
    return nx.is_connected(g)


def dc_power_flow(net: Network, pg: dict) -> dict:
    """DC power flow for fixed outputs pg {G1: MW, ...}; missing units = 0 MW.

    The slack generator (see slack_gen) absorbs any mismatch so the flow
    balances; mismatch_mw = total load - total scheduled generation (MW, the
    amount added to the slack unit). Reports generator-limit violations
    (against Pmin/Pmax of this network, 1e-3 MW tolerance; units out must be
    0 MW) and line overloads (RATE_A > 0 only).

    Returns status ('ok' | 'islanded' | 'no_slack'), pg_final {G: MW},
    slack, mismatch_mw, flow {L: MW}, loading_pct {L: %}, max_loading_pct,
    gen_violations [...], line_violations [...], cost ($/h), feasible (limits
    ok after slack), feasible_strict (also |mismatch| <= 1e-4 MW).
    """
    p = np.array([float(pg.get(g, 0.0) or 0.0) for g in net.gen_ids])
    out = {"pg_scheduled": {g: float(v) for g, v in zip(net.gen_ids, p)}}
    if not is_connected(net):
        out.update(status="islanded", feasible=False, feasible_strict=False)
        return out
    k = slack_gen(net)
    if k is None:
        out.update(status="no_slack", feasible=False, feasible_strict=False)
        return out

    mismatch = net.total_load_mw() - float(p.sum())
    p_final = p.copy()
    p_final[k] += mismatch

    # Solve B theta = P (p.u.) with the slack unit's bus as angle reference.
    C = incidence(net)
    b = net.susceptance() * net.br_status
    shift = np.deg2rad(net.br_shift)
    B = C.T @ np.diag(b) @ C
    p_inj = (gen_bus_matrix(net) @ p_final - net.pd - net.gs) / net.baseMVA
    p_inj = p_inj + C.T @ (b * shift)  # phase-shifter injection, as makeBdc
    ref = net.bus_index(int(net.gen_bus[k]))
    keep = [i for i in range(net.n_bus) if i != ref]
    theta = np.zeros(net.n_bus)
    theta[keep] = np.linalg.solve(B[np.ix_(keep, keep)], p_inj[keep])
    theta -= theta[net.bus_index(net.ref_bus)]  # report angles relative to the ref bus

    flow = branch_flows_mw(net, theta)
    pct = loading_pct(net, flow)
    gen_v = []
    for j, g in enumerate(net.gen_ids):
        lo, hi = (net.pmin[j], net.pmax[j]) if net.gen_status[j] else (0.0, 0.0)
        if p_final[j] < lo - LIMIT_TOL_MW or p_final[j] > hi + LIMIT_TOL_MW:
            gen_v.append({"gen": g, "pg_mw": float(p_final[j]), "pmin_mw": float(lo), "pmax_mw": float(hi)})
    line_v = []
    for j, l in enumerate(net.line_ids):
        if net.br_status[j] and net.rate_a[j] > 0 and abs(flow[j]) > net.rate_a[j] + LIMIT_TOL_MW:
            line_v.append({"line": l, "flow_mw": float(flow[j]), "rate_mw": float(net.rate_a[j]),
                           "loading_pct": float(pct[j])})
    ok = not gen_v and not line_v
    out.update(
        status="ok",
        slack=net.gen_ids[k],
        mismatch_mw=float(mismatch),
        pg_final={g: float(v) for g, v in zip(net.gen_ids, p_final)},
        theta={int(n): float(v) for n, v in zip(net.bus_num, theta)},
        flow={l: float(v) for l, v in zip(net.line_ids, flow)},
        loading_pct={l: (None if np.isnan(v) else float(v)) for l, v in zip(net.line_ids, pct)},
        max_loading_pct=float(np.nanmax(pct)) if np.any(~np.isnan(pct)) else 0.0,
        gen_violations=gen_v,
        line_violations=line_v,
        cost=generation_cost(net, p_final),
        feasible=ok,
        feasible_strict=ok and abs(mismatch) <= BALANCE_TOL_MW,
    )
    return out
