"""The four agent structures (modes). The agent never sees ground truth.

M1 direct dispatch, M2 edits -> DC-OPF, M3 = M2 + checker with retries,
M4 = M3 + verifier and template explanation. Numbering: operator ids only.
"""
from __future__ import annotations

import hashlib
import json
import time

from src.checker import blocking, check, infeasible_issue
from src.dcopf import solve_dcopf
from src.edits import EditSet, ParseError, apply_edits, canonical_diff, parse_edits, strip_fences
from src.llm import LLM, CallLogger
from src.network import Network, network_card
from src.verifier import explain, verify

MAX_CHECK_RETRIES = 2   # retries after checker rejections (M3, M4)
MAX_R10_RETRIES = 1     # one extra retry when the OPF is infeasible (M3, M4)

# ---- prompts (frozen after Step 6) -------------------------------------
# The two examples are generic and written separately from the benchmark.
EXAMPLES_EDITS = """EXAMPLES (generic, for format only)
Request: Crews have opened line L23, and demand at bus 18 is now 4.5 MW.
Reply: {"edits": [{"action": "set_line_status", "line": "L23", "status": "out"}, {"action": "set_load", "bus": 18, "pd_mw": 4.5}], "refuse": false, "reason": ""}
Request: Raise the voltage at bus 3 to 1.05 p.u.
Reply: {"edits": [], "refuse": true, "reason": "Voltage set-points are not part of this DC model, so this request cannot be represented."}"""

SYSTEM_EDITS = """You translate grid operator requests into model edits for a DC optimal power flow of the IEEE 30-bus system. A solver then computes the dispatch from your edits.

RULES
- Output exactly one JSON object in the schema below and nothing else: no prose, no markdown.
- Use only the ids in the grid description: buses by number (1-30), generators G1-G6, branches L1-L41. If the operator names a line by its end buses, find its id in the branch table.
- All power values are in MW. Percentages must be converted: a 15% rise is factor 1.15; "derated to 60%" of a unit means 0.6 times its Pmax in MW.
- Only change what the request asks for.
- If the request cannot be done safely or cannot be represented with these edits, set "refuse" to true, give a short reason, and leave "edits" empty.

SCHEMA
{"edits": [ ... ], "refuse": true or false, "reason": "text"}
Allowed edit objects:
{"action": "set_line_status", "line": "L12", "status": "out" or "in"}
{"action": "set_line_rating", "line": "L4", "rate_mw": number}
{"action": "set_gen_limits", "gen": "G2", "pmax_mw": number or null, "pmin_mw": number or null}   (null = unchanged)
{"action": "set_gen_status", "gen": "G5", "status": "out" or "in"}
{"action": "set_load", "bus": 7, "pd_mw": number}
{"action": "scale_load", "bus": 7, "factor": number}
{"action": "scale_cost", "gen": "G3", "factor": number}
Line ratings can only be lowered, never raised. Generator Pmax can never exceed its value in the table. Outages that cut any bus off from the grid are not allowed.

{examples}

{card}"""

SYSTEM_DIRECT = """You are a grid dispatcher for a DC model of the IEEE 30-bus system. Given the operator's request, decide the active power output of every generator for the situation the request describes.

RULES
- Reply with exactly one JSON object and nothing else: {"G1": MW, "G2": MW, "G3": MW, "G4": MW, "G5": MW, "G6": MW, "refuse": false, "reason": ""}
- Total generation must equal total load (the DC model is lossless). Respect every generator limit and every branch rating.
- If the request cannot be done safely, reply {"refuse": true, "reason": "short reason"} (generator values may be omitted).

{card}

CURRENT BASE-CASE DISPATCH (optimal before the request; cost {cost:.0f} $/h)
{dispatch}
Most loaded branches: {top}"""

FEEDBACK = "Your edits were rejected: {messages} Fix them, or set refuse to true if the request cannot be done safely."


def system_prompt_edits(net: Network) -> str:
    return SYSTEM_EDITS.replace("{examples}", EXAMPLES_EDITS).replace("{card}", network_card(net))


def system_prompt_direct(net: Network, base: dict) -> str:
    dispatch = ", ".join(f"{g}: {v:.1f} MW" for g, v in base["pg"].items())
    loads = sorted(((v, l) for l, v in base["loading_pct"].items() if v is not None), reverse=True)[:5]
    top = ", ".join(f"{l} {v:.0f}%" for v, l in loads)
    return (SYSTEM_DIRECT.replace("{card}", network_card(net)).replace("{dispatch}", dispatch)
            .replace("{top}", top).replace("{cost:.0f}", f"{base['cost']:.0f}"))


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


# ---- shared helpers ----------------------------------------------------
class _Session:
    """Holds the conversation, totals and the call log for one request."""

    def __init__(self, mode, request, llm: LLM, logger: CallLogger | None, system: str):
        self.mode, self.request, self.llm, self.logger = mode, request, llm, logger
        self.messages = [{"role": "system", "content": system}, {"role": "user", "content": request["text"]}]
        self.attempts: list[dict] = []
        self.tokens_in = self.tokens_out = 0
        self.latency = 0.0
        self.energy: float | None = None
        self.energy_note = None
        self.model = llm.model
        self.system_hash = prompt_hash(system)

    def ask(self) -> tuple[str, dict]:
        text, meta = self.llm.complete(self.messages, self.request["id"])
        self.model = meta.get("model") or self.model
        self.latency += meta.get("latency_s") or 0.0
        self.tokens_in += meta.get("tokens_in") or 0
        self.tokens_out += meta.get("tokens_out") or 0
        if meta.get("energy_kwh") is not None:
            self.energy = (self.energy or 0.0) + meta["energy_kwh"]
        self.energy_note = meta.get("energy_note")
        self.messages.append({"role": "assistant", "content": text})
        return text, meta

    def log(self, text, meta, parsed, issues=None):
        att = {"attempt": len(self.attempts) + 1, "raw_reply": text, "parsed": parsed,
               "issues": [i.to_dict() for i in issues or []], "error": meta.get("error")}
        self.attempts.append(att)
        if self.logger:
            self.logger.log({"mode": self.mode, "request_id": self.request["id"], "attempt": att["attempt"],
                             "model": meta.get("model"), "prompt": self.messages[:-1], "raw_reply": text,
                             "parsed": parsed, "issues": att["issues"], "latency_s": meta.get("latency_s"),
                             "tokens_in": meta.get("tokens_in"), "tokens_out": meta.get("tokens_out"),
                             "energy_kwh": meta.get("energy_kwh"), "energy_note": meta.get("energy_note"),
                             "error": meta.get("error"), "time": time.strftime("%Y-%m-%dT%H:%M:%S")})

    def record(self, **kw) -> dict:
        rec = {"id": self.request["id"], "group": self.request.get("group"), "mode": self.mode,
               "model": self.model, "backend": self.llm.name, "system_prompt_sha": self.system_hash,
               "attempts": self.attempts, "retries_used": max(0, len(self.attempts) - 1),
               "latency_s": self.latency, "tokens_in": self.tokens_in, "tokens_out": self.tokens_out,
               "energy_kwh": self.energy, "energy_note": self.energy_note,
               "raw_reply": self.attempts[-1]["raw_reply"] if self.attempts else None}
        rec.update(kw)
        return rec


def _parsed_dict(p):
    return p.to_dict() if isinstance(p, (EditSet, ParseError)) else p


def _solved_fields(net: Network, editset: EditSet, edited: Network, res: dict) -> dict:
    return {"edits": [e.to_dict() for e in editset.edits], "canonical_diff": canonical_diff(net, edited),
            "opf_status": res["status"], "cost": res.get("cost"), "pg": res.get("pg"),
            "max_loading_pct": res.get("max_loading_pct"), "binding": res.get("binding")}


# ---- Mode 1 -------------------------------------------------------------
def parse_direct(text: str, net: Network) -> dict | ParseError:
    """Parse a Mode 1 reply {"G1": MW, ..., "refuse": bool, "reason": str}."""
    try:
        obj = json.loads(strip_fences(text or ""))
    except (json.JSONDecodeError, ValueError):
        return ParseError("BAD_JSON", "The reply is not valid JSON.")
    if not isinstance(obj, dict):
        return ParseError("BAD_JSON", "The reply must be one JSON object.")
    refuse = obj.get("refuse", False)
    if not isinstance(refuse, bool) or not isinstance(obj.get("reason", ""), str):
        return ParseError("SCHEMA", "refuse must be true/false and reason a string.")
    allowed = set(net.gen_ids) | {"refuse", "reason"}
    if set(obj) - allowed:
        return ParseError("SCHEMA", f"Unknown keys: {sorted(set(obj) - allowed)}.")
    pg = {}
    for g in net.gen_ids:
        v = obj.get(g)
        if v is None and refuse:
            continue
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return ParseError("SCHEMA", f"{g} must be a number in MW.")
        pg[g] = float(v)
    return {"pg": pg, "refuse": refuse, "reason": obj.get("reason", "")}


def run_mode1(request: dict, net: Network, llm: LLM, logger: CallLogger | None = None,
              base_result: dict | None = None) -> dict:
    """Mode 1: the LLM outputs MW per generator. Judged later (evaluate.py) with
    a DC power flow on the network after the ground-truth edits."""
    base_result = base_result or solve_dcopf(net)
    s = _Session(1, request, llm, logger, system_prompt_direct(net, base_result))
    text, meta = s.ask()
    p = parse_direct(text, net) if not meta.get("error") else ParseError("LLM_ERROR", meta["error"])
    s.log(text, meta, _parsed_dict(p))
    if isinstance(p, ParseError):
        return s.record(outcome=p.kind, refuse=False, reason="", pg=None)
    if p["refuse"]:
        return s.record(outcome="refused", refuse=True, reason=p["reason"], pg=None)
    return s.record(outcome="dispatched", refuse=False, reason="", pg=p["pg"])


# ---- Modes 2-4 ----------------------------------------------------------
def run_mode2(request: dict, net: Network, llm: LLM, logger: CallLogger | None = None) -> dict:
    """Mode 2: LLM edits -> apply -> DC-OPF. No checker, no repair."""
    return _run_edits(2, request, net, llm, logger, use_checker=False, use_verifier=False)


def run_mode3(request: dict, net: Network, llm: LLM, logger: CallLogger | None = None) -> dict:
    """Mode 3: Mode 2 plus the checker; issues go back to the LLM (2 retries, +1 for R10)."""
    return _run_edits(3, request, net, llm, logger, use_checker=True, use_verifier=False)


def run_mode4(request: dict, net: Network, llm: LLM, logger: CallLogger | None = None,
              base_cost: float | None = None) -> dict:
    """Mode 4: Mode 3 plus verifier and template explanation."""
    return _run_edits(4, request, net, llm, logger, use_checker=True, use_verifier=True, base_cost=base_cost)


def _run_edits(mode, request, net, llm, logger, use_checker, use_verifier, base_cost=None) -> dict:
    s = _Session(mode, request, llm, logger, system_prompt_edits(net))
    check_retries = r10_retries = 0
    while True:
        text, meta = s.ask()
        if meta.get("error"):
            s.log(text, meta, None)
            return s.record(outcome="LLM_ERROR", refuse=False, reason="", error=meta["error"])
        parsed = parse_edits(text)

        if not use_checker:  # Mode 2
            s.log(text, meta, _parsed_dict(parsed))
            if isinstance(parsed, ParseError):
                return s.record(outcome=parsed.kind, refuse=False, reason="")
            if parsed.refuse:
                return s.record(outcome="refused", refuse=True, reason=parsed.reason, edits=[])
            try:
                edited = apply_edits(net, parsed)
            except KeyError as exc:
                return s.record(outcome="APPLY_ERROR", refuse=False, reason="",
                                edits=[e.to_dict() for e in parsed.edits], error=f"unknown id {exc}")
            res = solve_dcopf(edited)
            return s.record(outcome="solved" if res["status"] == "optimal" else "INFEASIBLE",
                            refuse=False, reason="", **_solved_fields(net, parsed, edited, res))

        issues = check(net, parsed)
        blocks = blocking(issues)
        s.log(text, meta, _parsed_dict(parsed), issues)
        if blocks:
            if check_retries < MAX_CHECK_RETRIES:
                check_retries += 1
                s.messages.append({"role": "user", "content": FEEDBACK.format(messages=" ".join(i.message for i in blocks))})
                continue
            return s.record(outcome="CHECKER_REJECTED", refuse=False, reason="",
                            checker_codes=[i.code for i in blocks])
        if parsed.refuse:
            rec = s.record(outcome="refused", refuse=True, reason=parsed.reason, edits=[])
            if use_verifier:
                rec["explanation"] = explain(net, None, None, None, refused=True, reason=parsed.reason)
            return rec

        edited = apply_edits(net, parsed)
        res = solve_dcopf(edited)
        r10 = infeasible_issue(res)
        if r10:
            s.attempts[-1]["issues"].append(r10.to_dict())
            if r10_retries < MAX_R10_RETRIES:
                r10_retries += 1
                s.messages.append({"role": "user", "content": FEEDBACK.format(messages=r10.message)})
                continue
            return s.record(outcome="INFEASIBLE", refuse=False, reason="", checker_codes=[r10.code],
                            **_solved_fields(net, parsed, edited, res))

        rec = s.record(outcome="solved", refuse=False, reason="", notes=[i.message for i in issues if i.type == "note"],
                       **_solved_fields(net, parsed, edited, res))
        if use_verifier:
            v = verify(net, edited, res, request["text"])
            rec["verify"] = v
            rec["extra_changes"] = v["extra_changes"]
            if base_cost is None:
                base_cost = solve_dcopf(net)["cost"]
            rec["explanation"] = explain(net, edited, res, base_cost)
            if not v["ok"]:
                rec["outcome"] = "VERIFY_FAILED"
        return rec


RUNNERS = {1: run_mode1, 2: run_mode2, 3: run_mode3, 4: run_mode4}
