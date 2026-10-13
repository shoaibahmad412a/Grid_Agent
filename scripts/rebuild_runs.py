"""Rebuild graded run files (results/runs/) from raw call logs (results/raw/).

The agent is deterministic once the LLM replies are fixed, so replaying the
logged replies (in attempt order) through the same agent code reproduces each
record exactly, without any new API call. Latency, tokens and model name are
taken from the log. Use it when only the raw logs are available.

Usage: python scripts/rebuild_runs.py [--raw DIR] [--out DIR] [--check scored.csv]
--check compares outcome/success/edit_correct/feasible/refused with an existing
scored.csv and reports any mismatch.
"""
import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

import _common  # noqa: F401
from _common import CASE, REQUESTS, RESULTS, TRUTH

import pandas as pd

from src.dcopf import solve_dcopf
from src.evaluate import read_jsonl, score_all
from src.llm import LLM, _meta
from src.network import load_case
from run_all import run_one


class ReplayLLM(LLM):
    """Returns the logged replies of one request in attempt order."""
    name = "api"

    def __init__(self, entries: list[dict]):
        self.entries = sorted(entries, key=lambda e: e.get("attempt", 1))
        self.model = self.entries[0].get("model") or "unknown"

    def complete(self, messages, request_id=None):
        e = self.entries.pop(0)
        return e.get("raw_reply") or "", _meta(e.get("model") or self.model, e.get("latency_s") or 0.0,
                                               e.get("tokens_in"), e.get("tokens_out"), error=e.get("error"),
                                               kwh=e.get("energy_kwh"), note=e.get("energy_note"))


def rebuild(raw_dir: Path, out_dir: Path, requests: list[dict]) -> list[dict]:
    net = load_case(str(CASE))
    base = solve_dcopf(net)
    out_dir.mkdir(parents=True, exist_ok=True)
    all_recs = []
    for path in sorted(raw_dir.glob("*_m*_r*.jsonl")):
        m = re.fullmatch(r"(.+)_m(\d)_r(\d+)", path.stem)
        backend, mode, rep = m.group(1), int(m.group(2)), int(m.group(3))
        by_id = defaultdict(list)
        for e in read_jsonl(path):
            by_id[e["request_id"]].append(e)
        recs = []
        for req in requests:
            if req["id"] not in by_id:
                continue
            llm = ReplayLLM(by_id[req["id"]])
            llm.name = backend
            rec = run_one(mode, req, net, llm, None, base)
            rec.update(repeat=rep, run_id=path.stem, rebuilt_from_raw=True)
            recs.append(rec)
        with open(out_dir / path.name, "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, default=str) + "\n")
        all_recs += recs
        print(f"{path.name}: {len(recs)} records")
    return all_recs


def _norm(v):
    """Empty (None/NaN) compares equal to empty; booleans and strings by value."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if str(v) in ("True", "False"):
        return str(v) == "True"
    return str(v)


def check(recs, truth, scored_csv) -> int:
    net = load_case(str(CASE))
    new = score_all(recs, truth, net).set_index(["id", "mode", "repeat"]).sort_index()
    old = pd.read_csv(scored_csv)
    old = old[old["backend"] == new["backend"].iloc[0]].set_index(["id", "mode", "repeat"]).sort_index()
    cols = ["outcome", "success", "edit_correct", "feasible", "refused"]
    n_bad = 0
    for key in old.index:
        if key not in new.index:
            print("missing", key)
            n_bad += 1
            continue
        a, b = old.loc[key, cols], new.loc[key, cols]
        if any(_norm(x) != _norm(y) for x, y in zip(a, b)):
            print("MISMATCH", key, dict(a), dict(b))
            n_bad += 1
    print(f"checked {len(old)} records against {scored_csv}: {n_bad} mismatches")
    return n_bad


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=str(RESULTS / "raw"))
    ap.add_argument("--out", default=str(RESULTS / "runs"))
    ap.add_argument("--check", default=None, help="scored.csv to compare with")
    a = ap.parse_args()
    reqs = read_jsonl(REQUESTS)
    recs = rebuild(Path(a.raw), Path(a.out), reqs)
    if a.check:
        raise SystemExit(1 if check(recs, read_jsonl(TRUTH), a.check) else 0)
