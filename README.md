# grid-llm-agent

A one-week pilot study: can an LLM agent safely turn a grid operator's plain-language request into grid decisions, and which parts of the agent structure keep it safe? It compares four agent structures on a hand-written benchmark of 40 requests, using a DC-OPF (DC optimal power flow) of the IEEE 30-bus system.

| Mode | Structure |
|---|---|
| M1 direct | The LLM writes the generator MW values itself. They are judged with a DC power flow. |
| M2 edits | The LLM writes edits in a fixed JSON schema, and the DC-OPF computes the dispatch. |
| M3 edits + checker | M2 plus rules R1–R10. Checker messages go back to the LLM for up to 2 retries, plus 1 more if the OPF is infeasible. |
| M4 full | M3 plus a verifier and a template explanation for the operator (no LLM). |

Project rules are in [CLAUDE.md](CLAUDE.md).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then fill in the values (never commit .env)
python -c "import cvxpy, networkx, matpowercaseframes, pandapower"
pytest
```

Versions are pinned in `requirements.txt`. They were installed and tested on Python 3.13; the plan names Python 3.11, and the same pins are expected to work there.

`.env` keys:

| Key | Meaning |
|---|---|
| `LLM_API_KEY`, `LLM_API_MODEL` | Remote model; the exact model name is recorded in every result |
| `LLM_API_PROVIDER` | `openai` (any OpenAI-compatible `/chat/completions` API: OpenAI, Groq, Together, OpenRouter, …) or `anthropic` |
| `LLM_API_BASE` | Optional base URL; leave empty for the provider default |
| `OLLAMA_MODEL`, `OLLAMA_URL` | Local model, for example `llama3.1:8b` and `http://localhost:11434` |
| `CODECARBON_COUNTRY` | ISO-3 country code for CodeCarbon's carbon intensity (default `PAK`) |

## Data source

- File: `data/pglib_opf_case30_ieee.m` (PGLib-OPF v23.07, MATPOWER format), unmodified.
- URL: https://raw.githubusercontent.com/power-grid-lib/pglib-opf/master/pglib_opf_case30_ieee.m (repository https://github.com/power-grid-lib/pglib-opf)
- Downloaded: 2026-10-09
- The file starts with a `%%%%` licence/comment header. The `function mpc = pglib_opf_case30_ieee` line is line 24.

Facts about this case that matter for the benchmark (see `results/day1_report.txt`):

- baseMVA is 100. There are 30 buses, 6 generators and 41 branches (7 of them transformers), and no branch has RATE_A = 0.
- **Only G1 (bus 1, 271 MW) and G2 (bus 2, 92 MW) produce active power.** G3–G6 are synchronous condensers with Pmax = 0 MW and zero cost.
- All costs are linear (c2 = c0 = 0): G1 costs 18.42 $/MWh and G2 52.18 $/MWh. The DC-OPF is therefore a linear program.
- Total load is 283.4 MW, capacity is 363 MW, and the reserve margin is 28.1%.
- Base-case cost is 7504.44 $/h with G1 = 215.75 MW and G2 = 67.65 MW. L1 (1-2) is binding at 100%, so LMPs (bus prices) range from 18.42 to 52.18 $/MWh.
- Bridge branches are L13 (cuts off bus 11), L16 (bus 13) and L34 (bus 26).
- All Gs values and phase shifts are 0, so those model terms are present but inactive.

## How to run each step

```bash
python scripts/day1_report.py                  # Step 1: prints and saves results/day1_report.txt
pytest -s tests/test_step2.py                  # Step 2: prints one checker message per rule
python scripts/validate_benchmark.py           # Step 3: checks data/requests.jsonl + data/ground_truth.jsonl
python scripts/build_ground_truth.py           # Step 3: writes results/ground_truth_solutions.jsonl
python scripts/run_all.py --mode 2 --backend mock --all              # Step 4: must be 100%
python scripts/run_all.py --mode 2 --backend ollama --ids R01,R02    # Step 4: a real model on a few ids
python scripts/run_all.py --modes all --backend mock --all           # Step 6: mock sanity runs
python scripts/run_all.py --modes all --backend ollama --all --repeats 3
python scripts/run_all.py --modes all --backend api --all --repeats 3
python scripts/evaluate_runs.py                # Step 6: tables in results/tables/ + DC-OPF energy
python scripts/export_failures.py              # Step 6: results/failures.csv (empty my_label column)
python scripts/make_figures.py                 # Step 6: figures a, b, c in results/figures/
python scripts/count_labels.py                 # Step 7: after my_label is filled in
```

To try the tools before the real benchmark exists, add `--requests data/requests.example.jsonl --truth data/ground_truth.example.jsonl` to `validate_benchmark.py` and `run_all.py`, or `--truth …` to `build_ground_truth.py`, `evaluate_runs.py` and `make_figures.py`.

How runs behave:
- `run_all.py` is resume-safe: ids already in `results/runs/<backend>_m<k>_r<n>.jsonl` are skipped.
- Every LLM call is logged to `results/raw/<run_id>.jsonl`.
- Before any real backend runs, an in-memory mock pass must score 100% in Modes 2–4, or the run stops.
- The run also refuses to start if any benchmark text appears in a prompt (leakage guard).

### Reproducing every result from a fresh clone

1. Follow **Setup** above. Fill in `.env`, start Ollama, and pull the model named in `OLLAMA_MODEL`.
2. Run `pytest`. Every test must pass.
3. Run `python scripts/day1_report.py`.
4. Run `python scripts/validate_benchmark.py`. It must report 40 items, 10/10/8/6/6, and 0 flags.
5. Run `python scripts/build_ground_truth.py`.
6. Run `python scripts/run_all.py --modes all --backend mock --all` and check that the mock scores 100%.
7. Run `python scripts/run_all.py --modes all --backend ollama --all --repeats 3`.
8. Run `python scripts/run_all.py --modes all --backend api --all --repeats 3`.
9. Run `python scripts/evaluate_runs.py`, then `python scripts/export_failures.py`, then `python scripts/make_figures.py`.
10. After `my_label` is filled in by hand, run `python scripts/count_labels.py`.

LLM replies can still vary slightly between runs even with temperature 0 and a fixed seed. That is why each run is repeated 3 times and the tables report mean ± std.

## Ground-truth file format

```json
{"id": "R01", "refuse": false, "edits": [...], "note": "why this is the right answer"}
```

Impossible items have `"refuse": true` and `"edits": []`. The validator can only *prove* an item is impossible if you also add an optional `"probe_edits"` list: the edits the request literally asks for. Example: `[{"action": "set_gen_limits", "gen": "G2", "pmax_mw": 500, "pmin_mw": null}]`. The validator runs the checker and then the DC-OPF on those edits. `probe_edits` is never shown to any LLM. See `data/*.example.jsonl`.

## Modelling and grading choices (review these)

These are the defaults I chose where the plan left a choice open. Each is one line of code to change.

1. **Mode 1 feasibility.** An LLM can rarely hit 1e-4 MW balance by hand. The main metric `feasible` therefore lets the slack unit absorb the mismatch and then checks every generator limit (slack included) and every line limit. `feasible_strict` additionally requires |mismatch| ≤ 1e-4 MW. Both are reported, and the raw mismatch is saved.
2. **All dispatches are judged on the true network.** That means the network after the ground-truth edits, for every mode: Mode 1's MW values, and the OPF dispatch of Modes 2–4. An agent that ignores an outage is therefore marked unsafe.
3. **`scale_cost`** multiplies c2, c1 and c0. In this case only c1 is non-zero, so the choice has no effect here.
4. **Scale edits use the base value.** Two `scale_load` edits on the same bus do not compound. A `scale_load` and a `set_load` that give the same MW are not a conflict.
5. **`set_gen_status "in"`** restores the file's Pmin and Pmax.
6. **The schema is strict.** All three top-level keys are required (`edits`, `refuse`, `reason`), bus must be an integer, and `set_gen_limits` needs both keys (null means unchanged).
7. **Retry budget.** M3 and M4 allow 2 checker retries, plus 1 extra retry after R10 (infeasible OPF).
8. **Islanding** (R8) is any bus not connected to the reference bus. This includes buses 11 and 13, which have only a condenser and no load.
9. **Angle-difference limits** (±30° in the file) are not modelled. They are not in the plan's formulation. pandapower's DC-OPF gives the same base cost (7504.44 $/h).
10. **Energy.** CodeCarbon measures at machine level, so each Ollama call is one tracker. A single DC-OPF solve (about 15 ms) is below CodeCarbon's resolution, so it is measured as 200 solves divided by 200. API calls get `None` with the reason "remote; not measurable".
11. **Prompt examples** (`EXAMPLES_EDITS` in `src/agent.py`) are generic: "line L23 opened and bus 18 at 4.5 MW", and "raise the voltage at bus 3" as a refusal. **Check that none of your 40 requests matches them.** Each record stores a hash of the system prompt, so a prompt change after the freeze is visible.

## Repository layout

```
src/      network.py dcopf.py dcpf.py edits.py checker.py verifier.py llm.py agent.py evaluate.py energy.py
scripts/  day1_report.py validate_benchmark.py build_ground_truth.py run_all.py evaluate_runs.py
          export_failures.py make_figures.py count_labels.py
tests/    test_step1.py … test_step6.py
data/     pglib_opf_case30_ieee.m, *.example.jsonl; requests.jsonl and ground_truth.jsonl are written by the researcher
results/  day1_report.txt, raw/ and runs/ (git-ignored), tables/, figures/
```
