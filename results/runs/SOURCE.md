# Source of the run files

Graded records of the full run (gpt-5-nano-2025-08-07, 40 requests x 4 modes x 3 repeats).

| File | Source |
|---|---|
| api_m1_r3, api_m2_r3, api_m3_r3, api_m4_r2, api_m4_r3 | Original files from the researcher's laptop |
| api_m1_r1, api_m1_r2, api_m2_r1, api_m2_r2, api_m3_r1, api_m3_r2, api_m4_r1 | Rebuilt from `results/raw/` with `scripts/rebuild_runs.py` (records carry `"rebuilt_from_raw": true`) |

The rebuild replays the logged replies through the same agent code. Checks:
- all 480 rebuilt records match the laptop's `results/tables/scored.csv` with 0 mismatches;
- the 5 original files match their rebuilt versions in every graded field.
The only difference is the Mode 4 explanation text for R08 and R20: the laptop ran before the
"-0.0%" display fix, the rebuild after it.
