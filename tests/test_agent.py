import pytest

from app import agent, llm, rag


@pytest.fixture(autouse=True)
def _fresh_graph():
    agent._graph = None


def _patch(monkeypatch, route_word, rewritten="standalone q"):
    calls = []

    def fake_chat(system, user, temperature=0.0):
        calls.append(system)
        if "rewrite" in system.lower():
            return rewritten
        if "routing" in system.lower():
            return route_word
        return "chat or clarify reply"

    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(
        rag,
        "answer",
        lambda q, mode, user_id="default": {
            "answer": f"doc answer to: {q}",
            "citations": [{"ref": 1}],
            "refused": False,
            "retrieved": [1],
        },
    )
    return calls


def test_search_route_uses_rag_and_rewrites_followups(monkeypatch):
    _patch(monkeypatch, "SEARCH")
    out = agent.run("and for contractors?", [{"role": "user", "content": "x"}], ["a.pdf"])
    assert out["route"] == "SEARCH"
    assert out["answer"] == "doc answer to: standalone q"
    assert out["standalone_question"] == "standalone q"
    assert out["from_documents"] is True and "retrieved" not in out


def test_chat_route_skips_rag(monkeypatch):
    _patch(monkeypatch, "CHAT")
    out = agent.run("thanks!", [], [])
    assert out["route"] == "CHAT" and out["citations"] == [] and "doc answer" not in out["answer"]


def test_clarify_route(monkeypatch):
    _patch(monkeypatch, "CLARIFY")
    assert agent.run("what about it?", [], [])["route"] == "CLARIFY"


def test_garbage_route_falls_back_to_search(monkeypatch):
    _patch(monkeypatch, "banana split")
    assert agent.run("q", [], ["a.pdf"])["route"] == "SEARCH"


def test_no_documents_returns_nodocs(monkeypatch):
    _patch(monkeypatch, "banana split")
    out = agent.run("q", [], [])
    assert out["route"] == "NODOCS" and "no documents" in out["answer"].lower()


def test_general_route_needs_permission(monkeypatch):
    _patch(monkeypatch, "GENERAL")
    assert agent.run("capital of France?", [], ["a.pdf"], allow_general=False)["route"] == "SEARCH"
    out = agent.run("capital of France?", [], ["a.pdf"], allow_general=True)
    assert out["route"] == "GENERAL" and out["from_documents"] is False


def test_no_documents_but_general_allowed(monkeypatch):
    _patch(monkeypatch, "SEARCH")
    assert agent.run("q", [], [], allow_general=True)["route"] == "GENERAL"


def test_refused_search_falls_back_to_general_only_when_allowed(monkeypatch):
    _patch(monkeypatch, "SEARCH")
    monkeypatch.setattr(
        rag,
        "answer",
        lambda q, mode, user_id="default": {
            "answer": rag.REFUSAL,
            "citations": [],
            "refused": True,
            "retrieved": [],
        },
    )
    strict = agent.run("q", [], ["a.pdf"], allow_general=False)
    assert strict["refused"] is True and strict["from_documents"] is False
    loose = agent.run("q", [], ["a.pdf"], allow_general=True)
    assert loose["refused"] is False and loose["from_documents"] is False


def test_llm_failure_in_rewrite_or_route_does_not_kill_the_request(monkeypatch):
    _patch(monkeypatch, "SEARCH")

    def flaky(system, user, temperature=0.0):
        if "rewrite" in system.lower() or "routing" in system.lower():
            raise llm.LLMError("rate limited")
        return "x"

    monkeypatch.setattr(llm, "chat", flaky)
    out = agent.run("and contractors?", [{"role": "user", "content": "leave?"}], ["a.pdf"])
    assert out["route"] == "SEARCH" and out["standalone_question"] == "and contractors?"


def test_summarize_route_picks_named_document(monkeypatch):
    _patch(monkeypatch, "SUMMARIZE")
    seen = {}
    monkeypatch.setattr(
        rag,
        "summarize",
        lambda q, sources, user_id="default": seen.update(sources=sources)
        or {"answer": "sum", "citations": []},
    )
    agent.run(
        "summarize the leave policy", [], ["Acme_Leave_Policy.pdf", "Acme_IT_Security_Policy.pdf"]
    )
    assert seen["sources"] == ["Acme_Leave_Policy.pdf"]


# ------------------------------------------------------------------------ streaming
def _stream_patch(monkeypatch, route_word, refused=False):
    _patch(monkeypatch, route_word)

    def fake_stream(q, mode, user_id="default"):
        if not refused:
            yield {"type": "token", "text": "Hi "}
            yield {"type": "token", "text": "there"}
        yield {
            "type": "final",
            "data": {
                "answer": "Hi there" if not refused else rag.REFUSAL,
                "citations": [],
                "refused": refused,
            },
        }

    monkeypatch.setattr(rag, "answer_stream", fake_stream)
    monkeypatch.setattr(llm, "chat_stream", lambda *a, **k: iter(["general ", "reply"]))


def test_stream_search_yields_tokens_then_final(monkeypatch):
    _stream_patch(monkeypatch, "SEARCH")
    events = list(agent.run_stream("q", [], ["a.pdf"]))
    assert [e["type"] for e in events] == ["token", "token", "final"]
    final = events[-1]["data"]
    assert (
        final["route"] == "SEARCH"
        and final["from_documents"] is True
        and final["standalone_question"] == "q"
    )


def test_stream_non_search_route_is_a_single_final_event(monkeypatch):
    _stream_patch(monkeypatch, "CHAT")
    events = list(agent.run_stream("thanks", [], ["a.pdf"]))
    assert [e["type"] for e in events] == ["final"] and events[0]["data"]["route"] == "CHAT"


def test_stream_refusal_falls_back_to_streamed_general_answer(monkeypatch):
    _stream_patch(monkeypatch, "SEARCH", refused=True)
    events = list(agent.run_stream("q", [], ["a.pdf"], allow_general=True))
    assert [e["text"] for e in events if e["type"] == "token"] == ["general ", "reply"]
    final = events[-1]["data"]
    assert final["answer"] == "general reply" and final["from_documents"] is False
    strict = list(agent.run_stream("q", [], ["a.pdf"], allow_general=False))
    assert [e["type"] for e in strict] == ["final"] and strict[0]["data"]["refused"] is True
