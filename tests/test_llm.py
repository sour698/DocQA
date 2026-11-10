import json

import pytest
import requests

from app import llm


class FakeResp:
    def __init__(self, code, text="", payload=None, lines=()):
        self.status_code, self.text, self._p, self._lines = code, text, payload, lines
        self.closed = False

    def json(self):
        return self._p

    def iter_lines(self, decode_unicode=True):
        return iter(self._lines)

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(llm, "LLM_API_KEY", "k")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)


def test_404_gives_model_hint(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResp(404, "not found"))
    with pytest.raises(llm.LLMError) as e:
        llm.chat("s", "u")
    assert "LLM_MODEL" in str(e.value) and "404" in str(e.value)


def test_ok_response(monkeypatch):
    payload = {"choices": [{"message": {"content": " hi "}}]}
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResp(200, payload=payload))
    assert llm.chat("s", "u") == "hi"


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(llm, "LLM_API_KEY", "")
    with pytest.raises(llm.LLMError, match="LLM_API_KEY"):
        llm.chat("s", "u")


def test_retries_429_then_succeeds(monkeypatch):
    calls = []

    def post(*a, **k):
        calls.append(1)
        if len(calls) < 3:
            return FakeResp(429, "Please try again in 1.5s")
        return FakeResp(200, payload={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(requests, "post", post)
    assert llm.chat("s", "u") == "ok" and len(calls) == 3


def test_deadline_stops_retrying(monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "LLM_DEADLINE", 5)
    monkeypatch.setattr(
        requests, "post", lambda *a, **k: calls.append(1) or FakeResp(429, "try again in 60s")
    )
    with pytest.raises(llm.LLMError, match="429"):
        llm.chat("s", "u")
    assert len(calls) == 1  # the 60 s wait would exceed the 5 s deadline -> give up at once


def test_stream_yields_text_pieces(monkeypatch):
    def sse(piece):
        return "data: " + json.dumps({"choices": [{"delta": {"content": piece}}]})

    lines = [
        sse("Hel"),
        "",
        sse("lo"),
        "data: " + json.dumps({"choices": [{"delta": {}}]}),
        "data: [DONE]",
    ]
    resp = FakeResp(200, lines=lines)
    monkeypatch.setattr(requests, "post", lambda *a, **k: resp)
    assert list(llm.chat_stream("s", "u")) == ["Hel", "lo"]
    assert resp.closed


def test_retry_after_parser():
    assert llm._retry_after_seconds("Please try again in 1m5.2s") == pytest.approx(65.2)
    assert llm._retry_after_seconds("try again in 250ms") == pytest.approx(0.25)
    assert llm._retry_after_seconds("nothing here") is None
