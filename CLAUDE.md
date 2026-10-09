# grid-llm-agent: project rules for Claude Code

## Goal
A one-week pilot study: can an LLM agent safely turn a grid operator's plain-language request into grid decisions, and which parts of the agent structure keep it safe? We compare four agent structures (modes) on a hand-written benchmark of 40 operator requests, using a DC optimal power flow (DC-OPF) of the IEEE 30-bus system.

## Who does what
- Shoaib (the human researcher) writes: data/requests.jsonl, data/ground_truth.jsonl, LOG.md, the my_label column in results/failures.csv, and the final note.
- Claude Code writes: code, tests, scripts and figures.
- Never create, edit, reword or "improve" the human files above. If one looks wrong, stop and tell me what you found.

## Working rules
1. For every new step, first show a short plan (files, functions, tests, any modelling choice) and wait for my approval before writing code.
2. Keep code small and readable. Plain Python, no agent frameworks (no LangChain, LlamaIndex, AutoGen). Ask before adding any package not in requirements.txt.
3. Every function that handles power-system quantities states its units (MW, p.u., rad, $/h, $/MWh) and numbering convention in its docstring.
4. Write pytest tests for every module. A step is done only when all tests pass and the step's "Done when" checks pass. Report the test output.
5. Do not run git commit or git push. At the end of a step, list the files changed and what I should review. I commit myself.
6. Never write API keys into code, logs or output. Read them from .env (listed in .gitignore).
7. Make runs reproducible: temperature 0, fixed seeds where supported, and record the exact model name for every LLM call.
8. Log every LLM call (mode, request id, full prompt, raw reply, parsed result, latency, token counts, any error) as one JSON line in results/raw/<run_id>.jsonl.
9. Never put benchmark requests or answers into any LLM prompt (no leakage). Prompt examples must be generic and written separately.
10. If something is ambiguous, ask me. Do not guess silently.

## Power-system conventions
- Data: PGLib-OPF file pglib_opf_case30_ieee.m (MATPOWER format) in data/. Base 100 MVA.
- Operator numbering (the only numbering the LLM ever sees): buses use the file's bus numbers (1-30); generators are G1..Gn in file row order; branches are L1..Lm in file row order, transformers included. Internal 0-based indices never leave src/.
- DC model: lossless, |V| = 1 p.u., branch susceptance b = 1/(x * tap) with tap = 1 when the file shows 0. If a phase-shift angle is non-zero, include it as MATPOWER's makeBdc does. Flow in MW = baseMVA * b * (theta_from - theta_to - shift).
- RATE_A = 0 means "no limit", not zero capacity.
- A generator taken out of service gets Pmin = Pmax = 0.
- Line ratings may only be lowered (derates), never raised. Generator Pmax may never exceed the file's Pmax (nameplate).
- Any request that would island a bus is refused by the checker. This pilot has no multi-island OPF.
- Costs: MATPOWER polynomial gencost (model 2), in $/h.
- Tolerances: power balance 1e-4 MW; limits 1e-3 MW.

## The four modes (agent structures)
- M1 direct: the LLM outputs generator MW values itself. We evaluate them with a DC power flow.
- M2 edits: the LLM outputs edits in the schema below; the DC-OPF solver computes the dispatch.
- M3 edits + checker: M2 plus rule checks before solving; checker errors go back to the LLM for up to 2 retries.
- M4 full: M3 plus a verifier that re-checks the solved result and writes a template-based explanation for the operator.

## Edit schema (the only output allowed from the LLM in M2-M4)
One JSON object: {"edits": [ ... ], "refuse": true or false, "reason": "text"}
Allowed edit objects:
- {"action": "set_line_status", "line": "L12", "status": "out" or "in"}
- {"action": "set_line_rating", "line": "L4", "rate_mw": number}
- {"action": "set_gen_limits", "gen": "G2", "pmax_mw": number or null, "pmin_mw": number or null}
- {"action": "set_gen_status", "gen": "G5", "status": "out" or "in"}
- {"action": "set_load", "bus": 7, "pd_mw": number}
- {"action": "scale_load", "bus": 7, "factor": number}
- {"action": "scale_cost", "gen": "G3", "factor": number}
Edits are graded by canonical diff: the final changed parameter values of the network, not the JSON text.

## Repo layout
  CLAUDE.md  README.md  LOG.md  requirements.txt  .env.example  .gitignore
  data/      pglib_opf_case30_ieee.m, requests.jsonl (human), ground_truth.jsonl (human)
  src/       network.py dcopf.py dcpf.py edits.py checker.py verifier.py llm.py agent.py evaluate.py energy.py
  scripts/   day1_report.py validate_benchmark.py build_ground_truth.py run_all.py export_failures.py make_figures.py
  tests/
  results/   raw/ runs/ tables/ figures/

## Metrics
edit_accuracy (canonical diff equals ground truth), feasibility (every limit and balance within tolerance), cost_gap_pct versus the ground-truth optimum, refusal_accuracy (on the "impossible" group), retries_used, latency_s, tokens_in and tokens_out, energy_kwh (local model only, via CodeCarbon; None for API calls).
