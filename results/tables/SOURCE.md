# Source of these tables

`by_mode.md` and `opf_energy.json` are copied verbatim from the output of
`python scripts/evaluate_runs.py` on the researcher's laptop (14 Oct 2026),
after the full run `run_all.py --modes all --backend api --all --repeats 3`
with gpt-5-nano-2025-08-07. The raw run files (`results/runs/`, `results/raw/`)
stay on that laptop (git-ignored). `by_group.md`, `failures.csv` and the
figures are added from the laptop when committed there.
