"""Print one run as a short graded table (one row per request).

Usage: python scripts/show_run.py api_m2_r1      (file in results/runs/)
Columns: id, group, outcome, edits correct?, feasible on the true grid?,
cost gap vs the true optimum, retries, latency, tokens, and the model's
canonical diff next to the correct one when they differ.
"""
import argparse
import json

import _common  # noqa: F401
from _common import CASE, RESULTS, TRUTH

from src.evaluate import read_jsonl, score_record
from src.network import load_case


def fmt(v):
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "NO"
    return str(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id", help="e.g. api_m2_r1")
    ap.add_argument("--truth", default=str(TRUTH))
    a = ap.parse_args()
    net = load_case(str(CASE))
    truth = {t["id"]: t for t in read_jsonl(a.truth)}
    recs = read_jsonl(RESULTS / "runs" / f"{a.run_id}.jsonl")
    cache: dict = {}
    print(f"{'id':<5}{'group':<11}{'outcome':<17}{'edits ok':<9}{'feasible':<9}{'cost gap':>9}"
          f"{'retries':>8}{'sec':>6}{'tokens':>8}")
    n_ok = n_feas = n = n_doable = 0
    tokens = 0
    for r in recs:
        s = score_record(r, truth[r["id"]], net, cache)
        gap = "-" if s["cost_gap_pct"] is None else f"{s['cost_gap_pct']:+.2f}%"
        tok = (r.get("tokens_in") or 0) + (r.get("tokens_out") or 0)
        tokens += tok
        print(f"{r['id']:<5}{fmt(s['group']):<11}{r['outcome']:<17}{fmt(s['edit_correct']):<9}{fmt(s['feasible']):<9}"
              f"{gap:>9}{r.get('retries_used', 0):>8}{r.get('latency_s', 0):>6.1f}{tok:>8}")
        if s["edit_correct"] is False and r.get("canonical_diff") is not None:
            print(f"      model:   {json.dumps(r['canonical_diff'])}")
            print(f"      correct: {json.dumps(cache[r['id']][1]) if r['id'] in cache else '(refuse)'}")
        n += 1
        n_ok += bool(s["success"])
        n_feas += bool(s["feasible"])
        n_doable += not s["impossible"]
    model = recs[0].get("model") if recs else "-"
    print(f"\n{n} records, model {model}: {n_ok}/{n} fully correct, "
          f"{n_feas}/{n_doable} doable requests feasible, {tokens} tokens in total")


if __name__ == "__main__":
    main()
