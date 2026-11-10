"""Integration tests: real PostgreSQL/pgvector + full-text search + RAG pipeline, with fake embedder/reranker/LLM."""

import pytest

from app import llm, rag
from app.ingest import chunk_pages
from app.store import Store, get_store

pytestmark = pytest.mark.usefixtures("pg")


def put(store, name, pages, chunk_words=30, overlap=5):
    store.put_document(
        name,
        b"original bytes of " + name.encode(),
        chunk_pages(pages, name, chunk_words, overlap),
        len(pages),
    )


def _index(user_id):
    store = get_store(user_id)
    leave = [
        (
            1,
            "ANNUAL LEAVE\nFull time employees receive 24 days of annual leave each year.\n"
            "Carry Forward\nUp to 10 unused days may be carried forward.",
        ),
        (2, "SICK LEAVE\nEmployees may take 12 days of paid sick leave per year."),
    ]
    it = [(1, "PASSWORDS\nPasswords must contain 14 characters and be rotated every 90 days.")]
    put(store, "Acme_Leave_Policy.pdf", leave)
    put(store, "Acme_IT_Security_Policy.pdf", it)
    return store


def test_list_sources_and_delete(user_id):
    store = _index(user_id)
    assert store.list_sources() == ["Acme_IT_Security_Policy.pdf", "Acme_Leave_Policy.pdf"]
    assert store.delete_source("Acme_IT_Security_Policy.pdf") is True
    assert store.list_sources() == ["Acme_Leave_Policy.pdf"]
    assert store.delete_source("Acme_IT_Security_Policy.pdf") is False  # already gone
    assert store.hybrid_search("password rotation", 5) == [] or all(
        c.startswith("Acme_Leave") for c in store.hybrid_search("password rotation", 5)
    )


def test_original_file_is_stored_with_the_chunks(user_id):
    store = _index(user_id)
    assert store.get_document("Acme_Leave_Policy.pdf") == b"original bytes of Acme_Leave_Policy.pdf"
    assert store.get_document("missing.pdf") is None


def test_accounts_are_isolated():
    a, b = get_store("acct-a"), get_store("acct-b")
    put(a, "a.pdf", [(1, "Secret alpha plan.")], 20, 5)
    assert b.list_sources() == [] and a.list_sources() == ["a.pdf"]
    assert b.dense_search("secret alpha plan", 5) == [] and b.bm25_search("secret alpha", 5) == []
    assert b.get_document("a.pdf") is None and b.delete_source("a.pdf") is False
    assert a.list_sources() == ["a.pdf"]


def test_reupload_replaces_chunks_and_file(user_id):
    store = _index(user_id)
    before = store.count_chunks()
    put(store, "Acme_Leave_Policy.pdf", [(1, "Only one short page now about parental leave.")])
    assert store.list_sources().count("Acme_Leave_Policy.pdf") == 1
    assert [c["text"] for c in store.chunks_for_source("Acme_Leave_Policy.pdf")] == [
        "Only one short page now about parental leave."
    ]
    assert store.count_chunks() < before


def test_keyword_search_survives_hostile_input(user_id):
    store = _index(user_id)
    for q in ["'; DROP TABLE users; --", "a & b | !c :* (d)", '\\ " \x00', "   ", "!!!"]:
        assert isinstance(store.bm25_search(q, 5), list)
        assert isinstance(store.hybrid_search(q, 5), list)
    assert store.bm25_search("sick leave", 3)  # still works afterwards


def test_search_modes_find_the_right_page(user_id):
    store = _index(user_id)
    q = "how many days of sick leave"
    for ids in (store.dense_search(q, 3), store.bm25_search(q, 3), store.hybrid_search(q, 5)):
        assert any(c["page"] == 2 for c in store.get(ids))
    top = store.rerank(q, store.hybrid_search(q, 10), 3)[0]
    assert top["source"] == "Acme_Leave_Policy.pdf" and top["page"] == 2


def test_source_filter_restricts_all_searchers(user_id):
    store = _index(user_id)
    only = ["Acme_IT_Security_Policy.pdf"]
    for fn in (store.dense_search, store.bm25_search, store.hybrid_search):
        ids = fn("leave days", 10, only)
        assert all(c["source"] == only[0] for c in store.get(ids))


def test_neighbor_context_is_added(user_id):
    store = _index(user_id)
    q = "carried forward unused days"
    hit = store.rerank(q, store.hybrid_search(q, 10), 1, neighbors=1)[0]
    assert hit["context"].startswith(hit["text"][:20]) or hit["text"] in hit["context"]
    assert len(hit["context"]) >= len(hit["text"])
    assert "context" not in store.rerank(q, store.hybrid_search(q, 10), 1)[0]


def test_chunks_for_source_in_reading_order(user_id):
    store = _index(user_id)
    pages = [c["page"] for c in store.chunks_for_source("Acme_Leave_Policy.pdf")]
    assert pages == sorted(pages)


# ------------------------------------------------------------------------------- RAG
def test_answer_cites_only_passages_the_model_used(monkeypatch, user_id):
    _index(user_id)
    seen = {}

    def fake_chat(system, prompt, temperature=0.0):
        seen["prompt"] = prompt
        return "Employees get 24 days of annual leave [1]."

    monkeypatch.setattr(llm, "chat", fake_chat)
    out = rag.answer("How many days of annual leave do employees get?", user_id=user_id)
    assert not out["refused"] and out["confidence"] in {"high", "medium", "low"}
    assert [c["ref"] for c in out["citations"]] == [1]
    assert out["citations"][0]["cited"] is True and out["citation_audit"]["valid_refs"] == [1]
    assert "untrusted" in rag.SYSTEM_PROMPT and "Context:" in seen["prompt"]


def test_answer_without_any_citation_shows_top_passages_unmarked(monkeypatch, user_id):
    _index(user_id)
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "24 days.")
    out = rag.answer("annual leave days per year", user_id=user_id)
    assert 1 <= len(out["citations"]) <= 3 and not any(c["cited"] for c in out["citations"])


def test_unrelated_question_is_refused_without_calling_llm(monkeypatch, user_id):
    _index(user_id)
    monkeypatch.setattr(llm, "chat", lambda *a, **k: pytest.fail("LLM must not be called"))
    out = rag.answer("zebra quantum volcano", user_id=user_id)
    assert out["refused"] and out["answer"] == rag.REFUSAL and out["citations"] == []


def test_model_refusal_is_detected_in_any_formatting(monkeypatch, user_id):
    _index(user_id)
    monkeypatch.setattr(
        llm, "chat", lambda *a, **k: "**I don\u2019t know based on the provided documents.**"
    )
    out = rag.answer("annual leave days", user_id=user_id)
    assert out["refused"] and out["citations"] == []


def test_question_naming_a_document_searches_only_that_document(monkeypatch, user_id):
    _index(user_id)
    hits = rag.retrieve("password rotation in the IT security policy", "hybrid_rerank", user_id)
    assert hits and {h["source"] for h in hits} == {"Acme_IT_Security_Policy.pdf"}


def test_naive_mode_never_refuses_on_score(monkeypatch, user_id):
    _index(user_id)
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "Something [1]")
    assert rag.answer("zebra quantum volcano", mode="naive", user_id=user_id)["refused"] is False


def test_stream_emits_tokens_then_final(monkeypatch, user_id):
    _index(user_id)
    monkeypatch.setattr(llm, "chat_stream", lambda *a, **k: iter(["24 days ", "[1]"]))
    events = list(rag.answer_stream("annual leave days per year", user_id=user_id))
    assert [e["type"] for e in events] == ["token", "token", "final"]
    final = events[-1]["data"]
    assert final["answer"] == "24 days [1]" and "retrieved" not in final and final["citations"]


def test_stream_refusal_has_no_tokens(user_id):
    _index(user_id)
    events = list(rag.answer_stream("zebra quantum volcano", user_id=user_id))
    assert [e["type"] for e in events] == ["final"] and events[0]["data"]["refused"]


def test_summarize_samples_the_named_document(monkeypatch, user_id):
    _index(user_id)
    monkeypatch.setattr(llm, "chat", lambda s, p, temperature=0.0: "Summary text")
    out = rag.summarize("summarize", ["Acme_Leave_Policy.pdf"], user_id=user_id)
    assert out["answer"].startswith("Summary text") and out["citations"]
    assert rag.summarize("x", [], user_id=user_id)["citations"] == []


def test_confidence_labels():
    assert rag.confidence_label(5) == "high" and rag.confidence_label(1) == "medium"
    assert rag.confidence_label(-2) == "low" and rag.confidence_label(None) is None


def test_store_class_can_be_built_directly():
    assert Store("direct-test").list_sources() == []
