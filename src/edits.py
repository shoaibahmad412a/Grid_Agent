"""Edit language: parse the LLM's JSON, apply edits to a network copy, and
compute the canonical diff used for grading.

Numbering: lines L1.., generators G1.., buses by file number. MW throughout.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field

from src.network import Network

NUM = "number"
NUM_OR_NULL = "number_or_null"
INT = "integer"
STR = "string"
STATUS = "status"

SCHEMA = {
    "set_line_status": {"line": STR, "status": STATUS},
    "set_line_rating": {"line": STR, "rate_mw": NUM},
    "set_gen_limits": {"gen": STR, "pmax_mw": NUM_OR_NULL, "pmin_mw": NUM_OR_NULL},
    "set_gen_status": {"gen": STR, "status": STATUS},
    "set_load": {"bus": INT, "pd_mw": NUM},
    "scale_load": {"bus": INT, "factor": NUM},
    "scale_cost": {"gen": STR, "factor": NUM},
}
TOP_KEYS = {"edits", "refuse", "reason"}


@dataclass
class Edit:
    """One validated edit. Values are exactly as given by the LLM (MW, factors)."""
    action: str
    args: dict

    def to_dict(self) -> dict:
        return {"action": self.action, **self.args}


@dataclass
class EditSet:
    edits: list[Edit] = field(default_factory=list)
    refuse: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return {"edits": [e.to_dict() for e in self.edits], "refuse": self.refuse, "reason": self.reason}


@dataclass
class ParseError:
    """Reply could not be read. kind: 'BAD_JSON' (not one JSON object) or
    'SCHEMA' (valid JSON that breaks the edit schema)."""
    kind: str
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


def strip_fences(text: str) -> str:
    """Remove surrounding whitespace and one markdown code fence, if present."""
    t = text.strip()
    m = re.fullmatch(r"```[a-zA-Z]*\s*\n?(.*?)\n?\s*```", t, flags=re.S)
    return m.group(1).strip() if m else t


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _type_ok(kind: str, v) -> bool:
    if kind == NUM:
        return _is_num(v)
    if kind == NUM_OR_NULL:
        return v is None or _is_num(v)
    if kind == INT:
        return isinstance(v, int) and not isinstance(v, bool)
    if kind == STR:
        return isinstance(v, str)
    if kind == STATUS:
        return v in ("out", "in")
    raise ValueError(kind)


_TYPE_WORDS = {NUM: "a number", NUM_OR_NULL: "a number or null", INT: "an integer bus number",
               STR: "a string id", STATUS: '"out" or "in"'}


def parse_edits(text: str) -> EditSet | ParseError:
    """Parse one JSON object in the edit schema (see CLAUDE.md).

    Accepts the object alone or inside one markdown code fence. Rejects
    unknown actions, unknown or missing keys and wrong types. Never raises.
    """
    if not isinstance(text, str):
        return ParseError("BAD_JSON", "The reply was empty; reply with one JSON object.")
    try:
        obj = json.loads(strip_fences(text))
    except (json.JSONDecodeError, ValueError):
        return ParseError("BAD_JSON", "The reply is not valid JSON; reply with exactly one JSON object and nothing else.")
    if not isinstance(obj, dict):
        return ParseError("BAD_JSON", "The reply must be one JSON object, not a list or a value.")
    keys = set(obj)
    if keys != TOP_KEYS:
        missing, extra = TOP_KEYS - keys, keys - TOP_KEYS
        bits = []
        if missing:
            bits.append("missing " + ", ".join(sorted(missing)))
        if extra:
            bits.append("unknown " + ", ".join(sorted(extra)))
        return ParseError("SCHEMA", f"The top-level object must have exactly the keys edits, refuse and reason ({'; '.join(bits)}).")
    if not isinstance(obj["edits"], list):
        return ParseError("SCHEMA", 'The key "edits" must be a list of edit objects.')
    if not isinstance(obj["refuse"], bool):
        return ParseError("SCHEMA", 'The key "refuse" must be true or false.')
    if not isinstance(obj["reason"], str):
        return ParseError("SCHEMA", 'The key "reason" must be a text string.')

    edits = []
    allowed = ", ".join(SCHEMA)
    for i, e in enumerate(obj["edits"], start=1):
        if not isinstance(e, dict) or "action" not in e:
            return ParseError("SCHEMA", f"Edit {i} must be an object with an \"action\" key; allowed actions: {allowed}.")
        act = e["action"]
        if act not in SCHEMA:
            return ParseError("SCHEMA", f"Edit {i} has unknown action {act!r}; allowed actions: {allowed}.")
        spec = SCHEMA[act]
        got = set(e) - {"action"}
        if got != set(spec):
            return ParseError("SCHEMA", f"Edit {i} ({act}) must have exactly the keys {', '.join(spec)}.")
        for k, kind in spec.items():
            if not _type_ok(kind, e[k]):
                return ParseError("SCHEMA", f"Edit {i} ({act}): {k} must be {_TYPE_WORDS[kind]}.")
        if act == "set_gen_limits" and e["pmax_mw"] is None and e["pmin_mw"] is None:
            return ParseError("SCHEMA", f"Edit {i} (set_gen_limits): give pmax_mw, pmin_mw or both; null means unchanged.")
        edits.append(Edit(act, {k: e[k] for k in spec}))
    return EditSet(edits, obj["refuse"], obj["reason"])


def edit_writes(base: Network, e: Edit) -> dict:
    """The parameter values one edit writes, as {(element, field): value}.

    Scale edits are relative to the BASE network (they never compound).
    Element names use operator ids: 'L12', 'G5', 'bus7'. Requires valid ids.
    """
    a = e.args
    if e.action == "set_line_status":
        return {(a["line"], "status"): 1 if a["status"] == "in" else 0}
    if e.action == "set_line_rating":
        return {(a["line"], "rate_a"): float(a["rate_mw"])}
    if e.action == "set_gen_limits":
        w = {}
        if a["pmax_mw"] is not None:
            w[(a["gen"], "Pmax")] = float(a["pmax_mw"])
        if a["pmin_mw"] is not None:
            w[(a["gen"], "Pmin")] = float(a["pmin_mw"])
        return w
    if e.action == "set_gen_status":
        k = base.gen_index(a["gen"])
        if a["status"] == "out":
            return {(a["gen"], "status"): 0, (a["gen"], "Pmax"): 0.0, (a["gen"], "Pmin"): 0.0}
        return {(a["gen"], "status"): 1, (a["gen"], "Pmax"): float(base.pmax_file[k]),
                (a["gen"], "Pmin"): float(base.pmin_file[k])}
    if e.action == "set_load":
        return {(f"bus{a['bus']}", "Pd"): float(a["pd_mw"])}
    if e.action == "scale_load":
        return {(f"bus{a['bus']}", "Pd"): float(base.pd[base.bus_index(a["bus"])] * a["factor"])}
    if e.action == "scale_cost":
        k = base.gen_index(a["gen"])
        f = float(a["factor"])
        return {(a["gen"], "c2"): base.c2[k] * f, (a["gen"], "c1"): base.c1[k] * f,
                (a["gen"], "c0"): base.c0[k] * f}
    raise ValueError(e.action)


def apply_edits(net: Network, editset: EditSet) -> Network:
    """Return a new Network with the edits applied; the input is never changed.

    Edits are applied in list order; scale edits use the input network's value
    (no compounding). set_gen_status 'out' sets Pmin = Pmax = 0; 'in' restores
    the file Pmin/Pmax. scale_cost multiplies c2, c1 and c0 by the factor.
    Raises KeyError for an unknown id (the checker catches these first).
    """
    new = net.copy()
    for e in editset.edits:
        for (elem, fld), v in edit_writes(net, e).items():
            _set(new, elem, fld, v)
    return new


def _set(net: Network, elem: str, fld: str, v) -> None:
    if elem.startswith("bus"):
        net.pd[net.bus_index(int(elem[3:]))] = v
    elif elem.startswith("G"):
        k = net.gen_index(elem)
        arr = {"status": net.gen_status, "Pmax": net.pmax, "Pmin": net.pmin,
               "c2": net.c2, "c1": net.c1, "c0": net.c0}[fld]
        arr[k] = v
    elif elem.startswith("L"):
        k = net.line_index(elem)
        {"status": net.br_status, "rate_a": net.rate_a}[fld][k] = v
    else:
        raise KeyError(elem)


def canonical_diff(base: Network, edited: Network) -> dict:
    """Every changed parameter with its final value, floats rounded to 0.01.

    Keys: 'bus7.Pd' (MW), 'G5.status' (0/1), 'G5.Pmax'/'G5.Pmin' (MW),
    'G3.c2'/'G3.c1'/'G3.c0' (cost coefficients), 'L12.status' (0/1),
    'L4.rate_a' (MW). Two edit sets with the same effect give the same dict.
    """
    out = {}

    def cmp(key, a, b, is_int=False):
        if is_int:
            if int(a) != int(b):
                out[key] = int(b)
        elif round(float(a), 2) != round(float(b), 2):
            out[key] = round(float(b), 2)

    for i, n in enumerate(base.bus_num):
        cmp(f"bus{n}.Pd", base.pd[i], edited.pd[i])
    for k, g in enumerate(base.gen_ids):
        cmp(f"{g}.status", base.gen_status[k], edited.gen_status[k], True)
        cmp(f"{g}.Pmax", base.pmax[k], edited.pmax[k])
        cmp(f"{g}.Pmin", base.pmin[k], edited.pmin[k])
        for c in ("c2", "c1", "c0"):
            cmp(f"{g}.{c}", getattr(base, c)[k], getattr(edited, c)[k])
    for k, l in enumerate(base.line_ids):
        cmp(f"{l}.status", base.br_status[k], edited.br_status[k], True)
        cmp(f"{l}.rate_a", base.rate_a[k], edited.rate_a[k])
    return out


def editset_from_obj(obj: dict) -> EditSet | ParseError:
    """Parse an already-loaded dict (e.g. a ground-truth line) with the same rules."""
    return parse_edits(json.dumps(obj))
