"""Rule checker for edit sets (R1-R10). Messages go back to the LLM, so each
is one plain sentence naming the element and what is allowed.

Numbering: L1.., G1.., buses by file number. MW throughout.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import networkx as nx

from src.edits import EditSet, ParseError, apply_edits, edit_writes
from src.network import Network

LOAD_FACTOR_RANGE = (0.0, 3.0)
COST_FACTOR_RANGE = (0.1, 10.0)
TOL_MW = 1e-3


@dataclass
class CheckIssue:
    code: str      # e.g. "R8_ISLANDING"
    type: str      # "format" | "physics" | "note" (notes never block)
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


def blocking(issues: list[CheckIssue]) -> list[CheckIssue]:
    """Issues that reject the edit set (everything except notes)."""
    return [i for i in issues if i.type != "note"]


# ---- helpers -----------------------------------------------------------
def _id_issue(net: Network, kind: str, value) -> CheckIssue | None:
    """R2 for one id. kind: 'line' | 'gen' | 'bus'."""
    if kind == "bus":
        if net.has_bus(value):
            return None
        lo, hi = int(net.bus_num.min()), int(net.bus_num.max())
        msg = f"Bus {value} does not exist; valid buses are {lo} to {hi}"
        if value in (0, hi + 1):
            msg += " (numbering starts at 1)"
        return CheckIssue("R2_UNKNOWN_ID", "format", msg + ".")
    prefix, n, name = ("L", net.n_branch, "Line") if kind == "line" else ("G", net.n_gen, "Generator")
    try:
        (net.line_index if kind == "line" else net.gen_index)(value)
        return None
    except KeyError:
        pass
    msg = f"{name} {value!r} does not exist; valid ids are {prefix}1 to {prefix}{n}"
    num = str(value)[1:] if isinstance(value, str) and value[:1] == prefix else None
    if num is not None and num.isdigit() and int(num) in (0, n + 1):
        msg += " (numbering starts at 1)"
    elif kind == "line":
        msg += ", written like L12; to name a line by its end buses, look it up in the branch table"
    return CheckIssue("R2_UNKNOWN_ID", "format", msg + ".")


def _target(e) -> tuple[str, object]:
    a = e.args
    if "line" in a:
        return "line", a["line"]
    if "gen" in a:
        return "gen", a["gen"]
    return "bus", a["bus"]


def _element_name(kind: str, value) -> str:
    return f"bus {value}" if kind == "bus" else str(value)


# ---- rules -------------------------------------------------------------
def check(base_net: Network, editset: EditSet | ParseError) -> list[CheckIssue]:
    """Run R1-R9 in order and return all issues found (empty = passes).

    R1/R2 problems stop the rules that need a valid network (R4, R5, R8, R9).
    R10 (infeasible OPF) is checked after solving: see infeasible_issue().
    A note (type 'note') is added when the reference-bus generator is taken out.
    """
    # R1 SCHEMA
    if isinstance(editset, ParseError):
        return [CheckIssue("R1_SCHEMA", "format", editset.message)]

    issues: list[CheckIssue] = []
    # R2 UNKNOWN_ID
    valid = []
    for e in editset.edits:
        kind, val = _target(e)
        iss = _id_issue(base_net, kind, val)
        if iss:
            issues.append(iss)
        else:
            valid.append(e)
    ids_ok = len(valid) == len(editset.edits)

    # R3 RANGE
    for e in editset.edits:
        a, name = e.args, _element_name(*_target(e))
        if e.action == "set_load" and a["pd_mw"] < 0:
            issues.append(CheckIssue("R3_RANGE", "physics", f"Load at {name} cannot be negative ({a['pd_mw']} MW); use 0 MW or more."))
        if e.action == "scale_load" and not LOAD_FACTOR_RANGE[0] <= a["factor"] <= LOAD_FACTOR_RANGE[1]:
            issues.append(CheckIssue("R3_RANGE", "physics", f"Load factor {a['factor']} for {name} is outside the allowed range 0 to 3 (for example +15% is 1.15)."))
        if e.action == "set_line_rating" and a["rate_mw"] <= 0:
            issues.append(CheckIssue("R3_RANGE", "physics", f"Rating of {name} must be above 0 MW; to take the line out use set_line_status."))
        if e.action == "set_gen_limits":
            for k in ("pmax_mw", "pmin_mw"):
                if a[k] is not None and a[k] < 0:
                    issues.append(CheckIssue("R3_RANGE", "physics", f"{k} of {name} cannot be negative ({a[k]} MW)."))
        if e.action == "scale_cost" and not COST_FACTOR_RANGE[0] <= a["factor"] <= COST_FACTOR_RANGE[1]:
            issues.append(CheckIssue("R3_RANGE", "physics", f"Cost factor {a['factor']} for {name} is outside the allowed range 0.1 to 10."))

    # R4 NAMEPLATE and R5 UPRATE (need valid ids)
    for e in valid:
        a = e.args
        if e.action == "set_gen_limits":
            k = base_net.gen_index(a["gen"])
            plate = base_net.pmax_file[k]
            pmax = a["pmax_mw"] if a["pmax_mw"] is not None else base_net.pmax[k]
            if a["pmax_mw"] is not None and a["pmax_mw"] > plate + TOL_MW:
                issues.append(CheckIssue("R4_NAMEPLATE", "physics", f"{a['gen']} Pmax {a['pmax_mw']:g} MW is above its rating of {plate:g} MW; Pmax can be at most {plate:g} MW."))
            if a["pmin_mw"] is not None and a["pmin_mw"] > pmax + TOL_MW:
                issues.append(CheckIssue("R4_NAMEPLATE", "physics", f"{a['gen']} Pmin {a['pmin_mw']:g} MW is above its Pmax of {pmax:g} MW; Pmin must not exceed Pmax."))
        if e.action == "set_line_rating":
            k = base_net.line_index(a["line"])
            orig = base_net.rate_a[k]
            if orig > 0 and a["rate_mw"] > orig + TOL_MW:
                issues.append(CheckIssue("R5_UPRATE", "physics", f"{a['line']} rating {a['rate_mw']:g} MW is above its original {orig:g} MW; only derates (lower ratings) are allowed."))

    # R6 CONFLICT
    seen: dict = {}
    for i, e in enumerate(valid, start=1):
        for key, v in edit_writes(base_net, e).items():
            if key in seen and round(float(seen[key][1]), 2) != round(float(v), 2):
                elem = key[0].replace("bus", "bus ")
                issues.append(CheckIssue("R6_CONFLICT", "format", f"Two edits set {key[1]} of {elem} to different values ({e.action} vs {seen[key][0]}); keep only one."))
            seen.setdefault(key, (e.action, v))

    # R7 EMPTY
    if not editset.refuse and not editset.edits:
        issues.append(CheckIssue("R7_EMPTY", "format", "There are no edits and refuse is false; give at least one edit, or set refuse to true with a reason."))
    if editset.refuse and editset.edits:
        issues.append(CheckIssue("R7_EMPTY", "format", "A refusal must have an empty edits list; either refuse with no edits or give edits with refuse false."))
    if editset.refuse and not editset.reason.strip():
        issues.append(CheckIssue("R7_EMPTY", "format", "A refusal needs a short reason the operator can read."))

    if not ids_ok or editset.refuse:
        return issues

    edited = apply_edits(base_net, editset)
    # R8 ISLANDING
    cut = islanded_buses(edited)
    if cut:
        outs = [e.args["line"] for e in editset.edits if e.action == "set_line_status" and e.args["status"] == "out"]
        what = " and ".join(outs) if outs else "these edits"
        buses = ", ".join(str(b) for b in cut)
        issues.append(CheckIssue("R8_ISLANDING", "physics", f"Taking {what} out would cut bus{'es' if len(cut) > 1 else ''} {buses} off from the grid; outages that island buses are not allowed."))
    # R9 ADEQUACY
    cap, load = edited.total_capacity_mw(), edited.total_load_mw()
    if cap < load - TOL_MW:
        issues.append(CheckIssue("R9_ADEQUACY", "physics", f"In-service generation capacity is {cap:.1f} MW but the load is {load:.1f} MW, so the load cannot be served."))
    # Note: reference-bus generator out (allowed; the power flow picks a new slack)
    for e in editset.edits:
        if e.action == "set_gen_status" and e.args["status"] == "out":
            k = base_net.gen_index(e.args["gen"])
            if base_net.gen_bus[k] == base_net.ref_bus:
                issues.append(CheckIssue("NOTE_SLACK", "note", f"{e.args['gen']} is at the reference bus {base_net.ref_bus}; the largest in-service unit becomes the slack."))
    return issues


def islanded_buses(net: Network) -> list[int]:
    """File numbers of buses not connected to the reference bus over in-service branches."""
    g = nx.Graph()
    g.add_nodes_from(int(b) for b in net.bus_num)
    g.add_edges_from((int(f), int(t)) for f, t, s in zip(net.br_from, net.br_to, net.br_status) if s)
    main = nx.node_connected_component(g, net.ref_bus)
    return sorted(int(b) for b in net.bus_num if int(b) not in main)


def infeasible_issue(opf_result: dict) -> CheckIssue | None:
    """R10: the edits passed R1-R9 but the DC-OPF found no feasible dispatch."""
    if opf_result.get("status") == "optimal":
        return None
    return CheckIssue("R10_INFEASIBLE", "physics", "The edits pass all rule checks but no safe dispatch exists (the optimal power flow is infeasible); check the edits or refuse the request.")


def bridge_branches(net: Network) -> list[dict]:
    """In-service branches whose outage islands buses: [{line, from, to, cut_off: [bus..]}]."""
    out = []
    for k, lid in enumerate(net.line_ids):
        if not net.br_status[k]:
            continue
        n2 = net.copy()
        n2.br_status[k] = 0
        cut = islanded_buses(n2)
        if cut:
            out.append({"line": lid, "from": int(net.br_from[k]), "to": int(net.br_to[k]), "cut_off": cut})
    return out
