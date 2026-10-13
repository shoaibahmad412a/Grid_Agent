"""Export one row per failed record to results/failures.csv with an empty my_label column.

The my_label column belongs to the researcher: this script never fills it and
refuses to overwrite a failures.csv that already has labels.
Usage: python scripts/export_failures.py [--truth PATH] [--requests PATH] [--out PATH]
"""
import argparse
import csv
import json
import os

import _common  # noqa: F401
from _common import CASE, REQUESTS, RESULTS, TRUTH

from src.dcpf import dc_power_flow
from src.evaluate import load_runs, read_jsonl, score_record
from src.network import load_case

COLUMNS = ["backend", "model", "mode", "repeat", "request_id", "group", "request_text", "outcome",
           "raw_reply", "refusal_reason", "checker_messages", "canonical_diff", "expected", "violations",
           "my_label"]
# Helper columns for labelling (filled by the script, never a label):
#   expected   - the ground-truth effect on the grid (canonical diff), or REFUSE
#   violations - for a dispatch, the limits it breaks on the TRUE grid (lines in MW, units in MW)


def violations(rec, item, cache) -> str:
    """Limits a dispatch breaks on the true grid, e.g. 'L1 179.3/138 MW; G1 283.4 > max 271 MW'."""
    if item.get("refuse") or not rec.get("pg") or rec["id"] not in cache:
        return ""
    pf = dc_power_flow(cache[rec["id"]][0], rec["pg"])
    if pf.get("status") != "ok":
        return pf.get("status", "")
    parts = [f"{v['line']} {abs(v['flow_mw']):.1f}/{v['rate_mw']:g} MW" for v in pf["line_violations"]]
    parts += [f"{v['gen']} {v['pg_mw']:.1f} MW outside {v['pmin_mw']:g}-{v['pmax_mw']:g}" for v in pf["gen_violations"]]
    return "; ".join(parts) or "none"


def failure_rows(records, truth, requests, net):
    by_id = {t["id"]: t for t in truth}
    text = {r["id"]: r for r in requests}
    cache, rows = {}, []
    for rec in records:
        if rec["id"] not in by_id:
            continue
        s = score_record(rec, by_id[rec["id"]], net, cache)
        if s["success"]:
            continue
        msgs = [i["message"] for a in rec.get("attempts", []) for i in a.get("issues", [])]
        item = by_id[rec["id"]]
        expected = "REFUSE" if item.get("refuse") else json.dumps(cache[rec["id"]][1])
        rows.append({
            "backend": rec.get("backend"), "model": rec.get("model"), "mode": rec["mode"],
            "repeat": rec.get("repeat"), "request_id": rec["id"],
            "group": text.get(rec["id"], {}).get("group", rec.get("group")),
            "request_text": text.get(rec["id"], {}).get("text", ""), "outcome": rec.get("outcome"),
            "raw_reply": rec.get("raw_reply"), "checker_messages": " | ".join(msgs),
            "canonical_diff": json.dumps(rec.get("canonical_diff")) if rec.get("canonical_diff") is not None else "",
            "refusal_reason": rec.get("reason", "") if rec.get("refuse") else "",
            "expected": expected,
            "violations": violations(rec, item, cache),
            "my_label": "",
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--truth", default=str(TRUTH))
    ap.add_argument("--requests", default=str(REQUESTS))
    ap.add_argument("--runs", default=str(RESULTS / "runs"))
    ap.add_argument("--out", default=str(RESULTS / "failures.csv"))
    a = ap.parse_args()
    if os.path.exists(a.out):
        with open(a.out, encoding="utf-8-sig") as f:
            if any((r.get("my_label") or "").strip() for r in csv.DictReader(f)):
                raise SystemExit(f"{a.out} already has labels in my_label; not overwriting it.")
    rows = failure_rows(load_runs(a.runs), read_jsonl(a.truth), read_jsonl(a.requests), load_case(str(CASE)))
    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} failed records written to {a.out}")


if __name__ == "__main__":
    main()
