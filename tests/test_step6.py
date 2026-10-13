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


def test_show_run_table(net, tmp_path, monkeypatch, capsys):
    import json as _json
    import show_run
    from src.evaluate import mock_answers, read_jsonl
    from src.agent import run_mode2
    from src.llm import MockLLM
    from conftest import EX_GT, EX_REQ
    truth = read_jsonl(EX_GT)
    llm = MockLLM(mock_answers(net, truth, 2))
    runs = tmp_path / "runs"
    runs.mkdir()
    with open(runs / "x.jsonl", "w") as f:
        for r in read_jsonl(EX_REQ):
            f.write(_json.dumps(run_mode2(r, net, llm)) + "\n")
    monkeypatch.setattr(show_run, "RESULTS", tmp_path)
    monkeypatch.setattr("sys.argv", ["x", "x", "--truth", str(EX_GT)])
    show_run.main()
    out = capsys.readouterr().out
    assert "2/2 fully correct" in out and "1/1 doable requests feasible" in out


def test_utf8_benchmark_text_survives_reading(tmp_path):
    """Request texts with non-ASCII characters (an em dash in R17) must be read as UTF-8 on every OS."""
    from src.evaluate import read_jsonl
    p = tmp_path / "r.jsonl"
    p.write_bytes('{"id": "X", "text": "more expensive—triple"}\n'.encode("utf-8"))
    assert read_jsonl(p)[0]["text"] == "more expensive—triple"


def test_count_labels_reads_excel_cp1252(tmp_path, monkeypatch):
    import count_labels
    import export_failures as ef
    (tmp_path / "tables").mkdir()
    (tmp_path / "figures").mkdir()
    row = {c: "" for c in ef.COLUMNS}
    row.update(backend="api", mode="1", request_id="R01", request_text="café", my_label="UNSAFE_DISPATCH")
    import csv as _csv, io as _io
    buf = _io.StringIO()
    w = _csv.DictWriter(buf, fieldnames=ef.COLUMNS)
    w.writeheader()
    w.writerow(row)
    (tmp_path / "failures.csv").write_bytes(buf.getvalue().encode("cp1252"))
    monkeypatch.setattr(count_labels, "RESULTS", tmp_path)
    count_labels.main()
    assert "UNSAFE_DISPATCH" in (tmp_path / "tables" / "failure_labels.md").read_text(encoding="utf-8")


def test_run_all_drop_records(tmp_path):
    import json as _json
    from run_all import drop_records
    p = tmp_path / "api_m2_r1.jsonl"
    p.write_text("".join(_json.dumps({"id": i}) + "\n" for i in ("R16", "R17", "R18")), encoding="utf-8")
    assert drop_records(p, {"R17"}) == 1
    assert [_json.loads(l)["id"] for l in p.read_text(encoding="utf-8").splitlines()] == ["R16", "R18"]


def test_rebuild_runs_replays_logged_replies(net, tmp_path):
    """A run rebuilt from its raw log gives the same records as the original run."""
    import json as _json
    from rebuild_runs import rebuild
    from src.agent import run_mode3
    from src.llm import CallLogger, ScriptedLLM
    req = {"id": "T1", "group": "outage", "text": "the line between bus 27 and bus 30 tripped"}
    bad = '{"edits": [{"action": "set_line_status", "line": "L42", "status": "out"}], "refuse": false, "reason": ""}'
    good = '{"edits": [{"action": "set_line_status", "line": "L38", "status": "out"}], "refuse": false, "reason": ""}'
    raw = tmp_path / "raw"
    original = run_mode3(req, net, ScriptedLLM([bad, good]), CallLogger("api_m3_r1", raw))
    recs = rebuild(raw, tmp_path / "runs", [req])
    assert len(recs) == 1
    for k in ("outcome", "retries_used", "canonical_diff", "cost", "pg"):
        assert recs[0][k] == original[k]
    assert _json.loads((tmp_path / "runs" / "api_m3_r1.jsonl").read_text(encoding="utf-8"))["repeat"] == 1


def test_failures_helper_columns(net):
    """expected and violations columns help the researcher label; my_label stays empty."""
    import export_failures as ef
    from src.dcopf import solve_dcopf
    item = {"id": "T3", "refuse": False, "note": "", "edits": [{"action": "set_line_status", "line": "L2", "status": "out"}]}
    rec = {"id": "T3", "mode": 1, "outcome": "dispatched", "refuse": False, "pg": solve_dcopf(net)["pg"], "repeat": 1}
    rows = ef.failure_rows([rec], [item], [{"id": "T3", "group": "outage", "text": "1-3 tripped"}], net)
    assert rows[0]["expected"] == '{"L2.status": 0}'
    assert rows[0]["violations"].startswith("L1 ") and rows[0]["my_label"] == ""
