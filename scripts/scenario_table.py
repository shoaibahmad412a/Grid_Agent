"""Scenario reference for writing the benchmark: what every single change does.

Tests every line outage, line derates, generator derates/outages/cost changes
and load changes on the 30-bus grid, and reports whether the checker accepts it,
whether the DC-OPF is feasible, the cost change and the binding lines. It does
not write any benchmark item. Prints and saves results/scenario_table.txt.
MW, $/h, %.
"""
import json

import _common  # noqa: F401
from _common import CASE, RESULTS

from src.checker import blocking, check
from src.dcopf import solve_dcopf
from src.edits import apply_edits, parse_edits
from src.network import load_case

net = load_case(str(CASE))
BASE = solve_dcopf(net)


def evaluate(edits):
    """(verdict, detail) for one edit list: checker code, 'infeasible', or cost change."""
    es = parse_edits(json.dumps({"edits": edits, "refuse": False, "reason": ""}))
    issues = blocking(check(net, es))
    if issues:
        return "CHECKER", ",".join(sorted({i.code.split("_", 1)[0] + "_" + i.code.split("_", 1)[1] for i in issues}))
    r = solve_dcopf(apply_edits(net, es))
    if r["status"] != "optimal":
        return "R10", "OPF infeasible"
    d = (r["cost"] - BASE["cost"]) / BASE["cost"] * 100
    return "OK", f"{r['cost']:7.0f} $/h ({d:+5.1f}%)  G1 {r['pg']['G1']:5.1f}  G2 {r['pg']['G2']:5.1f}  binding {','.join(r['binding']) or '-'}"


def row(label, edits):
    v, d = evaluate(edits)
    return f"  {label:<34}{v:<9}{d}"


def max_load_factor(bus):
    """Largest factor (to 0.01, up to 3) for one bus load that keeps the OPF feasible."""
    lo, hi = 1.0, 3.0
    if evaluate([{"action": "scale_load", "bus": bus, "factor": hi}])[0] == "OK":
        return hi
    for _ in range(12):
        mid = (lo + hi) / 2
        if evaluate([{"action": "scale_load", "bus": bus, "factor": mid}])[0] == "OK":
            lo = mid
        else:
            hi = mid
    return lo


def build() -> str:
    L = [f"SCENARIO TABLE  base cost {BASE['cost']:.0f} $/h, G1 {BASE['pg']['G1']:.1f} MW, G2 {BASE['pg']['G2']:.1f} MW, binding {BASE['binding']}",
         "Verdict: OK = doable (feasible DC-OPF); CHECKER = a rule refuses it; R10 = passes rules but no feasible dispatch.",
         "", "1. LINE OUTAGES (every branch)"]
    for k, l in enumerate(net.line_ids):
        L.append(row(f"{l} out ({net.br_from[k]}-{net.br_to[k]})", [{"action": "set_line_status", "line": l, "status": "out"}]))

    L += ["", "2. LINE DERATES (branches loaded above 40% in the base case)"]
    for k, l in enumerate(net.line_ids):
        pct = BASE["loading_pct"][l] or 0
        if pct < 40:
            continue
        for f in (0.9, 0.75, 0.5):
            mw = round(net.rate_a[k] * f, 1)
            L.append(row(f"{l} ({net.br_from[k]}-{net.br_to[k]}) {net.rate_a[k]:g} -> {mw:g} MW", [{"action": "set_line_rating", "line": l, "rate_mw": mw}]))

    L += ["", "3. GENERATORS"]
    for g in ("G1", "G2"):
        k = net.gen_index(g)
        for f in (0.9, 0.75, 0.6, 0.5):
            mw = round(net.pmax_file[k] * f, 2)
            L.append(row(f"{g} Pmax {net.pmax_file[k]:g} -> {mw:g} MW ({f:.0%})", [{"action": "set_gen_limits", "gen": g, "pmax_mw": mw, "pmin_mw": None}]))
        L.append(row(f"{g} out of service", [{"action": "set_gen_status", "gen": g, "status": "out"}]))
        for f in (0.5, 1.5, 3.0):
            L.append(row(f"{g} cost x{f:g}", [{"action": "scale_cost", "gen": g, "factor": f}]))
    for g, mw in (("G1", 50), ("G2", 30), ("G2", 80)):
        L.append(row(f"{g} Pmin -> {mw} MW (must-run)", [{"action": "set_gen_limits", "gen": g, "pmax_mw": None, "pmin_mw": mw}]))
    L.append(row("G3 out (condenser, 0 MW)", [{"action": "set_gen_status", "gen": "G3", "status": "out"}]))
    L.append(row("G1 Pmax 300 MW (above nameplate)", [{"action": "set_gen_limits", "gen": "G1", "pmax_mw": 300, "pmin_mw": None}]))

    L += ["", "4. LOADS (every bus with load): cost at +10% and +25%, and the largest feasible factor"]
    for i, b in enumerate(net.bus_num):
        if net.pd[i] == 0:
            continue
        b = int(b)
        v10 = evaluate([{"action": "scale_load", "bus": b, "factor": 1.1}])
        v25 = evaluate([{"action": "scale_load", "bus": b, "factor": 1.25}])
        mf = max_load_factor(b)
        cost = lambda v: v[1].split("(")[1].split(")")[0] if v[0] == "OK" else v[0]
        L.append(f"  bus {b:<3} {net.pd[i]:6.1f} MW   +10%: {cost(v10):<8} +25%: {cost(v25):<8} max factor {mf:.2f} "
                 f"(= {net.pd[i] * mf:.1f} MW, +{(mf - 1) * net.pd[i]:.1f} MW)")
    total = net.total_load_mw()
    L.append(f"  system: load {total:.1f} MW, capacity {net.total_capacity_mw():.1f} MW, headroom {net.total_capacity_mw() - total:.1f} MW")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    text = build()
    print(text)
    (RESULTS / "scenario_table.txt").write_text(text)
    print(f"saved {RESULTS / 'scenario_table.txt'}")
