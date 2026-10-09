"""Step 5: Modes 1, 3 and 4, verifier and explanation."""
import json

from src.agent import run_mode1, run_mode3, run_mode4
from src.dcopf import solve_dcopf
from src.edits import apply_edits, parse_edits
from src.evaluate import mock_answers, score_record
from src.llm import MockLLM, ScriptedLLM
from src.verifier import explain, extra_changes, mentioned, verify

REQ = {"id": "T1", "group": "outage", "text": "the line between bus 27 and bus 30 tripped"}


def E(edits, refuse=False, reason=""):
    return json.dumps({"edits": edits, "refuse": refuse, "reason": reason})


def test_mock_100pct_in_modes_2_3_4(net, benchmark):
    from run_all import mock_sanity
    reqs, truth = benchmark
    assert mock_sanity(net, solve_dcopf(net), reqs, truth) == {2: 100.0, 3: 100.0, 4: 100.0}


def test_mock_mode1_is_feasible(net, benchmark):
    reqs, truth = benchmark
    by_id = {t["id"]: t for t in truth}
    llm = MockLLM(mock_answers(net, truth, 1))
    for r in reqs:
        assert score_record(run_mode1(r, net, llm), by_id[r["id"]], net)["success"]


def test_mode3_retry_fixes_wrong_id(net):
    llm = ScriptedLLM([E([{"action": "set_line_status", "line": "L42", "status": "out"}]),
                       E([{"action": "set_line_status", "line": "L38", "status": "out"}])])
    rec = run_mode3(REQ, net, llm)
    assert rec["outcome"] == "solved" and rec["retries_used"] == 1
    assert rec["canonical_diff"] == {"L38.status": 0}
    fb = llm.calls[1][-1]["content"]
    assert fb.startswith("Your edits were rejected:") and "L42" in fb and "set refuse to true" in fb


def test_mode3_gives_up_after_two_retries(net):
    bad = E([{"action": "set_line_status", "line": "L13", "status": "out"}])
    rec = run_mode3(REQ, net, ScriptedLLM([bad, bad, bad]))
    assert rec["outcome"] == "CHECKER_REJECTED" and rec["retries_used"] == 2
    assert rec["checker_codes"] == ["R8_ISLANDING"]


def test_mode3_refusal_after_feedback(net):
    rec = run_mode3(REQ, net, ScriptedLLM([
        E([{"action": "set_gen_limits", "gen": "G2", "pmax_mw": 500, "pmin_mw": None}]),
        E([], refuse=True, reason="G2 cannot exceed 92 MW")]))
    assert rec["outcome"] == "refused" and rec["refuse"] and rec["retries_used"] == 1


def test_mode3_r10_gets_one_extra_retry(net):
    infeasible = E([{"action": "set_line_rating", "line": "L1", "rate_mw": 10},
                    {"action": "set_line_rating", "line": "L2", "rate_mw": 10}])
    rec = run_mode3(REQ, net, ScriptedLLM([infeasible, E([], True, "no safe dispatch")]))
    assert rec["outcome"] == "refused" and rec["retries_used"] == 1
    assert rec["attempts"][0]["issues"][0]["code"] == "R10_INFEASIBLE"


def test_mode4_explanation_and_extra_changes(net):
    llm = ScriptedLLM([E([{"action": "set_line_status", "line": "L38", "status": "out"},
                          {"action": "scale_load", "bus": 7, "factor": 1.1}])])
    rec = run_mode4(REQ, net, llm)
    assert rec["outcome"] == "solved" and rec["verify"]["ok"]
    assert rec["extra_changes"] == ["bus7"]
    assert rec["explanation"].startswith("Changes applied: bus 7 load 22.8 -> 25.1 MW; L38 out of service.")
    assert "New cost:" in rec["explanation"] and "Most loaded line: L1 at 100%." in rec["explanation"]


def test_mode4_refusal_explanation(net):
    rec = run_mode4(REQ, net, ScriptedLLM([E([], True, "the line does not exist")]))
    assert rec["explanation"] == "Refused: the line does not exist."


def test_verifier_flags_overload(net):
    """A hand-made dispatch with everything on G1 breaks G1's limit and loads L1 past 100%."""
    v = verify(net, net, {"pg": {"G1": 283.4, "G2": 0.0}}, "anything")
    assert not v["ok"]
    assert [x["gen"] for x in v["gen_violations"]] == ["G1"]
    assert any(x["line"] == "L1" and x["loading_pct"] > 100 for x in v["line_violations"])


def test_verifier_flags_line_overload_from_outage(net):
    es = parse_edits(E([{"action": "set_line_status", "line": "L2", "status": "out"}]))
    edited = apply_edits(net, es)
    base_dispatch = solve_dcopf(net)  # ignores the outage: 215.8 MW must now leave bus 1 on L1 (138 MW)
    v = verify(net, edited, base_dispatch, "L2 out")
    assert any(x["line"] == "L1" for x in v["line_violations"])


def test_mentioned_rules(net):
    assert mentioned(net, "L38", "line from 27 to 30")
    assert mentioned(net, "L38", "take L38 out")
    assert not mentioned(net, "L38", "line 6-9")
    assert mentioned(net, "G2", "unit at bus 2 is derated")
    assert mentioned(net, "bus7", "load at bus 7")
    assert extra_changes(net, {"L38.status": 0, "bus5.Pd": 10}, "27-30 is out") == ["bus5"]


def test_explain_template(net):
    assert explain(net, None, None, None, refused=True, reason="unsafe.") == "Refused: unsafe."
