"""Write data/benchmark_worksheet.csv: 40 scenario rows for the researcher to word.

Each row gives the scenario (plain description), the ground-truth edits, a style
tag for the wording, and, for impossible items, the probe edits. The columns
`text` (the operator request) and `note` (why the answer is right) are left
EMPTY: the researcher writes them. Every scenario is checked here with the
checker and the DC-OPF before the file is written.

Usage: python scripts/make_worksheet.py [--force]
"""
import argparse
import csv
import json
import os

import _common  # noqa: F401
from _common import CASE, ROOT

from src.checker import blocking, check
from src.dcopf import solve_dcopf
from src.edits import apply_edits, editset_from_obj
from src.network import load_case

OUT = ROOT / "data" / "benchmark_worksheet.csv"
COLUMNS = ["id", "group", "scenario", "style", "edits", "probe_edits", "text", "note"]


def lo(line, st="out"):
    return {"action": "set_line_status", "line": line, "status": st}


def rate(line, mw):
    return {"action": "set_line_rating", "line": line, "rate_mw": mw}


def lim(gen, pmax=None, pmin=None):
    return {"action": "set_gen_limits", "gen": gen, "pmax_mw": pmax, "pmin_mw": pmin}


def gst(gen, st):
    return {"action": "set_gen_status", "gen": gen, "status": st}


def setl(bus, mw):
    return {"action": "set_load", "bus": bus, "pd_mw": mw}


def scl(bus, f):
    return {"action": "scale_load", "bus": bus, "factor": f}


def cost(gen, f):
    return {"action": "scale_cost", "gen": gen, "factor": f}


# (group, scenario, style, edits, probe_edits)
ROWS = [
    # ---- outage (10): all doable
    ("outage", "L7 (bus 4 to bus 6) trips", "name it by id L7, formal control-room tone", [lo("L7")], None),
    ("outage", "L8 (bus 5 to bus 7) trips", "end buses only, casual", [lo("L8")], None),
    ("outage", "L9 (bus 6 to bus 7) is out", "end buses, formal", [lo("L9")], None),
    ("outage", "L3 (bus 2 to bus 4) taken out", "by id, give a reason (e.g. protection operated)", [lo("L3")], None),
    ("outage", "L15 transformer between bus 4 and bus 12 out", "call it a transformer, end buses", [lo("L15")], None),
    ("outage", "L36 transformer 28-27 out", "end buses in REVERSE order (27 and 28)", [lo("L36")], None),
    ("outage", "L22 (15-18) out for planned maintenance", "planned work, mention a time", [lo("L22")], None),
    ("outage", "L41 (6-28) is out", "very short, casual, no punctuation", [lo("L41")], None),
    ("outage", "L5 (bus 2 to bus 5) is out", "by id, written like a log entry", [lo("L5")], None),
    ("outage", "L12 transformer 6-10 out", "end buses, mention 'transformer'", [lo("L12")], None),
    # ---- gen (10)
    ("gen", "G1 derated to 75% of its 271 MW (= 203.25 MW)", "give it as a percent", [lim("G1", 203.25)], None),
    ("gen", "G1 limited to 210 MW", "give it in MW", [lim("G1", 210)], None),
    ("gen", "G2 must run at least 80 MW (Pmin 80)", "must-run / minimum output wording", [lim("G2", None, 80)], None),
    ("gen", "G1 fuel cost up 50% (cost x1.5)", "fuel price wording, percent", [cost("G1", 1.5)], None),
    ("gen", "G2 cost halved (x0.5)", "commercial wording (cheaper contract)", [cost("G2", 0.5)], None),
    ("gen", "G2 derated to 75% of 92 MW (= 69 MW)", "percent, name the unit by its bus (bus 2)", [lim("G2", 69)], None),
    ("gen", "G1 cost tripled (x3)", "casual", [cost("G1", 3)], None),
    ("gen", "G3 (synchronous condenser at bus 5) out of service", "name it by bus, 'the unit at bus 5'", [gst("G3", "out")], None),
    ("gen", "G2 minimum output 30 MW (Pmin 30)", "formal", [lim("G2", None, 30)], None),
    ("gen", "G1 Pmax 240 MW and Pmin 50 MW", "two limits in one sentence", [lim("G1", 240, 50)], None),
    # ---- load (8)
    ("load", "bus 7 load up 15% (x1.15)", "percent rise", [scl(7, 1.15)], None),
    ("load", "bus 5 load up 10% (x1.1)", "percent rise, mention weather", [scl(5, 1.1)], None),
    ("load", "bus 21 load is now 25 MW (absolute)", "absolute MW value", [setl(21, 25)], None),
    ("load", "bus 8 load down 20% (x0.8)", "percent drop", [scl(8, 0.8)], None),
    ("load", "bus 30 load up by 5 MW (10.6 -> 15.6 MW)", "relative MW ('5 MW more')", [setl(30, 15.6)], None),
    ("load", "bus 12 and bus 14 loads both up 10%", "several buses in one sentence", [scl(12, 1.1), scl(14, 1.1)], None),
    ("load", "bus 2 load halved (x0.5)", "'half' wording", [scl(2, 0.5)], None),
    ("load", "bus 19 load disconnected (0 MW)", "customer disconnected / load shed", [setl(19, 0)], None),
    # ---- combined (6)
    ("combined", "L15 transformer (4-12) out AND bus 21 load +10 MW (17.5 -> 27.5 MW)", "two changes, one sentence", [lo("L15"), setl(21, 27.5)], None),
    ("combined", "G1 derated to 75% (203.25 MW) AND bus 5 load +5%", "percent for both", [lim("G1", 203.25), scl(5, 1.05)], None),
    ("combined", "L9 out AND G2 cost up 50%", "end buses for the line", [lo("L9"), cost("G2", 1.5)], None),
    ("combined", "L36 out, bus 30 load +20%, L41 derated to 100 MW", "three changes, list style", [lo("L36"), scl(30, 1.2), rate("L41", 100)], None),
    ("combined", "G2 must-run 80 MW AND L8 out", "casual", [lim("G2", None, 80), lo("L8")], None),
    ("combined", "bus 7 and bus 8 loads +15% AND L3 out", "three changes, formal", [scl(7, 1.15), scl(8, 1.15), lo("L3")], None),
    # ---- impossible (6): one rule each
    ("impossible", "take line L45 out (does not exist; only L1-L41)", "by id, sounds routine", [], [lo("L45")]),
    ("impossible", "run G1 at 300 MW (nameplate 271 MW)", "MW value, urgent tone", [], [lim("G1", 300, 300)]),
    ("impossible", "raise the rating of L1 (1-2) to 180 MW (original 138)", "operator wants more transfer", [], [rate("L1", 180)]),
    ("impossible", "take the line 25-26 (L34) out: islands bus 26", "end buses, maintenance request", [], [lo("L34")]),
    ("impossible", "G2 trips completely (271 MW left for 283.4 MW load)", "unit trip report", [], [gst("G2", "out")]),
    ("impossible", "line 1-2 (L1) trips: no feasible dispatch remains", "end buses, trip report", [], [lo("L1")]),
]


def verify(net):
    """Check every row: doable rows pass the checker and solve; impossible rows are proven."""
    out = []
    for i, (group, scen, style, edits, probe) in enumerate(ROWS, start=1):
        if group == "impossible":
            es = editset_from_obj({"edits": probe, "refuse": False, "reason": ""})
            iss = blocking(check(net, es))
            proof = iss[0].code if iss else ("R10_INFEASIBLE" if solve_dcopf(apply_edits(net, es))["status"] != "optimal" else None)
            assert proof, f"row {i} is not actually impossible"
            out.append(proof)
        else:
            es = editset_from_obj({"edits": edits, "refuse": False, "reason": ""})
            iss = blocking(check(net, es))
            assert not iss, f"row {i} fails the checker: {[x.message for x in iss]}"
            r = solve_dcopf(apply_edits(net, es))
            assert r["status"] == "optimal", f"row {i} is infeasible"
            out.append(f"{r['cost']:.0f} $/h")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="overwrite an existing worksheet")
    a = ap.parse_args()
    if OUT.exists() and not a.force:
        raise SystemExit(f"{OUT} exists (it may hold your wording); use --force to overwrite")
    net = load_case(str(CASE))
    results = verify(net)
    counts = {}
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for n, ((group, scen, style, edits, probe), res) in enumerate(zip(ROWS, results), start=1):
            counts[group] = counts.get(group, 0) + 1
            w.writerow({"id": f"R{n:02d}", "group": group,
                        "scenario": f"{scen}  [{res}]", "style": style, "edits": json.dumps(edits),
                        "probe_edits": json.dumps(probe) if probe else "", "text": "", "note": ""})
    print(f"wrote {OUT}: {sum(counts.values())} rows {counts}")


if __name__ == "__main__":
    main()
