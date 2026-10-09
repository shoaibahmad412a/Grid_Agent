"""Step 6: evaluation, failure export, energy, leakage guard."""
import csv
import json

import pytest

from src import energy
from src.agent import run_mode2, system_prompt_direct, system_prompt_edits
from src.dcopf import solve_dcopf
from src.evaluate import aggregate, score_all, score_record, to_markdown
from src.llm import ScriptedLLM

ITEM = {"id": "T1", "group": "load", "refuse": False, "note": "",
        "edits": [{"action": "scale_load", "bus": 7, "factor": 1.15}]}
IMP = {"id": "T2", "group": "impossible", "refuse": True, "edits": [], "note": "no"}


def rec_for(net, reply, rid="T1", mode=2, rep=1):
    r = run_mode2({"id": rid, "group": "load", "text": "bus 7 up 15%"}, net, ScriptedLLM([reply]))
    r.update(repeat=rep, mode=mode)
    return r


def test_scoring_correct_wrong_and_refusal(net):
    good = rec_for(net, '{"edits": [{"action": "set_load", "bus": 7, "pd_mw": 26.22}], "refuse": false, "reason": ""}')
    s = score_record(good, ITEM, net)
    assert s["edit_correct"] and s["feasible"] and s["success"] and abs(s["cost_gap_pct"]) < 1e-6
    wrong = rec_for(net, '{"edits": [{"action": "set_load", "bus": 7, "pd_mw": 15}], "refuse": false, "reason": ""}')
    s = score_record(wrong, ITEM, net)
    assert not s["edit_correct"] and not s["success"]
    ref = rec_for(net, '{"edits": [], "refuse": true, "reason": "no"}')
    assert score_record(ref, ITEM, net)["false_refusal"]
    ref["id"] = "T2"
    s = score_record(ref, IMP, net)
    assert s["success"] and s["refused"] and s["feasible"] is None


def test_mode1_dispatch_ignoring_outage_is_unsafe(net):
    """M1 is judged on the network after the ground-truth edits."""
    item = {"id": "T3", "refuse": False, "note": "", "edits": [{"action": "set_line_status", "line": "L2", "status": "out"}]}
    base = solve_dcopf(net)
    rec = {"id": "T3", "mode": 1, "outcome": "dispatched", "refuse": False, "pg": base["pg"], "repeat": 1}
    s = score_record(rec, item, net)
    assert s["feasible"] is False and s["success"] is False


def test_aggregate_mean_std_over_repeats(net):
    good = '{"edits": [{"action": "set_load", "bus": 7, "pd_mw": 26.22}], "refuse": false, "reason": ""}'
    bad = '{"edits": [{"action": "set_load", "bus": 7, "pd_mw": 10}], "refuse": false, "reason": ""}'
    recs = [rec_for(net, good, rep=1), rec_for(net, bad, rep=2)]
    df = score_all(recs, [ITEM], net)
    t = aggregate(df, ["backend", "mode"])
    assert t.loc[0, "edit_accuracy_mean"] == 50 and t.loc[0, "edit_accuracy_std"] == 50
    assert "50.00 ± 50.00" in to_markdown(t)


def test_export_failures_never_overwrites_labels(tmp_path, net, monkeypatch):
    import export_failures as ef
    rows = ef.failure_rows([rec_for(net, "nope")], [ITEM], [{"id": "T1", "group": "load", "text": "x"}], net)
    assert len(rows) == 1 and rows[0]["my_label"] == "" and rows[0]["outcome"] == "BAD_JSON"
    out = tmp_path / "failures.csv"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ef.COLUMNS)
        w.writeheader()
        w.writerow({**rows[0], "my_label": "BAD_JSON"})
    monkeypatch.setattr("sys.argv", ["x", "--out", str(out), "--runs", str(tmp_path)])
    with pytest.raises(SystemExit, match="already has labels"):
        ef.main()


def test_no_benchmark_text_in_prompts(net, benchmark):
    reqs, truth = benchmark
    prompts = system_prompt_edits(net) + system_prompt_direct(net, solve_dcopf(net))
    for r in reqs:
        assert r["text"] not in prompts
    for t in truth:
        if len(t.get("note", "")) > 10:
            assert t["note"] not in prompts


def test_energy_measure_returns_value_or_reason():
    result, kwh, note = energy.measure(lambda: 41 + 1)
    assert result == 42
    assert (kwh is None and note) or kwh >= 0
