"""Scoring and aggregation. This is the only module that reads ground truth.

Every dispatch (Mode 1: the LLM's MW values; Modes 2-4: the OPF result on the
LLM's edits) is judged with a DC power flow on the network AFTER the
ground-truth edits, i.e. against the real situation. MW, $/h, %.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.dcopf import solve_dcopf
from src.dcpf import dc_power_flow
from src.edits import ParseError, apply_edits, canonical_diff, editset_from_obj
from src.network import Network

GROUPS = ["outage", "gen", "load", "combined", "impossible"]
TARGET_COUNTS = {"outage": 10, "gen": 10, "load": 8, "combined": 6, "impossible": 6}


def read_jsonl(path) -> list[dict]:
    rows = []
    with open(path) as f:
        for n, line in enumerate(f, start=1):
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path} line {n}: invalid JSON ({exc.msg})") from exc
    return rows


def gt_editset(item: dict):
    """Ground-truth item -> EditSet (or ParseError)."""
    return editset_from_obj({"edits": item.get("edits", []), "refuse": item.get("refuse", False),
                             "reason": item.get("note", "") if item.get("refuse") else ""})


def ground_truth_solution(net: Network, item: dict) -> dict:
    """Apply a non-impossible ground-truth item and solve the DC-OPF.

    Returns {id, status, cost ($/h), pg {G: MW}, max_loading_pct, binding,
    canonical_diff}; impossible items get status 'refuse'.
    """
    if item.get("refuse"):
        return {"id": item["id"], "status": "refuse", "canonical_diff": {}}
    es = gt_editset(item)
    if isinstance(es, ParseError):
        return {"id": item["id"], "status": "bad_ground_truth", "error": es.message}
    edited = apply_edits(net, es)
    res = solve_dcopf(edited)
    return {"id": item["id"], "status": res["status"], "cost": res.get("cost"), "pg": res.get("pg"),
            "max_loading_pct": res.get("max_loading_pct"), "binding": res.get("binding"),
            "canonical_diff": canonical_diff(net, edited)}


def mock_answers(net: Network, truth: list[dict], mode: int) -> dict[str, str]:
    """Replies a perfect agent would give (used by MockLLM for pipeline checks)."""
    out = {}
    for item in truth:
        if item.get("refuse"):
            reason = item.get("note") or "cannot be done safely"
            obj = {"refuse": True, "reason": reason} if mode == 1 else {"edits": [], "refuse": True, "reason": reason}
        elif mode == 1:
            sol = ground_truth_solution(net, item)
            obj = {**(sol.get("pg") or {}), "refuse": False, "reason": ""}
        else:
            obj = {"edits": item["edits"], "refuse": False, "reason": ""}
        out[item["id"]] = json.dumps(obj)
    return out


def score_record(rec: dict, item: dict, net: Network, gt_cache: dict | None = None) -> dict:
    """Score one agent record against its ground-truth item.

    Fields: impossible, refused, edit_correct (Modes 2-4; None for Mode 1),
    feasible (dispatch passes all limits on the true network after the slack
    unit absorbs any mismatch; None for impossible items), feasible_strict
    (also |mismatch| <= 1e-4 MW), mismatch_mw, cost_gap_pct, false_refusal,
    success.
    """
    gt_cache = {} if gt_cache is None else gt_cache
    impossible = bool(item.get("refuse"))
    refused = bool(rec.get("refuse")) and rec.get("outcome") == "refused"
    out = {"id": rec["id"], "group": item.get("group", rec.get("group")), "mode": rec["mode"],
           "backend": rec.get("backend"), "model": rec.get("model"), "repeat": rec.get("repeat"),
           "outcome": rec.get("outcome"), "impossible": impossible, "refused": refused,
           "retries_used": rec.get("retries_used", 0), "latency_s": rec.get("latency_s"),
           "tokens_in": rec.get("tokens_in"), "tokens_out": rec.get("tokens_out"),
           "energy_kwh": rec.get("energy_kwh"), "extra_changes": len(rec.get("extra_changes") or [])}

    if impossible:
        out.update(edit_correct=None if rec["mode"] == 1 else refused, feasible=None, feasible_strict=None,
                   mismatch_mw=None, cost_gap_pct=None, false_refusal=False, success=refused)
        return out

    if item["id"] not in gt_cache:
        es = gt_editset(item)
        gt_net = apply_edits(net, es)
        gt_cache[item["id"]] = (gt_net, canonical_diff(net, gt_net), solve_dcopf(gt_net))
    gt_net, gt_diff, gt_sol = gt_cache[item["id"]]

    if rec["mode"] == 1:
        edit_correct = None
    else:
        edit_correct = (not refused) and rec.get("outcome") in ("solved", "VERIFY_FAILED", "INFEASIBLE") \
            and rec.get("canonical_diff") == gt_diff

    pg = rec.get("pg")
    has_dispatch = bool(pg) and not refused and rec.get("outcome") in ("solved", "dispatched", "VERIFY_FAILED")
    feasible = strict = False
    mismatch = gap = None
    if has_dispatch:
        pf = dc_power_flow(gt_net, pg)
        feasible, strict = bool(pf["feasible"]), bool(pf["feasible_strict"])
        mismatch = pf.get("mismatch_mw")
        if pf.get("status") == "ok" and gt_sol.get("status") == "optimal" and gt_sol["cost"]:
            gap = (pf["cost"] - gt_sol["cost"]) / gt_sol["cost"] * 100.0
    success = feasible and (edit_correct if rec["mode"] != 1 else True)
    out.update(edit_correct=edit_correct, feasible=feasible, feasible_strict=strict, mismatch_mw=mismatch,
               cost_gap_pct=gap, false_refusal=refused, success=bool(success))
    return out


def load_runs(runs_dir="results/runs") -> list[dict]:
    """All records from results/runs/<backend>_m<k>_r<n>.jsonl, with repeat set."""
    recs = []
    for p in sorted(Path(runs_dir).glob("*_m*_r*.jsonl")):
        rep = int(p.stem.rsplit("_r", 1)[1])
        for r in read_jsonl(p):
            r.setdefault("repeat", rep)
            recs.append(r)
    return recs


def score_all(records: list[dict], truth: list[dict], net: Network) -> pd.DataFrame:
    """Score every record whose id has a ground-truth item. One row per record."""
    by_id = {t["id"]: t for t in truth}
    cache: dict = {}
    return pd.DataFrame([score_record(r, by_id[r["id"]], net, cache) for r in records if r["id"] in by_id])


def _metrics(g: pd.DataFrame) -> dict:
    """Metrics for one (backend, mode, repeat) slice."""
    doable = g[~g["impossible"]]
    imp = g[g["impossible"]]
    ec = g["edit_correct"].dropna()
    e = g["energy_kwh"].dropna()
    return {
        "edit_accuracy": float(ec.astype(float).mean() * 100) if len(ec) else np.nan,
        "feasibility_pct": float(doable["feasible"].astype(float).mean() * 100) if len(doable) else np.nan,
        "feasibility_strict_pct": float(doable["feasible_strict"].astype(float).mean() * 100) if len(doable) else np.nan,
        "median_cost_gap_pct": float(doable["cost_gap_pct"].dropna().median()) if doable["cost_gap_pct"].notna().any() else np.nan,
        "refusal_accuracy": float(imp["refused"].astype(float).mean() * 100) if len(imp) else np.nan,
        "false_refusal_pct": float(doable["false_refusal"].astype(float).mean() * 100) if len(doable) else np.nan,
        "success_pct": float(g["success"].astype(float).mean() * 100),
        "mean_retries": float(g["retries_used"].mean()),
        "latency_s": float(g["latency_s"].mean()),
        "tokens_per_decision": float((g["tokens_in"].fillna(0) + g["tokens_out"].fillna(0)).mean()),
        "energy_kwh_per_decision": float(e.mean()) if len(e) else np.nan,
        "n": int(len(g)),
    }


METRICS = ["edit_accuracy", "feasibility_pct", "feasibility_strict_pct", "median_cost_gap_pct",
           "refusal_accuracy", "false_refusal_pct", "success_pct", "mean_retries", "latency_s",
           "tokens_per_decision", "energy_kwh_per_decision"]


def aggregate(df: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """Mean and std over repeats of the per-repeat metrics, grouped by `by`."""
    per_rep = []
    for keys, g in df.groupby(by + ["repeat"], dropna=False):
        keys = keys if isinstance(keys, tuple) else (keys,)
        per_rep.append({**dict(zip(by + ["repeat"], keys)), **_metrics(g)})
    pr = pd.DataFrame(per_rep)
    rows = []
    for keys, g in pr.groupby(by, dropna=False):
        keys = keys if isinstance(keys, tuple) else (keys,)
        row = dict(zip(by, keys))
        row["repeats"] = len(g)
        for m in METRICS:
            row[f"{m}_mean"] = g[m].mean()
            row[f"{m}_std"] = g[m].std(ddof=0) if len(g) > 1 else 0.0
        rows.append(row)
    return pd.DataFrame(rows)


def to_markdown(df: pd.DataFrame, digits: int = 2) -> str:
    """Plain markdown table (no extra package). mean/std pairs are shown as 'm ± s'."""
    cols, show = [], {}
    for c in df.columns:
        if c.endswith("_std"):
            continue
        if c.endswith("_mean"):
            base = c[:-5]
            std = df.get(f"{base}_std")
            show[base] = [_pm(m, s, digits) for m, s in zip(df[c], std if std is not None else [None] * len(df))]
            cols.append(base)
        else:
            show[c] = [str(v) for v in df[c]]
            cols.append(c)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for i in range(len(df)):
        lines.append("| " + " | ".join(show[c][i] for c in cols) + " |")
    return "\n".join(lines) + "\n"


def _pm(m, s, d):
    if m is None or (isinstance(m, float) and np.isnan(m)):
        return "n/a"
    fmt = f"{{:.{d}g}}" if abs(m) < 0.01 and m != 0 else f"{{:.{d}f}}"
    return fmt.format(m) if s is None or s == 0 or np.isnan(s) else f"{fmt.format(m)} ± {fmt.format(s)}"


def write_tables(df: pd.DataFrame, out_dir="results/tables") -> dict:
    """Save by-mode and by-group tables as CSV and markdown. Returns the frames."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    by_mode = aggregate(df, ["backend", "model", "mode"])
    by_group = aggregate(df, ["backend", "mode", "group"])
    for name, t in (("by_mode", by_mode), ("by_group", by_group)):
        t.to_csv(out / f"{name}.csv", index=False)
        (out / f"{name}.md").write_text(to_markdown(t))
    return {"by_mode": by_mode, "by_group": by_group}
