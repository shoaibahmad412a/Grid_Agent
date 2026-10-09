"""Step 2: edit language and checker (no LLM)."""
import json

import numpy as np
import pytest

from src.checker import blocking, bridge_branches, check, infeasible_issue
from src.dcopf import solve_dcopf
from src.dcpf import dc_power_flow
from src.edits import EditSet, ParseError, apply_edits, canonical_diff, parse_edits


def P(edits, refuse=False, reason=""):
    return parse_edits(json.dumps({"edits": edits, "refuse": refuse, "reason": reason}))


def codes(net, es):
    return [i.code for i in blocking(check(net, es))]


# ---- parse_edits ------------------------------------------------------
def test_parse_valid_and_fenced():
    text = '```json\n{"edits": [{"action": "set_load", "bus": 7, "pd_mw": 25}], "refuse": false, "reason": ""}\n```'
    es = parse_edits(text)
    assert isinstance(es, EditSet) and es.edits[0].args == {"bus": 7, "pd_mw": 25}


@pytest.mark.parametrize("text,kind", [
    ("not json", "BAD_JSON"),
    ("[1, 2]", "BAD_JSON"),
    ('{"edits": [], "refuse": false}', "SCHEMA"),
    ('{"edits": [{"action": "remove_line", "line": "L1"}], "refuse": false, "reason": ""}', "SCHEMA"),
    ('{"edits": [{"action": "set_load", "bus": "7", "pd_mw": 5}], "refuse": false, "reason": ""}', "SCHEMA"),
    ('{"edits": [{"action": "set_load", "bus": 7, "pd_mw": 5, "x": 1}], "refuse": false, "reason": ""}', "SCHEMA"),
    ('{"edits": [{"action": "set_line_status", "line": "L1", "status": "off"}], "refuse": false, "reason": ""}', "SCHEMA"),
    ('{"edits": [{"action": "set_gen_limits", "gen": "G1", "pmax_mw": null, "pmin_mw": null}], "refuse": false, "reason": ""}', "SCHEMA"),
])
def test_parse_rejects(text, kind):
    p = parse_edits(text)
    assert isinstance(p, ParseError) and p.kind == kind


# ---- apply / diff ----------------------------------------------------
def test_scale_and_set_load_same_diff(net):
    a = apply_edits(net, P([{"action": "scale_load", "bus": 7, "factor": 1.15}]))
    b = apply_edits(net, P([{"action": "set_load", "bus": 7, "pd_mw": 22.8 * 1.15}]))
    assert canonical_diff(net, a) == canonical_diff(net, b) == {"bus7.Pd": 26.22}


def test_apply_never_mutates_base(net):
    before = net.copy()
    apply_edits(net, P([{"action": "set_line_status", "line": "L12", "status": "out"},
                        {"action": "set_gen_status", "gen": "G2", "status": "out"},
                        {"action": "scale_load", "bus": 5, "factor": 2},
                        {"action": "scale_cost", "gen": "G1", "factor": 2}]))
    for f in ("pd", "pmax", "pmin", "gen_status", "br_status", "c1", "rate_a"):
        assert np.array_equal(getattr(before, f), getattr(net, f))


def test_gen_out_sets_limits_to_zero(net):
    n2 = apply_edits(net, P([{"action": "set_gen_status", "gen": "G2", "status": "out"}]))
    k = net.gen_index("G2")
    assert n2.gen_status[k] == 0 and n2.pmin[k] == 0 and n2.pmax[k] == 0
    assert canonical_diff(net, n2) == {"G2.status": 0, "G2.Pmax": 0.0}
    back = apply_edits(n2, P([{"action": "set_gen_status", "gen": "G2", "status": "in"}]))
    assert back.pmax[k] == 92


def test_canonical_diff_empty_for_noop(net):
    n2 = apply_edits(net, P([{"action": "set_line_status", "line": "L5", "status": "in"}]))
    assert canonical_diff(net, n2) == {}


# ---- one test per rule ------------------------------------------------
def test_r1_schema(net):
    assert codes(net, parse_edits("hello")) == ["R1_SCHEMA"]


@pytest.mark.parametrize("edit,hint", [
    ({"action": "set_line_status", "line": "L42", "status": "out"}, True),
    ({"action": "set_line_status", "line": "L0", "status": "out"}, True),
    ({"action": "set_gen_status", "gen": "G0", "status": "out"}, True),
    ({"action": "set_load", "bus": 31, "pd_mw": 5}, True),
    ({"action": "set_line_status", "line": "line 6-9", "status": "out"}, False),
])
def test_r2_unknown_id(net, edit, hint):
    iss = blocking(check(net, P([edit])))
    assert [i.code for i in iss] == ["R2_UNKNOWN_ID"]
    assert ("numbering starts at 1" in iss[0].message) == hint


@pytest.mark.parametrize("edit", [
    {"action": "set_load", "bus": 7, "pd_mw": -5},
    {"action": "scale_load", "bus": 7, "factor": 15},
    {"action": "set_line_rating", "line": "L4", "rate_mw": 0},
    {"action": "scale_cost", "gen": "G1", "factor": 50},
    {"action": "set_gen_limits", "gen": "G1", "pmax_mw": -1, "pmin_mw": None},
])
def test_r3_range(net, edit):
    assert "R3_RANGE" in codes(net, P([edit]))


def test_r4_nameplate(net):
    assert codes(net, P([{"action": "set_gen_limits", "gen": "G2", "pmax_mw": 500, "pmin_mw": None}])) == ["R4_NAMEPLATE"]
    assert codes(net, P([{"action": "set_gen_limits", "gen": "G2", "pmax_mw": 50, "pmin_mw": 60}])) == ["R4_NAMEPLATE"]
    assert codes(net, P([{"action": "set_gen_limits", "gen": "G2", "pmax_mw": 92, "pmin_mw": 10}])) == []


def test_r5_uprate(net):
    assert codes(net, P([{"action": "set_line_rating", "line": "L4", "rate_mw": 200}])) == ["R5_UPRATE"]
    assert codes(net, P([{"action": "set_line_rating", "line": "L4", "rate_mw": 100}])) == []


def test_r6_conflict(net):
    assert "R6_CONFLICT" in codes(net, P([{"action": "set_load", "bus": 7, "pd_mw": 20},
                                          {"action": "set_load", "bus": 7, "pd_mw": 30}]))
    # same final value is not a conflict
    assert codes(net, P([{"action": "scale_load", "bus": 7, "factor": 1.15},
                         {"action": "set_load", "bus": 7, "pd_mw": 26.22}])) == []


def test_r7_empty(net):
    assert codes(net, P([])) == ["R7_EMPTY"]
    assert codes(net, P([], refuse=True, reason="")) == ["R7_EMPTY"]
    assert codes(net, P([{"action": "set_load", "bus": 7, "pd_mw": 5}], refuse=True, reason="x")) == ["R7_EMPTY"]
    assert codes(net, P([], refuse=True, reason="not possible")) == []


def test_r8_islanding_on_every_bridge(net):
    bridges = bridge_branches(net)
    assert {b["line"] for b in bridges} == {"L13", "L16", "L34"}
    for b in bridges:
        iss = blocking(check(net, P([{"action": "set_line_status", "line": b["line"], "status": "out"}])))
        assert [i.code for i in iss] == ["R8_ISLANDING"]
        assert str(b["cut_off"][0]) in iss[0].message


def test_r8_not_fired_for_meshed_line(net):
    assert codes(net, P([{"action": "set_line_status", "line": "L38", "status": "out"}])) == []


def test_r9_adequacy(net):
    iss = blocking(check(net, P([{"action": "set_gen_status", "gen": "G1", "status": "out"}])))
    assert [i.code for i in iss] == ["R9_ADEQUACY"]
    assert "92.0 MW" in iss[0].message and "283.4 MW" in iss[0].message


def test_r10_infeasible(net):
    # Passes R1-R9 (capacity 363 MW >= load) but derating the two lines out of
    # bus 1 traps G1, so no dispatch serves the load.
    es = P([{"action": "set_line_rating", "line": "L1", "rate_mw": 10},
            {"action": "set_line_rating", "line": "L2", "rate_mw": 10}])
    assert codes(net, es) == []
    res = solve_dcopf(apply_edits(net, es))
    assert infeasible_issue(res).code == "R10_INFEASIBLE"
    assert infeasible_issue(solve_dcopf(net)) is None


def test_ref_gen_out_is_note_and_new_slack(net):
    es = P([{"action": "set_gen_status", "gen": "G1", "status": "out"},
            {"action": "scale_load", "bus": 5, "factor": 0}])
    issues = check(net, es)
    assert any(i.code == "NOTE_SLACK" for i in issues)
    n2 = apply_edits(net, es)
    pf = dc_power_flow(n2, {"G2": 50})
    assert pf["slack"] == "G2"


def test_messages_are_one_sentence(net):
    samples = [
        parse_edits("oops"),
        P([{"action": "set_line_status", "line": "L42", "status": "out"}]),
        P([{"action": "scale_load", "bus": 7, "factor": 15}]),
        P([{"action": "set_gen_limits", "gen": "G2", "pmax_mw": 500, "pmin_mw": None}]),
        P([{"action": "set_line_rating", "line": "L4", "rate_mw": 200}]),
        P([{"action": "set_load", "bus": 7, "pd_mw": 1}, {"action": "set_load", "bus": 7, "pd_mw": 2}]),
        P([]),
        P([{"action": "set_line_status", "line": "L13", "status": "out"}]),
        P([{"action": "set_gen_status", "gen": "G1", "status": "out"}]),
    ]
    print()
    for s in samples:
        for i in blocking(check(net, s)):
            print(f"{i.code:<15} {i.message}")
            assert "Traceback" not in i.message and i.message.endswith(".")
    print(f"{'R10_INFEASIBLE':<15} {infeasible_issue({'status': 'infeasible'}).message}")
