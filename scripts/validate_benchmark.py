"""Validate the human-written benchmark and print a readable report.

Usage: python scripts/validate_benchmark.py [--requests PATH] [--truth PATH]
Defaults: data/requests.jsonl and data/ground_truth.jsonl. Read-only: it never
changes the benchmark files.

Impossible items have no edits, so to PROVE they are impossible add an optional
"probe_edits" list to the ground-truth line: the edits the request literally
asks for. The validator runs the checker (and then the DC-OPF) on them.
"""
import argparse
import json

import _common  # noqa: F401
from _common import CASE, REQUESTS, TRUTH

from src.checker import blocking, check
from src.dcopf import solve_dcopf
from src.edits import EditSet, ParseError, apply_edits, editset_from_obj
from src.evaluate import GROUPS, TARGET_COUNTS, gt_editset, read_jsonl
from src.network import load_case


def validate(requests_path, truth_path) -> tuple[list[str], int]:
    """Return (report lines, number of flags)."""
    net = load_case(str(CASE))
    out, flags = [], 0

    def flag(msg):
        nonlocal flags
        flags += 1
        out.append(f"  FLAG  {msg}")

    try:
        reqs, truth = read_jsonl(requests_path), read_jsonl(truth_path)
    except (OSError, ValueError) as exc:
        return [f"cannot read benchmark: {exc}"], 1
    out.append(f"BENCHMARK REPORT  requests: {requests_path}  ground truth: {truth_path}")
    out.append(f"{len(reqs)} requests, {len(truth)} ground-truth items")

    out.append("\n[1] ids")
    rid = [r.get("id") for r in reqs]
    tid = [t.get("id") for t in truth]
    for name, ids in (("requests", rid), ("ground truth", tid)):
        dups = sorted({i for i in ids if ids.count(i) > 1})
        if dups:
            flag(f"duplicate ids in {name}: {dups}")
    for i in sorted(set(rid) - set(tid)):
        flag(f"{i}: request has no ground truth")
    for i in sorted(set(tid) - set(rid)):
        flag(f"{i}: ground truth has no request")
    if set(rid) == set(tid) and len(rid) == len(set(rid)):
        out.append("  ok    every request has exactly one ground truth")

    out.append("\n[2] groups (target 10/10/8/6/6)")
    for r in reqs:
        if r.get("group") not in GROUPS:
            flag(f"{r.get('id')}: invalid group {r.get('group')!r}; allowed: {', '.join(GROUPS)}")
    counts = {g: sum(r.get("group") == g for r in reqs) for g in GROUPS}
    for g in GROUPS:
        mark = "ok   " if counts[g] == TARGET_COUNTS[g] else "FLAG "
        if counts[g] != TARGET_COUNTS[g]:
            flags += 1
        out.append(f"  {mark} {g:<11}{counts[g]:>3} / {TARGET_COUNTS[g]}")

    out.append("\n[3] request text")
    texts = [r.get("text", "") for r in reqs]
    for r in reqs:
        n = len(str(r.get("text", "")).split())
        if n < 4:
            flag(f"{r.get('id')}: text has only {n} word(s)")
        if texts.count(r.get("text", "")) > 1:
            flag(f"{r.get('id')}: exact duplicate text")

    out.append("\n[4] ground-truth answers")
    group_of = {r.get("id"): r.get("group") for r in reqs}
    for t in truth:
        i = t.get("id")
        es = gt_editset(t)
        if isinstance(es, ParseError):
            flag(f"{i}: edits do not parse: {es.message}")
            continue
        if group_of.get(i) == "impossible" or t.get("refuse"):
            if group_of.get(i) != "impossible":
                flag(f"{i}: refuse is true but the group is {group_of.get(i)!r}")
            if not t.get("refuse") or t.get("edits"):
                flag(f"{i}: impossible items need refuse = true and no edits")
            out.append(f"  {i}  impossible: {prove_impossible(net, t)}")
            if "NOT ACTUALLY IMPOSSIBLE" in out[-1] or "cannot verify" in out[-1]:
                flags += 1
            continue
        issues = blocking(check(net, es))
        if issues:
            flag(f"{i}: fails the checker: " + " | ".join(f"{x.code}: {x.message}" for x in issues))
            continue
        res = solve_dcopf(apply_edits(net, es))
        if res["status"] != "optimal":
            flag(f"{i}: DC-OPF is {res['status']} after the edits")
        else:
            out.append(f"  ok    {i}  cost {res['cost']:.1f} $/h, max loading {res['max_loading_pct']:.1f}%")
    out.append(f"\nFLAGS: {flags}")
    return out, flags


def prove_impossible(net, item) -> str:
    """Which checker rule or solver result shows the probe edits are impossible."""
    probe = item.get("probe_edits")
    if probe is None:
        return "cannot verify: add \"probe_edits\" (the edits the request asks for)"
    es = editset_from_obj({"edits": probe, "refuse": False, "reason": ""})
    issues = blocking(check(net, es))
    if issues:
        return "proven by " + "; ".join(f"{x.code} ({x.message})" for x in issues)
    assert isinstance(es, EditSet)
    res = solve_dcopf(apply_edits(net, es))
    if res["status"] != "optimal":
        return f"proven by R10_INFEASIBLE (DC-OPF {res['status']})"
    return f"NOT ACTUALLY IMPOSSIBLE: probe edits pass every rule and the DC-OPF solves (cost {res['cost']:.1f} $/h)"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", default=str(REQUESTS))
    ap.add_argument("--truth", default=str(TRUTH))
    a = ap.parse_args()
    lines, n = validate(a.requests, a.truth)
    print("\n".join(lines))
    raise SystemExit(1 if n else 0)
