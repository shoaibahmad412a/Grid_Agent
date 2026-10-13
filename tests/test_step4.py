"""Step 4: LLM backends and Mode 2."""
import json

import pytest
import requests

from src import llm as llm_mod
from src.agent import run_mode2, system_prompt_edits
from src.evaluate import mock_answers, score_record
from src.llm import APILLM, CallLogger, MockLLM, OllamaLLM, ScriptedLLM

REQ = {"id": "T1", "group": "load", "text": "demand at bus 7 is up 15%"}
GOOD = '{"edits": [{"action": "scale_load", "bus": 7, "factor": 1.15}], "refuse": false, "reason": ""}'


def test_mock_mode2_matches_every_benchmark_item(net, benchmark):
    reqs, truth = benchmark
    by_id = {t["id"]: t for t in truth}
    llm = MockLLM(mock_answers(net, truth, 2))
    for r in reqs:
        rec = run_mode2(r, net, llm)
        s = score_record(rec, by_id[r["id"]], net)
        assert s["success"], (r["id"], rec["outcome"])


def test_mode2_record_fields(net, tmp_path):
    rec = run_mode2(REQ, net, ScriptedLLM([GOOD]), CallLogger("t", tmp_path))
    assert rec["outcome"] == "solved" and rec["canonical_diff"] == {"bus7.Pd": 26.22}
    for k in ("id", "mode", "raw_reply", "edits", "opf_status", "cost", "pg", "max_loading_pct",
              "latency_s", "tokens_in", "tokens_out"):
        assert k in rec
    line = json.loads((tmp_path / "t.jsonl").read_text().splitlines()[0])
    for k in ("mode", "request_id", "prompt", "raw_reply", "parsed", "latency_s", "tokens_in", "tokens_out", "error", "model"):
        assert k in line


@pytest.mark.parametrize("reply", ["Sure! Here are the edits.", "", '{"edits": [', "[]"])
def test_bad_json_recorded_not_crash(net, reply):
    assert run_mode2(REQ, net, ScriptedLLM([reply]))["outcome"] == "BAD_JSON"


def test_mode2_unknown_id_is_recorded(net):
    bad = '{"edits": [{"action": "set_line_status", "line": "L42", "status": "out"}], "refuse": false, "reason": ""}'
    assert run_mode2(REQ, net, ScriptedLLM([bad]))["outcome"] == "APPLY_ERROR"


def test_system_prompt_contains_card_and_schema(net):
    p = system_prompt_edits(net)
    assert "set_line_status" in p and "L41: 6-28" in p and "refuse" in p


def test_logger_scrubs_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-very-secret-123456")
    lg = CallLogger("s", tmp_path)
    lg.log({"prompt": "key sk-very-secret-123456 leaked"})
    assert "sk-very-secret" not in (tmp_path / "s.jsonl").read_text()


class FakeResp:
    def __init__(self, code, body):
        self.status_code, self._body, self.text = code, body, json.dumps(body)

    def json(self):
        return self._body


def test_ollama_backend(monkeypatch):
    seen = {}

    def fake_post(url, headers, json, timeout):
        seen.update(url=url, payload=json)
        return FakeResp(200, {"model": "llama3.1:8b", "message": {"content": GOOD},
                              "prompt_eval_count": 100, "eval_count": 20})
    monkeypatch.setattr(requests, "post", fake_post)
    text, meta = OllamaLLM(model="llama3.1:8b", url="http://x:1", measure_energy=False).complete([{"role": "user", "content": "hi"}])
    assert text == GOOD and meta["tokens_in"] == 100 and meta["model"] == "llama3.1:8b"
    assert seen["payload"]["options"] == {"temperature": 0, "seed": 42}


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_api_backend_and_retry_only_on_rate_limit(monkeypatch, provider):
    monkeypatch.setenv("LLM_API_KEY", "sk-test-key-000000")
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    calls = []
    ok = ({"model": "m", "content": [{"type": "text", "text": GOOD}], "usage": {"input_tokens": 5, "output_tokens": 6}}
          if provider == "anthropic" else
          {"model": "m", "choices": [{"message": {"content": GOOD}}], "usage": {"prompt_tokens": 5, "completion_tokens": 6}})

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        assert json["temperature"] == 0  # model "m" accepts sampling parameters
        return FakeResp(429, {}) if len(calls) == 1 else FakeResp(200, ok)
    monkeypatch.setattr(requests, "post", fake_post)
    api = APILLM(model="m", provider=provider)
    text, meta = api.complete([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])
    assert text == GOOD and len(calls) == 2 and meta["tokens_out"] == 6
    assert meta["energy_kwh"] is None and meta["energy_note"] == "remote; not measurable"
    assert "sk-test" not in repr(api)


def test_api_never_retries_on_content(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-test-key-000000")
    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(1) or FakeResp(
        200, {"choices": [{"message": {"content": "garbage"}}], "usage": {}}))
    text, _ = APILLM(model="m", provider="openai").complete([{"role": "user", "content": "u"}])
    assert text == "garbage" and len(calls) == 1


def test_anthropic_haiku_5_5_request_shape(monkeypatch):
    """Claude Haiku 5.5 rejects temperature, so it must not be sent; refusals are errors."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test-key-000000")
    monkeypatch.setenv("LLM_API_EFFORT", "low")
    sent = []
    body = {"model": "claude-haiku-5-5", "stop_reason": "end_turn", "usage": {"input_tokens": 9, "output_tokens": 3},
            "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": GOOD}]}
    monkeypatch.setattr(requests, "post", lambda url, headers, json, timeout: sent.append((url, headers, json)) or FakeResp(200, body))
    text, meta = APILLM(model="claude-haiku-5-5", provider="anthropic").complete(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])
    url, headers, payload = sent[0]
    assert url == "https://api.anthropic.com/v1/messages" and headers["x-api-key"] == "sk-test-key-000000"
    assert "temperature" not in payload and payload["output_config"] == {"effort": "low"}
    assert payload["system"] == "s" and payload["messages"] == [{"role": "user", "content": "u"}]
    assert text == GOOD and meta["model"] == "claude-haiku-5-5" and meta["error"] is None
    body["stop_reason"] = "refusal"
    assert APILLM(model="claude-haiku-5-5", provider="anthropic").complete([{"role": "user", "content": "u"}])[1]["error"] == "stop_reason refusal"


def test_gemini_request_shape(monkeypatch):
    """Native Gemini call: system instruction, model role for assistant turns,
    temperature 0 + seed, thinking level, thought parts skipped, thinking tokens counted."""
    monkeypatch.setenv("LLM_API_KEY", "AIza-test-key-000000")
    monkeypatch.setenv("LLM_API_EFFORT", "low")
    sent = []
    body = {"modelVersion": "gemini-3.1-flash-lite",
            "candidates": [{"finishReason": "STOP", "content": {"role": "model", "parts": [
                {"text": "hidden reasoning", "thought": True}, {"text": GOOD}]}}],
            "usageMetadata": {"promptTokenCount": 1200, "candidatesTokenCount": 40, "thoughtsTokenCount": 60}}
    monkeypatch.setattr(requests, "post",
                        lambda url, headers, json, timeout: sent.append((url, headers, json)) or FakeResp(200, body))
    api = APILLM(model="gemini-3.1-flash-lite", provider="gemini")
    text, meta = api.complete([{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
                               {"role": "assistant", "content": "a"}, {"role": "user", "content": "fix it"}])
    url, headers, payload = sent[0]
    assert url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent"
    assert headers == {"x-goog-api-key": "AIza-test-key-000000"} and "AIza" not in url
    assert payload["systemInstruction"] == {"parts": [{"text": "s"}]}
    assert [c["role"] for c in payload["contents"]] == ["user", "model", "user"]
    assert payload["generationConfig"]["temperature"] == 0 and payload["generationConfig"]["seed"] == 42
    assert payload["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    assert text == GOOD and meta["tokens_in"] == 1200 and meta["tokens_out"] == 100 and meta["error"] is None
    body["candidates"][0]["finishReason"] = "MAX_TOKENS"
    assert api.complete([{"role": "user", "content": "u"}])[1]["error"] == "finishReason MAX_TOKENS"
    body.pop("candidates")
    body["promptFeedback"] = {"blockReason": "SAFETY"}
    assert api.complete([{"role": "user", "content": "u"}])[1]["error"] == "blocked: SAFETY"


def test_check_api_script(monkeypatch, capsys):
    import runpy
    monkeypatch.setenv("LLM_API_KEY", "AIza-test-key-000000")
    monkeypatch.setenv("LLM_API_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_API_MODEL", "gemini-3.1-flash-lite")
    reply = '{"edits": [], "refuse": true, "reason": "test"}'
    body = {"modelVersion": "gemini-3.1-flash-lite", "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 9},
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": reply}]}}]}
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResp(200, body))
    runpy.run_path("scripts/check_api.py", run_name="__main__")
    out = capsys.readouterr().out
    assert "parses as edit JSON: yes" in out and "AIza" not in out
