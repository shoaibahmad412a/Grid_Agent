"""LLM backends with one interface: complete(messages, request_id) -> (text, meta).

meta = {model, latency_s, tokens_in, tokens_out, error, energy_kwh, energy_note}.
Backends: MockLLM (ground-truth replies, pipeline tests only), ScriptedLLM
(fixed reply list, tests), OllamaLLM (local), APILLM (OpenAI, default model
gpt-5-nano). Keys come from .env and never appear in meta, logs or output.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv as _dotenv_load

from src import energy

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"


def load_dotenv() -> None:
    """Load ROOT/.env, whatever the current folder is.

    Also reads files that Windows tools saved as UTF-16 or with a UTF-8 BOM.
    Values already set in the environment are not overwritten.
    """
    if ENV_FILE.is_file():
        raw = ENV_FILE.read_bytes()
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            enc = "utf-16"
        elif raw.startswith(b"\xef\xbb\xbf"):
            enc = "utf-8-sig"
        else:
            enc = "utf-8"
        _dotenv_load(ENV_FILE, encoding=enc)
    else:
        _dotenv_load()  # fall back to python-dotenv's own search


def missing_key_message() -> str:
    """Explain why LLM_API_KEY was not found (never prints any key)."""
    msg = f"LLM_API_KEY is not set. Expected it in {ENV_FILE}"
    if not ENV_FILE.is_file():
        near = sorted(p.name for p in ROOT.glob(".env*") if p.name != ".env.example")
        msg += " - that file does not exist."
        if near:
            msg += f" Found {near} instead: rename it to exactly '.env' (Windows may hide a '.txt' ending)."
    else:
        msg += " - the file exists but has no line 'LLM_API_KEY=...' (check the spelling, and that it is not commented out)."
    return msg


SEED = 42
TIMEOUT_S = 300
MAX_RETRIES = 3
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}
DEFAULT_API_MODEL = "gpt-5-nano"
# OpenAI reasoning models accept only the default temperature (a value of 0 is a 400).
OPENAI_REASONING_PREFIXES = ("o1", "o3", "o4", "gpt-5", "gpt-6")


def openai_sampling(model: str) -> bool:
    """Send temperature 0 + seed to this OpenAI model?

    LLM_API_TEMPERATURE overrides: '0' always sends them, 'off' never does.
    Otherwise they are skipped for OpenAI reasoning models (o-series, gpt-5+).
    """
    force = (os.getenv("LLM_API_TEMPERATURE") or "").strip().lower()
    if force in ("0", "on"):
        return True
    if force == "off":
        return False
    return not model.startswith(OPENAI_REASONING_PREFIXES)


def _meta(model, latency=0.0, tin=None, tout=None, error=None, kwh=None, note=None) -> dict:
    return {"model": model, "latency_s": latency, "tokens_in": tin, "tokens_out": tout,
            "error": error, "energy_kwh": kwh, "energy_note": note}


class LLM:
    name = "base"
    model = "none"

    def complete(self, messages: list[dict], request_id: str | None = None) -> tuple[str, dict]:
        raise NotImplementedError


class MockLLM(LLM):
    """Returns a fixed answer per request id (the ground truth). Pipeline tests only."""
    name = "mock"
    model = "mock-ground-truth"

    def __init__(self, answers: dict[str, str]):
        self.answers = answers

    def complete(self, messages, request_id=None):
        return self.answers[request_id], _meta(self.model, 0.0, 0, 0, note="mock")


class ScriptedLLM(LLM):
    """Returns the given replies in order (for tests of the retry loop)."""
    name = "scripted"
    model = "scripted"

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def complete(self, messages, request_id=None):
        self.calls.append([dict(m) for m in messages])
        return self.replies.pop(0), _meta(self.model, 0.0, 0, 0, note="scripted")


def _post_with_retry(url, headers, payload):
    """POST JSON; retry only on network errors and rate-limit/server status codes."""
    last = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT_S)
            if r.status_code in RETRY_STATUS:
                last = f"HTTP {r.status_code}"
            else:
                return r, None
        except (requests.ConnectionError, requests.Timeout) as exc:
            last = f"{type(exc).__name__}"
        if attempt < MAX_RETRIES:
            time.sleep(2 ** (attempt + 1))
    return None, f"gave up after {MAX_RETRIES} retries: {last}"


class OllamaLLM(LLM):
    """Local model via Ollama /api/chat. Temperature 0, fixed seed. Energy via CodeCarbon."""
    name = "ollama"

    def __init__(self, model: str | None = None, url: str | None = None, measure_energy: bool = True):
        load_dotenv()
        self.model = model or os.getenv("OLLAMA_MODEL") or ""
        self.url = (url or os.getenv("OLLAMA_URL") or "http://localhost:11434").rstrip("/")
        self.measure_energy = measure_energy
        if not self.model:
            raise ValueError("OLLAMA_MODEL is not set in .env")

    def complete(self, messages, request_id=None):
        payload = {"model": self.model, "messages": messages, "stream": False,
                   "options": {"temperature": 0, "seed": SEED}}
        t0 = time.perf_counter()
        call = lambda: _post_with_retry(f"{self.url}/api/chat", {}, payload)
        if self.measure_energy:
            (r, err), kwh, note = energy.measure(call)
        else:
            (r, err), kwh, note = call(), None, "not measured"
        dt = time.perf_counter() - t0
        if err or r is None:
            return "", _meta(self.model, dt, error=err, kwh=kwh, note=note)
        if r.status_code != 200:
            return "", _meta(self.model, dt, error=f"HTTP {r.status_code}: {r.text[:200]}", kwh=kwh, note=note)
        d = r.json()
        return d.get("message", {}).get("content", ""), _meta(
            d.get("model", self.model), dt, d.get("prompt_eval_count"), d.get("eval_count"), kwh=kwh, note=note)


class APILLM(LLM):
    """OpenAI Chat Completions API (default model gpt-5-nano).

    gpt-5-nano is a reasoning model: it accepts only its default temperature,
    so temperature 0 and the seed are not sent (see openai_sampling), and the
    reasoning effort comes from LLM_API_EFFORT (minimal|low|medium|high).
    Reasoning tokens are billed as output and are included in tokens_out.
    """
    name = "api"
    DEFAULT_BASE = "https://api.openai.com/v1"

    def __init__(self, model: str | None = None, base: str | None = None):
        load_dotenv()
        self._key = os.getenv("LLM_API_KEY") or ""
        self.model = model or os.getenv("LLM_API_MODEL") or DEFAULT_API_MODEL
        self.base = (base or os.getenv("LLM_API_BASE") or self.DEFAULT_BASE).rstrip("/")
        self.effort = os.getenv("LLM_API_EFFORT") or None
        if not self._key:
            raise ValueError(missing_key_message())

    def __repr__(self):  # never show the key
        return f"APILLM(model={self.model!r})"

    def complete(self, messages, request_id=None):
        payload = {"model": self.model, "messages": messages}
        if openai_sampling(self.model):
            payload.update(temperature=0, seed=SEED)
        if self.effort:
            payload["reasoning_effort"] = self.effort
        headers = {"Authorization": f"Bearer {self._key}"}
        t0 = time.perf_counter()
        try:
            r, err = _post_with_retry(f"{self.base}/chat/completions", headers, payload)
            dt = time.perf_counter() - t0
            if err or r is None:
                return "", _meta(self.model, dt, error=err, note=energy.REMOTE_NOTE)
            if r.status_code != 200:
                return "", _meta(self.model, dt, error=f"HTTP {r.status_code}: {self._scrub(r.text[:300])}",
                                 note=energy.REMOTE_NOTE)
            d = r.json()
            choice = d["choices"][0]
            u = d.get("usage", {})
            finish = choice.get("finish_reason")
            stop_err = None if finish in (None, "stop") else f"finish_reason {finish}"
            return choice["message"].get("content") or "", _meta(
                d.get("model", self.model), dt, u.get("prompt_tokens"), u.get("completion_tokens"),
                error=stop_err, note=energy.REMOTE_NOTE)
        except Exception as exc:
            return "", _meta(self.model, time.perf_counter() - t0,
                             error=self._scrub(f"{type(exc).__name__}: {exc}"), note=energy.REMOTE_NOTE)

    def _scrub(self, text: str) -> str:
        return text.replace(self._key, "[REDACTED]") if self._key else text


def make_llm(backend: str, answers: dict[str, str] | None = None) -> LLM:
    """Build a backend by name: 'mock' (needs answers), 'ollama', 'api'."""
    if backend == "mock":
        return MockLLM(answers or {})
    if backend == "ollama":
        return OllamaLLM()
    if backend == "api":
        return APILLM()
    raise ValueError(f"unknown backend {backend!r}")


class CallLogger:
    """Writes one JSON line per LLM call to results/raw/<run_id>.jsonl.

    Any secret value from the environment is scrubbed before writing.
    """

    def __init__(self, run_id: str, root: str | Path = "results/raw"):
        load_dotenv()
        self.path = Path(root) / f"{run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._secrets = [v for k, v in os.environ.items() if k.endswith("_KEY") and v and len(v) >= 8]

    def log(self, entry: dict) -> None:
        line = json.dumps(entry, default=str)
        for s in self._secrets:
            line = line.replace(s, "[REDACTED]")
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def list_models() -> list[str]:
    """Model ids the OpenAI key in .env can use."""
    load_dotenv()
    key = os.getenv("LLM_API_KEY") or ""
    if not key:
        raise ValueError(missing_key_message())
    base = (os.getenv("LLM_API_BASE") or APILLM.DEFAULT_BASE).rstrip("/")
    r = requests.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300].replace(key, '[REDACTED]')}")
    return sorted(m.get("id", "") for m in r.json().get("data", []))
