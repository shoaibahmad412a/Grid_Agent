"""Step 1: network model and base-case DC-OPF."""
import numpy as np
import pytest

from src.dcopf import solve_dcopf
from src.dcpf import dc_power_flow
from src.network import network_card


def test_case_loads(net):
    assert net.baseMVA == 100
    assert (net.n_bus, net.n_gen, net.n_branch) == (30, 6, 41)
    assert net.ref_bus == 1
    assert net.gen_ids[0] == "G1" and net.line_ids[-1] == "L41"
    assert np.all(net.br_tap > 0)  # tap 0 in the file is stored as 1


def test_id_mapping(net):
    assert net.gen_index("G1") == 0 and net.line_index("L12") == 11 and net.bus_index(30) == 29
    for bad in ("G0", "G7", "L42", "X1", 5):
        with pytest.raises(KeyError):
            (net.line_index if str(bad).startswith("L") else net.gen_index)(bad)


def test_copy_is_independent(net):
    n2 = net.copy()
    n2.pd[0] = 999
    assert net.pd[0] != 999


def test_base_case_solves_and_respects_limits(net):
    r = solve_dcopf(net)
    assert r["status"] == "optimal"
    assert r["balance_residual_mw"] < 1e-4
    assert abs(sum(r["pg"].values()) - net.total_load_mw()) < 1e-4
    for k, g in enumerate(net.gen_ids):
        assert net.pmin[k] - 1e-3 <= r["pg"][g] <= net.pmax[k] + 1e-3
    for k, l in enumerate(net.line_ids):
        if net.rate_a[k] > 0:
            assert abs(r["flow"][l]) <= net.rate_a[k] + 1e-3


def test_flow_formula_by_hand(net):
    r = solve_dcopf(net)
    k = net.line_index("L11")  # 6-9 transformer, tap 0.978
    th = r["theta"]
    hand = 100 * (th[6] - th[9]) / (net.br_x[k] * net.br_tap[k])
    assert abs(hand - r["flow"]["L11"]) < 1e-6


def test_second_solver_agrees(net):
    a = solve_dcopf(net, "CLARABEL")
    b = solve_dcopf(net, "OSQP")
    assert b["status"] == "optimal"
    assert abs(a["cost"] - b["cost"]) / a["cost"] < 1e-3


def test_lmps_positive_and_match_perturbation(net):
    r = solve_dcopf(net)
    assert min(r["lmp"].values()) > 0
    n2 = net.copy()
    n2.pd[net.bus_index(7)] += 1.0
    d = solve_dcopf(n2)["cost"] - r["cost"]
    assert abs(d - r["lmp"][7]) < 0.05


def test_no_line_limits_gives_equal_lmps(net):
    n2 = net.copy()
    n2.rate_a[:] = 0  # 0 = no limit
    r = solve_dcopf(n2)
    lmps = np.array(list(r["lmp"].values()))
    assert r["status"] == "optimal"
    assert lmps.max() - lmps.min() < 1e-3


def test_dcpf_reproduces_opf_flows(net):
    r = solve_dcopf(net)
    pf = dc_power_flow(net, r["pg"])
    assert abs(pf["mismatch_mw"]) < 1e-4
    assert pf["feasible_strict"]
    for l in net.line_ids:
        assert abs(pf["flow"][l] - r["flow"][l]) < 1e-5


def test_dcpf_reports_violations(net):
    pf = dc_power_flow(net, {"G1": 283.4})  # above G1 Pmax 271
    assert pf["status"] == "ok"
    assert any(v["gen"] == "G1" for v in pf["gen_violations"])
    assert not pf["feasible"]


def test_dcpf_slack_absorbs_mismatch(net):
    pf = dc_power_flow(net, {"G1": 200.0, "G2": 80.0})
    assert pf["slack"] == "G1"
    assert abs(pf["mismatch_mw"] - 3.4) < 1e-9
    assert abs(pf["pg_final"]["G1"] - 203.4) < 1e-9
    assert not pf["feasible_strict"]


def test_infeasible_never_crashes(net):
    n2 = net.copy()
    n2.pd = n2.pd * 3  # far above 363 MW capacity
    assert solve_dcopf(n2)["status"] == "infeasible"


def test_network_card_uses_operator_ids(net):
    card = network_card(net)
    assert "G1" in card and "L41" in card and "bus 30" in card
    assert "L0" not in card and "G0" not in card


def test_pandapower_comparison(net):
    """Report (not force) agreement with pandapower's DC-OPF."""
    pp = pytest.importorskip("pandapower")
    from pandapower.converter.matpower import from_mpc
    try:
        ppnet = from_mpc("data/pglib_opf_case30_ieee.m", f_hz=60)
        pp.rundcopp(ppnet)
        cost = float(ppnet.res_cost)
    except Exception as exc:
        pytest.skip(f"pandapower could not run the case: {exc}")
    ours = solve_dcopf(net)["cost"]
    print(f"pandapower cost {cost:.2f} $/h vs ours {ours:.2f} $/h, diff {(cost - ours) / ours * 100:+.3f}%")
