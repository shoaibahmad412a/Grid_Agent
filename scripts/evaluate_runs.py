"""Score all runs and write results/tables/{by_mode,by_group,scored}.{csv,md}.

Also measures the energy of one DC-OPF solve (results/tables/opf_energy.json)
so the LLM's energy can be compared with the solver's.
Usage: python scripts/evaluate_runs.py [--truth PATH] [--runs DIR] [--no-energy]
"""
import argparse
import json

import _common  # noqa: F401
from _common import CASE, RESULTS, TRUTH

from src.energy import measure_opf_energy
from src.evaluate import load_runs, read_jsonl, score_all, to_markdown, write_tables
from src.network import load_case

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--truth", default=str(TRUTH))
    ap.add_argument("--runs", default=str(RESULTS / "runs"))
    ap.add_argument("--no-energy", action="store_true")
    a = ap.parse_args()
    net = load_case(str(CASE))
    df = score_all(load_runs(a.runs), read_jsonl(a.truth), net)
    if df.empty:
        raise SystemExit("no runs found in " + a.runs)
    out = RESULTS / "tables"
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "scored.csv", index=False)
    t = write_tables(df, out)
    print(to_markdown(t["by_mode"]))
    if not a.no_energy:
        e = measure_opf_energy(net)
        (out / "opf_energy.json").write_text(json.dumps(e, indent=2))
        print("DC-OPF solve:", e)
    print(f"tables saved in {out}")
