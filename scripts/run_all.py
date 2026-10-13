"""Run requests through the agent modes and save one record per request.

Usage examples:
  python scripts/run_all.py --mode 2 --backend mock --all
  python scripts/run_all.py --mode all --backend ollama --ids R01,R02
  python scripts/run_all.py --modes all --backend api --all --repeats 3
  python scripts/run_all.py --modes all --backend api --ids R17 --repeats 3 --redo

Output: results/runs/<backend>_m<k>_r<n>.jsonl (records) and
results/raw/<backend>_m<k>_r<n>.jsonl (every LLM call). Resume-safe: ids
already in the output file are skipped. Before any real backend runs, a mock
sanity pass (in memory) must score 100% in Modes 2-4, or the run stops.
"""
import argparse
import json
import os
import time

import _common  # noqa: F401
from _common import CASE, REQUESTS, RESULTS, TRUTH

from src.agent import RUNNERS, run_mode1, run_mode4, system_prompt_direct, system_prompt_edits
from src.dcopf import solve_dcopf
from src.evaluate import mock_answers, read_jsonl, score_record
from src.llm import CallLogger, MockLLM, make_llm
from src.network import load_case


def check_no_leakage(net, base, requests, truth) -> None:
    """Rule 9: no benchmark text or answer note may appear in any system prompt."""
    prompts = system_prompt_edits(net) + system_prompt_direct(net, base)
    for r in requests:
        if r["text"].strip() and r["text"].strip() in prompts:
            raise SystemExit(f"LEAKAGE: request {r['id']} text appears in a system prompt")
    for t in truth:
        note = (t.get("note") or "").strip()
        if len(note) > 10 and note in prompts:
            raise SystemExit(f"LEAKAGE: ground-truth note of {t['id']} appears in a system prompt")


def run_one(mode, req, net, llm, logger, base):
    if mode == 1:
        return run_mode1(req, net, llm, logger, base_result=base)
    if mode == 4:
        return run_mode4(req, net, llm, logger, base_cost=base["cost"])
    return RUNNERS[mode](req, net, llm, logger)


def drop_records(path, ids) -> int:
    """Remove the records with these ids from a run file; return how many were removed."""
    rows = read_jsonl(path)
    keep = [r for r in rows if r["id"] not in ids]
    with open(path, "w", encoding="utf-8") as f:
        for r in keep:
            f.write(json.dumps(r, default=str) + "\n")
    return len(rows) - len(keep)


def mock_sanity(net, base, requests, truth, modes=(2, 3, 4)) -> dict:
    """Run the mock backend in memory; return success % per mode."""
    by_id = {t["id"]: t for t in truth}
    out, cache = {}, {}
    for m in modes:
        llm = MockLLM(mock_answers(net, truth, m))
        ok = [score_record(run_one(m, r, net, llm, None, base), by_id[r["id"]], net, cache)["success"]
              for r in requests]
        out[m] = 100.0 * sum(ok) / max(len(ok), 1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", "--modes", dest="mode", default="2", help="1, 2, 3, 4 or all")
    ap.add_argument("--backend", default="mock", choices=["mock", "ollama", "api"])
    ap.add_argument("--ids", default=None, help="comma-separated ids, e.g. R01,R02")
    ap.add_argument("--all", action="store_true", help="run every request")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--requests", default=str(REQUESTS))
    ap.add_argument("--truth", default=str(TRUTH))
    ap.add_argument("--out-dir", default=str(RESULTS))
    ap.add_argument("--redo", action="store_true",
                    help="delete the selected ids from existing run files first, so they run again "
                         "(old calls stay in results/raw)")
    a = ap.parse_args()
    if not a.all and not a.ids:
        ap.error("give --ids or --all")

    net = load_case(str(CASE))
    base = solve_dcopf(net)
    requests = read_jsonl(a.requests)
    truth = read_jsonl(a.truth)
    if a.ids:
        want = [i.strip() for i in a.ids.split(",")]
        requests = [r for r in requests if r["id"] in want]
        missing = set(want) - {r["id"] for r in requests}
        if missing:
            raise SystemExit(f"unknown ids: {sorted(missing)}")
    modes = [1, 2, 3, 4] if a.mode == "all" else [int(a.mode)]
    check_no_leakage(net, base, requests, truth)

    sanity = mock_sanity(net, base, requests, truth)
    print("mock sanity (success %, modes 2-4): " + ", ".join(f"M{m} {v:.0f}%" for m, v in sanity.items()))
    if a.backend != "mock" and any(v < 100 for v in sanity.values()):
        raise SystemExit("mock sanity is below 100%: fix the pipeline or the ground truth before a real run")

    runs_dir, raw_dir = f"{a.out_dir}/runs", f"{a.out_dir}/raw"
    jobs = [(m, rep) for rep in range(1, a.repeats + 1) for m in modes]
    total = len(jobs) * len(requests)
    done_calls, t_start = 0, time.time()
    for m, rep in jobs:
        run_id = f"{a.backend}_m{m}_r{rep}"
        out_path = f"{runs_dir}/{run_id}.jsonl"
        os.makedirs(runs_dir, exist_ok=True)
        if a.redo and os.path.exists(out_path):
            drop_records(out_path, {r["id"] for r in requests})
        done = {r["id"] for r in read_jsonl(out_path)} if os.path.exists(out_path) else set()
        llm = make_llm(a.backend, mock_answers(net, truth, m) if a.backend == "mock" else None)
        logger = CallLogger(run_id, raw_dir)
        for req in requests:
            if req["id"] in done:
                total -= 1
                continue
            rec = run_one(m, req, net, llm, logger, base)
            rec.update(repeat=rep, run_id=run_id)
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")
            done_calls += 1
            el = time.time() - t_start
            eta = el / done_calls * (total - done_calls)
            print(f"[{done_calls}/{total}] {run_id} {req['id']:<6} {rec['outcome']:<17} "
                  f"retries={rec['retries_used']} {rec['latency_s']:.1f}s  ETA {eta / 60:.1f} min", flush=True)
    print("done")


if __name__ == "__main__":
    main()
