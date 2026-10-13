"""Solve the DC-OPF for every ground-truth item and save the optimum.

Usage: python scripts/build_ground_truth.py [--truth PATH] [--out PATH]
Writes results/ground_truth_solutions.jsonl: id, status, cost ($/h), Pg (MW),
max loading (%), binding lines, canonical diff. Impossible items: status 'refuse'.
"""
import argparse
import json

import _common  # noqa: F401
from _common import CASE, RESULTS, TRUTH

from src.evaluate import ground_truth_solution, read_jsonl
from src.network import load_case

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--truth", default=str(TRUTH))
    ap.add_argument("--out", default=str(RESULTS / "ground_truth_solutions.jsonl"))
    a = ap.parse_args()
    net = load_case(str(CASE))
    rows = [ground_truth_solution(net, t) for t in read_jsonl(a.truth)]
    with open(a.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    for r in rows:
        cost = f"{r['cost']:.1f} $/h" if r.get("cost") is not None else ""
        print(f"{r['id']:<6}{r['status']:<12}{cost:<14}{r.get('canonical_diff')}")
    print(f"saved {a.out}")
