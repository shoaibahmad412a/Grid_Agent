"""Mode 4 verifier: re-check a solved result and write a template explanation
(no LLM). Numbering: L1.., G1.., buses by file number. MW, $/h, %.
"""
from __future__ import annotations

import re

from src.dcpf import dc_power_flow
from src.edits import canonical_diff
from src.network import Network


def verify(base_net: Network, edited_net: Network, result: dict, request_text: str) -> dict:
    """Re-check an OPF result on the edited network with an independent DC power flow.

    Checks: balance residual (MW, tolerance 1e-4), generator limits and line
    loading <= 100% (tolerance 1e-3 MW). Also lists 'extra changes': changed
    elements the request never mentions by id or end buses (warning only).
    Returns {ok, balance_residual_mw, gen_violations, line_violations,
    max_loading_pct, extra_changes}.
    """
    pf = dc_power_flow(edited_net, result.get("pg", {}))
    ok = pf.get("status") == "ok" and pf["feasible_strict"]
    diff = canonical_diff(base_net, edited_net)
    return {
        "ok": bool(ok),
        "balance_residual_mw": abs(pf.get("mismatch_mw", float("nan"))),
        "gen_violations": pf.get("gen_violations", []),
        "line_violations": pf.get("line_violations", []),
        "max_loading_pct": pf.get("max_loading_pct"),
        "extra_changes": extra_changes(base_net, diff, request_text),
    }


def _numbers(text: str) -> set[int]:
    return {int(n) for n in re.findall(r"(?<![\d.])\d+(?![\d.])", text)}


def mentioned(base_net: Network, element: str, text: str) -> bool:
    """Does the request text refer to this element ('L12', 'G5', 'bus7')?

    Lines: 'L12', or both end bus numbers, or 'line/branch 12'.
    Generators: 'G5', 'gen/generator/unit 5', or 'bus N' of its bus.
    Buses: the bus number appears in the text.
    """
    t = text.lower()
    nums = _numbers(t)
    if element.startswith("bus"):
        return int(element[3:]) in nums
    k = int(element[1:])
    if element.startswith("L"):
        if re.search(rf"\bl\s*-?\s*{k}\b", t):
            return True
        i = k - 1
        if {int(base_net.br_from[i]), int(base_net.br_to[i])} <= nums:
            return True
        return bool(re.search(rf"\b(line|branch|transformer)\s*#?\s*{k}\b", t))
    if re.search(rf"\bg\s*-?\s*{k}\b", t) or re.search(rf"\b(gen|generator|unit)\s*#?\s*{k}\b", t):
        return True
    return int(base_net.gen_bus[k - 1]) in nums and "bus" in t


def extra_changes(base_net: Network, diff: dict, text: str) -> list[str]:
    """Changed elements (from a canonical diff) that the request does not mention."""
    elems = sorted({key.split(".")[0] for key in diff})
    return [e for e in elems if not mentioned(base_net, e, text)]


def _describe(base_net: Network, diff: dict) -> list[str]:
    parts = []
    by_elem: dict[str, dict] = {}
    for key, v in diff.items():
        e, f = key.split(".")
        by_elem.setdefault(e, {})[f] = v
    for e, f in by_elem.items():
        if e.startswith("bus"):
            i = base_net.bus_index(int(e[3:]))
            parts.append(f"bus {e[3:]} load {base_net.pd[i]:.1f} -> {f['Pd']:.1f} MW")
        elif e.startswith("L"):
            if "status" in f:
                parts.append(f"{e} {'out of service' if f['status'] == 0 else 'back in service'}")
            if "rate_a" in f:
                k = base_net.line_index(e)
                parts.append(f"{e} rating {base_net.rate_a[k]:g} -> {f['rate_a']:g} MW")
        else:
            k = base_net.gen_index(e)
            if f.get("status") == 0:
                parts.append(f"{e} out of service")
                continue
            if f.get("status") == 1:
                parts.append(f"{e} back in service")
            if "Pmax" in f:
                parts.append(f"{e} Pmax {base_net.pmax[k]:g} -> {f['Pmax']:g} MW")
            if "Pmin" in f:
                parts.append(f"{e} Pmin {base_net.pmin[k]:g} -> {f['Pmin']:g} MW")
            if any(c in f for c in ("c2", "c1", "c0")):
                parts.append(f"{e} cost {base_net.c1[k]:.2f} -> {f.get('c1', base_net.c1[k]):.2f} $/MWh")
    return parts


def explain(base_net: Network, edited_net: Network | None, result: dict | None,
            base_cost: float | None, refused: bool = False, reason: str = "") -> str:
    """Fixed-template explanation for the operator (no LLM, cannot invent facts).

    'Changes applied: ... New cost: X $/h (+Y% vs base). Most loaded line: Lk at Z%.'
    For refusals: 'Refused: <reason>.'
    """
    if refused:
        return f"Refused: {reason.strip().rstrip('.')}."
    if edited_net is None or not result or result.get("status") != "optimal":
        return "No dispatch: the request could not be solved."
    parts = _describe(base_net, canonical_diff(base_net, edited_net))
    changes = "; ".join(parts) if parts else "none"
    cost = result["cost"]
    pct = (cost - base_cost) / base_cost * 100 if base_cost else 0.0
    loads = {l: v for l, v in result["loading_pct"].items() if v is not None}
    top = max(loads, key=loads.get) if loads else None
    text = f"Changes applied: {changes}. New cost: {cost:,.0f} $/h ({pct:+.1f}% vs base)."
    if top:
        text += f" Most loaded line: {top} at {loads[top]:.0f}%."
    return text
