"""Step 3: benchmark tooling, tested on the example files."""
import json

from conftest import EX_GT, EX_REQ

from src.evaluate import ground_truth_solution, read_jsonl
from validate_benchmark import prove_impossible, validate


def test_example_files_have_two_marked_lines():
    for p in (EX_REQ, EX_GT):
        rows = read_jsonl(p)
        assert len(rows) == 2
        assert all("EXAMPLE" in (r.get("text") or r.get("note")) for r in rows)


def test_validator_reads_examples():
    lines, flags = validate(EX_REQ, EX_GT)
    text = "\n".join(lines)
    assert "every request has exactly one ground truth" in text
    assert "ok    EX01" in text
    assert "EX02  impossible: proven by R4_NAMEPLATE" in text
    assert flags == 5  # only the group counts differ from 10/10/8/6/6


def test_validator_flags_problems(tmp_path):
    req = tmp_path / "r.jsonl"
    gt = tmp_path / "g.jsonl"
    req.write_text("\n".join(json.dumps(x) for x in [
        {"id": "A", "group": "outage", "text": "L38 out"},
        {"id": "B", "group": "weather", "text": "Line from 25 to 26 has tripped"},
        {"id": "B", "group": "outage", "text": "Line from 25 to 26 has tripped"},
        {"id": "C", "group": "impossible", "text": "Raise bus 7 load by 10 MW please"},
    ]))
    gt.write_text("\n".join(json.dumps(x) for x in [
        {"id": "A", "refuse": False, "edits": [{"action": "set_line_status", "line": "L38", "status": "out"}], "note": ""},
        {"id": "B", "refuse": False, "edits": [{"action": "set_line_status", "line": "L34", "status": "out"}], "note": ""},
        {"id": "C", "refuse": True, "edits": [], "note": "",
         "probe_edits": [{"action": "set_load", "bus": 7, "pd_mw": 32.8}]},
    ]))
    text = "\n".join(validate(req, gt)[0])
    assert "duplicate ids in requests" in text
    assert "invalid group 'weather'" in text
    assert "A: text has only 2 word(s)" in text
    assert "exact duplicate text" in text
    assert "R8_ISLANDING" in text            # B islands bus 26
    assert "NOT ACTUALLY IMPOSSIBLE" in text  # C is doable


def test_prove_impossible_needs_probe(net):
    assert "cannot verify" in prove_impossible(net, {"id": "X", "refuse": True, "edits": []})


def test_build_ground_truth(net):
    sols = [ground_truth_solution(net, t) for t in read_jsonl(EX_GT)]
    assert sols[0]["status"] == "optimal" and sols[0]["canonical_diff"] == {"L38.status": 0}
    assert sols[1]["status"] == "refuse"


def test_scenario_table_verdicts():
    import scenario_table as st
    assert st.evaluate([{"action": "set_line_status", "line": "L13", "status": "out"}]) == ("CHECKER", "R8_ISLANDING")
    assert st.evaluate([{"action": "set_line_status", "line": "L1", "status": "out"}])[0] == "R10"
    assert st.evaluate([{"action": "set_line_status", "line": "L38", "status": "out"}])[0] == "OK"
    assert 1.28 < st.max_load_factor(5) < 1.30


def test_worksheet_rows_are_valid(net):
    import make_worksheet as mw
    proofs = mw.verify(net)
    groups = [r[0] for r in mw.ROWS]
    assert [groups.count(g) for g in ("outage", "gen", "load", "combined", "impossible")] == [10, 10, 8, 6, 6]
    assert sorted(p for p, g in zip(proofs, groups) if g == "impossible") == sorted(
        ["R2_UNKNOWN_ID", "R4_NAMEPLATE", "R5_UPRATE", "R8_ISLANDING", "R9_ADEQUACY", "R10_INFEASIBLE"])


def test_worksheet_converter_copies_words_and_needs_every_cell():
    import pytest
    from worksheet_to_benchmark import convert
    row = {"id": "R01", "group": "outage", "edits": '[{"action": "set_line_status", "line": "L7", "status": "out"}]',
           "probe_edits": "", "text": "  My own words  ", "note": "L7 is 4-6"}
    reqs, truth = convert([row])
    assert reqs == [{"id": "R01", "group": "outage", "text": "My own words"}]
    assert truth[0]["refuse"] is False and truth[0]["edits"][0]["line"] == "L7"
    with pytest.raises(ValueError, match="R01"):
        convert([{**row, "text": ""}])
    imp = {**row, "group": "impossible", "edits": "[]", "probe_edits": '[{"action": "set_line_status", "line": "L45", "status": "out"}]'}
    assert convert([imp])[1][0] == {"id": "R01", "refuse": True, "edits": [], "note": "L7 is 4-6",
                                    "probe_edits": [{"action": "set_line_status", "line": "L45", "status": "out"}]}
