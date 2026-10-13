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


def test_api_backend_and_retry_only_on_rate_limit(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-test-key-000000")
    monkeypatch.delenv("LLM_API_TEMPERATURE", raising=False)
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    calls = []
    ok = {"model": "gpt-5-nano-2025-08-07", "choices": [{"message": {"content": GOOD}, "finish_reason": "stop"}],
          "usage": {"prompt_tokens": 5, "completion_tokens": 6}}

    def fake_post(url, headers, json, timeout):
        calls.append((url, json))
        return FakeResp(429, {}) if len(calls) == 1 else FakeResp(200, ok)
    monkeypatch.setattr(requests, "post", fake_post)
    api = APILLM()  # default model gpt-5-nano
    text, meta = api.complete([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])
    assert api.model == "gpt-5-nano" and calls[0][0] == "https://api.openai.com/v1/chat/completions"
    assert text == GOOD and len(calls) == 2 and meta["tokens_out"] == 6 and meta["model"] == "gpt-5-nano-2025-08-07"
    assert meta["energy_kwh"] is None and meta["energy_note"] == "remote; not measurable"
    assert "sk-test" not in repr(api)


def test_api_never_retries_on_content(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-test-key-000000")
    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(1) or FakeResp(
        200, {"choices": [{"message": {"content": "garbage"}, "finish_reason": "stop"}], "usage": {}}))
    text, _ = APILLM().complete([{"role": "user", "content": "u"}])
    assert text == "garbage" and len(calls) == 1


def test_api_truncated_reply_is_an_error(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-test-key-000000")
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResp(
        200, {"choices": [{"message": {"content": ""}, "finish_reason": "length"}], "usage": {}}))
    assert APILLM().complete([{"role": "user", "content": "u"}])[1]["error"] == "finish_reason length"


def test_check_api_script(monkeypatch, capsys):
    import runpy
    monkeypatch.setenv("LLM_API_KEY", "sk-test-key-000000")
    reply = '{"edits": [], "refuse": true, "reason": "test"}'
    body = {"model": "gpt-5-nano", "usage": {"prompt_tokens": 20, "completion_tokens": 9},
            "choices": [{"message": {"content": reply}, "finish_reason": "stop"}]}
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResp(200, body))
    runpy.run_path("scripts/check_api.py", run_name="__main__")
    out = capsys.readouterr().out
    assert "parses as edit JSON: yes" in out and "sk-test" not in out


def test_openai_reasoning_model_gets_no_temperature(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-test-key-000000")
    monkeypatch.setenv("LLM_API_EFFORT", "low")
    monkeypatch.delenv("LLM_API_TEMPERATURE", raising=False)
    sent = []
    ok = {"model": "gpt-5-nano", "choices": [{"message": {"content": GOOD}, "finish_reason": "stop"}],
          "usage": {"prompt_tokens": 5, "completion_tokens": 6}}
    monkeypatch.setattr(requests, "post", lambda url, headers, json, timeout: sent.append(json) or FakeResp(200, ok))
    APILLM(model="gpt-5-nano").complete([{"role": "user", "content": "u"}])
    assert "temperature" not in sent[0] and "seed" not in sent[0] and sent[0]["reasoning_effort"] == "low"
    APILLM(model="gpt-4.1-nano").complete([{"role": "user", "content": "u"}])
    assert sent[1]["temperature"] == 0 and sent[1]["seed"] == 42
    monkeypatch.setenv("LLM_API_TEMPERATURE", "0")
    APILLM(model="gpt-5-nano").complete([{"role": "user", "content": "u"}])
    assert sent[2]["temperature"] == 0 and sent[2]["seed"] == 42


def test_list_models(monkeypatch):
    from src.llm import list_models
    monkeypatch.setenv("LLM_API_KEY", "sk-test-key-000000")
    seen = {}

    class R:
        status_code = 200
        text = ""

        def json(self):
            return {"data": [{"id": "gpt-b"}, {"id": "gpt-a"}]}
    monkeypatch.setattr(requests, "get", lambda url, headers, timeout: seen.update(url=url, h=headers) or R())
    assert list_models() == ["gpt-a", "gpt-b"]
    assert seen["url"] == "https://api.openai.com/v1/models" and seen["h"]["Authorization"].startswith("Bearer ")
