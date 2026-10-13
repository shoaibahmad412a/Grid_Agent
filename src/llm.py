"""LLM backends with one interface: complete(messages, request_id) -> (text, meta).

meta = {model, latency_s, tokens_in, tokens_out, error, energy_kwh, energy_note}.
Backends: MockLLM (ground-truth replies, pipeline tests only), ScriptedLLM
(fixed reply list, tests), OllamaLLM (local), APILLM (OpenAI-compatible or
Anthropic). Keys come from .env and never appear in meta, logs or output.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

from src import energy

SEED = 42
TIMEOUT_S = 300
MAX_RETRIES = 3
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}
# Claude models that reject sampling parameters (a non-default temperature is a 400).
# On these, temperature cannot be set to 0; repeats measure the run-to-run spread.
NO_SAMPLING_PREFIXES = ("claude-haiku-5", "claude-sonnet-5", "claude-opus-5", "claude-opus-4-7",
                        "claude-opus-4-8", "claude-fable", "claude-mythos")
ANTHROPIC_MAX_TOKENS = 16000  # thinking tokens count toward this limit


def accepts_temperature(model: str) -> bool:
    """False for Claude models where a temperature value returns HTTP 400."""
    return not model.startswith(NO_SAMPLING_PREFIXES)


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
    """Remote chat API. LLM_API_PROVIDER = 'openai' (any OpenAI-compatible
    /chat/completions endpoint) or 'anthropic'. Temperature 0; seed where supported."""
    name = "api"

    def __init__(self, model: str | None = None, provider: str | None = None, base: str | None = None):
        load_dotenv()
        self._key = os.getenv("LLM_API_KEY") or ""
        self.model = model or os.getenv("LLM_API_MODEL") or ""
        self.provider = (provider or os.getenv("LLM_API_PROVIDER") or "openai").lower()
        default = "https://api.anthropic.com" if self.provider == "anthropic" else "https://api.openai.com/v1"
        self.base = (base or os.getenv("LLM_API_BASE") or default).rstrip("/")
        self.effort = os.getenv("LLM_API_EFFORT") or None  # Anthropic only: low|medium|high|xhigh|max
        if not self._key or not self.model:
            raise ValueError("LLM_API_KEY and LLM_API_MODEL must be set in .env")

    def __repr__(self):  # never show the key
        return f"APILLM(provider={self.provider!r}, model={self.model!r})"

    def complete(self, messages, request_id=None):
        t0 = time.perf_counter()
        try:
            if self.provider == "anthropic":
                system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
                chat = [m for m in messages if m["role"] != "system"]
                payload = {"model": self.model, "max_tokens": ANTHROPIC_MAX_TOKENS,
                           "system": system, "messages": chat}
                if accepts_temperature(self.model):
                    payload["temperature"] = 0
                if self.effort:
                    payload["output_config"] = {"effort": self.effort}
                headers = {"x-api-key": self._key, "anthropic-version": "2023-06-01"}
                r, err = _post_with_retry(f"{self.base}/v1/messages", headers, payload)
            else:
                payload = {"model": self.model, "messages": messages, "temperature": 0, "seed": SEED}
                headers = {"Authorization": f"Bearer {self._key}"}
                r, err = _post_with_retry(f"{self.base}/chat/completions", headers, payload)
            dt = time.perf_counter() - t0
            if err or r is None:
                return "", _meta(self.model, dt, error=err, note=energy.REMOTE_NOTE)
            if r.status_code != 200:
                return "", _meta(self.model, dt, error=f"HTTP {r.status_code}: {self._scrub(r.text[:300])}",
                                 note=energy.REMOTE_NOTE)
            d = r.json()
            if self.provider == "anthropic":
                text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
                u = d.get("usage", {})
                tin, tout = u.get("input_tokens"), u.get("output_tokens")
                if d.get("stop_reason") in ("refusal", "max_tokens"):
                    return text, _meta(d.get("model", self.model), dt, tin, tout,
                                       error=f"stop_reason {d['stop_reason']}", note=energy.REMOTE_NOTE)
            else:
                text = d["choices"][0]["message"].get("content") or ""
                u = d.get("usage", {})
                tin, tout = u.get("prompt_tokens"), u.get("completion_tokens")
            return text, _meta(d.get("model", self.model), dt, tin, tout, note=energy.REMOTE_NOTE)
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
        with self.path.open("a") as f:
            f.write(line + "\n")
