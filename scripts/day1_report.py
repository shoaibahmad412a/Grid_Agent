"""Day 1 report: grid facts, base-case DC-OPF, bridges and the id mapping.

Prints and saves results/day1_report.txt. MW, $/h, $/MWh, %.
"""
import _common  # noqa: F401
from _common import CASE, RESULTS

import networkx as nx

from src.checker import bridge_branches
from src.dcopf import solve_dcopf
from src.network import load_case


def build_report() -> str:
    net = load_case(str(CASE))
    r = solve_dcopf(net)
    L = []
    w = L.append
    w("DAY 1 REPORT: pglib_opf_case30_ieee, DC model")
    w("=" * 60)
    w(f"baseMVA: {net.baseMVA:g}   reference bus: {net.ref_bus}")
    w(f"buses: {net.n_bus}   generators: {net.n_gen}   branches: {net.n_branch} "
      f"({int(net.br_xfmr.sum())} transformers)")

    w("\nGENERATORS")
    w(f"{'id':<4}{'bus':>4}{'status':>8}{'Pmin MW':>9}{'Pmax MW':>9}{'c2':>8}{'c1 $/MWh':>10}{'c0 $/h':>8}")
    for k, g in enumerate(net.gen_ids):
        w(f"{g:<4}{net.gen_bus[k]:>4}{net.gen_status[k]:>8}{net.pmin[k]:>9.1f}{net.pmax[k]:>9.1f}"
          f"{net.c2[k]:>8.3g}{net.c1[k]:>10.3f}{net.c0[k]:>8.1f}")

    w("\nBRANCHES (RATE_A = 0 means no limit)")
    w(f"{'id':<5}{'from':>5}{'to':>4}{'x p.u.':>8}{'tap':>7}{'shift':>6}{'RATE_A':>8}  flag")
    for k, l in enumerate(net.line_ids):
        flag = ("NO LIMIT " if net.rate_a[k] == 0 else "") + ("transformer" if net.br_xfmr[k] else "")
        w(f"{l:<5}{net.br_from[k]:>5}{net.br_to[k]:>4}{net.br_x[k]:>8.4f}{net.br_tap[k]:>7.3f}"
          f"{net.br_shift[k]:>6.1f}{net.rate_a[k]:>8.0f}  {flag}")
    w(f"branches with RATE_A = 0: {[l for k, l in enumerate(net.line_ids) if net.rate_a[k] == 0] or 'none'}")

    load, cap = net.total_load_mw(), net.total_capacity_mw()
    w(f"\nTotal load: {load:.1f} MW   total capacity: {cap:.1f} MW   "
      f"reserve margin: {(cap - load) / load * 100:.1f}%")

    w("\nBASE-CASE DC-OPF")
    w(f"status: {r['status']}   solver: {r['solver']}   solve time: {r['solve_time_s'] * 1000:.0f} ms")
    w(f"cost: {r['cost']:.2f} $/h   balance residual: {r['balance_residual_mw']:.2e} MW")
    w("Pg: " + ", ".join(f"{g} {v:.2f} MW" for g, v in r["pg"].items()))
    w(f"total generation: {sum(r['pg'].values()):.2f} MW (load {load:.2f} MW)")
    top = sorted(((v, l) for l, v in r["loading_pct"].items() if v is not None), reverse=True)[:5]
    w("top 5 loaded branches: " + ", ".join(f"{l} {v:.1f}% ({r['flow'][l]:+.1f} MW)" for v, l in top))
    w(f"binding branches (>= 99.9%): {r['binding'] or 'none'}")
    lmp = r["lmp"]
    w(f"LMP min: {min(lmp.values()):.2f} $/MWh (bus {min(lmp, key=lmp.get)})   "
      f"max: {max(lmp.values()):.2f} $/MWh (bus {max(lmp, key=lmp.get)})")
    w("LMP by bus: " + ", ".join(f"{b}: {v:.2f}" for b, v in lmp.items()))

    w("\nBRIDGE BRANCHES (an outage islands buses; networkx bridges)")
    g = nx.Graph()
    g.add_edges_from((int(f), int(t)) for f, t in zip(net.br_from, net.br_to))
    bridges = {frozenset(e) for e in nx.bridges(g)}
    for b in bridge_branches(net):
        assert frozenset((b["from"], b["to"])) in bridges
        w(f"{b['line']} ({b['from']}-{b['to']}): would cut off bus(es) {b['cut_off']}")
    assert len(bridges) == len(bridge_branches(net)), "bridge check disagrees with networkx"

    w("\nID MAPPING (operator id -> internal 0-based index)")
    w("buses: " + ", ".join(f"{b}->{i}" for i, b in enumerate(net.bus_num)))
    w("generators: " + ", ".join(f"{g}->{k} (bus {net.gen_bus[k]})" for k, g in enumerate(net.gen_ids)))
    w("branches: " + ", ".join(f"{l}->{k}" for k, l in enumerate(net.line_ids)))
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    text = build_report()
    print(text)
    (RESULTS).mkdir(exist_ok=True)
    (RESULTS / "day1_report.txt").write_text(text, encoding="utf-8")
    print(f"saved {RESULTS / 'day1_report.txt'}")
