# Source of these tables

All files come from the researcher's laptop run (14 Oct 2026): `run_all.py --modes all
--backend api --all --repeats 3` with gpt-5-nano-2025-08-07, then `evaluate_runs.py`.

- `by_mode.csv`, `by_group.csv`, `scored.csv`, `opf_energy.json`: uploaded unchanged.
- `by_mode.md`: uploaded unchanged except converted from Windows cp1252 to UTF-8
  (the "±" signs were stored as cp1252 by a bug that is now fixed).
- `by_group.md`: rebuilt from `by_group.csv` with `src.evaluate.to_markdown`
  (the laptop file was not uploaded).
- The raw run files (`results/runs/`, `results/raw/`) stay on that laptop (git-ignored).

Known issue in this run: request R17's em dash was read with the Windows cp1252
encoding, so the model received "expensiveâ€”triple" instead of "expensive—triple"
in all 12 R17 calls (visible in results/raw). Fixed in code; see README.
