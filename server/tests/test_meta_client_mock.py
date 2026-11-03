"""Phase 1: MetaClient with mocked transport (no AI calls)."""
import json

import httpx
import pytest
from pydantic import BaseModel

from app.errors import BudgetExceeded, LLMStepError
from app.meta_client import MetaClient


class ToySchema(BaseModel):
    answer: str


def _client(handler, records, **kw):
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://x.test/v1")
    return MetaClient(
        api_key="SECRETKEY123", base_url="https://x.test/v1", model_id="m",
        timeout_s=5, max_retry=2, recorder=records.append, http_client=http, **kw,
    )


def _ok_payload(content, in_tok=10, out_tok=20):
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": in_tok, "completion_tokens": out_tok},
    }


def test_chat_json_success_and_record():
    records = []

    def h(request):
        assert request.headers["authorization"] == "Bearer SECRETKEY123"
        body = json.loads(request.content.decode())
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(200, json=_ok_payload('{"answer": "yes"}'))

    c = _client(h, records)
    out = c.chat_json(bundle="planning", episode=None, system="s", user="u",
                      schema=ToySchema, max_tokens=100)
    assert out == {"answer": "yes"}
    assert len(records) == 1
    r = records[0]
    assert (r["bundle"], r["ok"], r["input_tokens"], r["output_tokens"]) == \
        ("planning", 1, 10, 20)
    assert "SECRETKEY123" not in json.dumps(r)


def test_retry_on_500_then_success():
    records = {"n": 0}

    def h(request):
        records["n"] += 1
        if records["n"] < 3:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json=_ok_payload('{"answer": "ok"}'))

    recs = []
    c = _client(h, recs)
    c._backoff = lambda *a, **k: None  # no sleeping in tests
    out = c.chat_json(bundle="verify", episode=7, system="s", user="u",
                      schema=ToySchema, max_tokens=100)
    assert out == {"answer": "ok"}
    assert records["n"] == 3
    assert recs[0]["ok"] == 1


def test_retry_exhausted_raises_and_records():
    recs = []

    def h(request):
        return httpx.Response(503, text="down")

    c = _client(h, recs)
    c._backoff = lambda *a, **k: None
    with pytest.raises(LLMStepError, match="retry_exhausted"):
        c.chat_json(bundle="drafting", episode=1, system="s", user="u",
                    schema=ToySchema, max_tokens=100)
    assert recs[0]["ok"] == 0
    assert "retry_exhausted" in recs[0]["error"]


def test_schema_failure_triggers_one_recovery():
    calls = []
    recs = []

    def h(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(200, json=_ok_payload('{"wrong": 1}'))
        return httpx.Response(200, json=_ok_payload('{"answer": "fixed"}'))

    c = _client(h, recs)
    out = c.chat_json(bundle="entity", episode=2, system="s", user="u",
                      schema=ToySchema, max_tokens=100)
    assert out == {"answer": "fixed"}
    assert len(calls) == 2
    assert recs[0]["ok"] == 1


def test_double_schema_failure_raises():
    recs = []

    def h(request):
        return httpx.Response(200, json=_ok_payload("not json at all"))

    c = _client(h, recs)
    with pytest.raises(LLMStepError, match="json_schema_failed"):
        c.chat_json(bundle="quality", episode=3, system="s", user="u",
                    schema=ToySchema, max_tokens=100)
    assert recs[0]["ok"] == 0


def test_length_cutoff_with_empty_output_raises_clear_error():
    """Reasoning can burn max_tokens before content: name the cutoff."""
    recs = []

    def h(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": ""},
                         "finish_reason": "length"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 100,
                      "completion_tokens_details": {"reasoning_tokens": 100}}})

    c = _client(h, recs)
    with pytest.raises(LLMStepError, match="max_tokens_exhausted"):
        c.chat_json(bundle="publish-blurb", episode=None, system="s", user="u",
                    schema=ToySchema, max_tokens=100)
    assert recs[0]["ok"] == 0
    assert recs[0]["error"] == "max_tokens_exhausted"


def test_budget_exceeded_skips_http():
    def h(request):  # pragma: no cover
        raise AssertionError("must not call HTTP")

    def checker():
        raise BudgetExceeded("over")

    recs = []
    c = _client(h, recs, budget_checker=checker)
    with pytest.raises(BudgetExceeded):
        c.chat_json(bundle="planning", episode=None, system="s", user="u",
                    schema=ToySchema, max_tokens=100)
    assert recs[0]["error"] == "budget_exceeded"


def test_records_finish_reason_and_reasoning_tokens():
    recs = []
    seen = []

    def h(request):
        body = json.loads(request.content.decode())
        seen.append(body["messages"][-1]["content"])
        if len(seen) == 1:
            return httpx.Response(200, json={
                "choices": [{"message": {"content": '{"answer": '},
                             "finish_reason": "length"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 100,
                          "completion_tokens_details": {"reasoning_tokens": 90}}})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"answer": "ok"}'},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 40,
                      "completion_tokens_details": {"reasoning_tokens": 30}}})

    c = _client(h, recs)
    assert c.chat_json(bundle="idea_chat", episode=None, system="s", user="u",
                       schema=ToySchema, max_tokens=100) == {"answer": "ok"}
    assert recs[0]["finish_reason"] == "length>stop"
    assert (recs[0]["output_tokens"], recs[0]["reasoning_tokens"]) == (140, 120)
    # the recovery prompt shows the exact JSON shape, not just key names
    assert '{"answer": ""}' in seen[1]
