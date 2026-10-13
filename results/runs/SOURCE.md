# Source of the run files

Graded records of the full run (gpt-5-nano-2025-08-07, 40 requests x 4 modes x 3 repeats),
plus the mock sanity run (`mock_m2_r1`, ground-truth answers, 100%).

All 13 files are the original files from the researcher's laptop.

Before the originals were uploaded, the API run files were rebuilt from `results/raw/` with
`scripts/rebuild_runs.py` (replaying the logged replies through the same agent code). The
rebuilt records matched the laptop's `results/tables/scored.csv` with 0 mismatches, and every
original file matched its rebuilt version in every graded field. The only difference was the
Mode 4 explanation text for R08 and R20: the laptop ran before the "-0.0%" display fix.
