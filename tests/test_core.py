import pytest

from app.ingest import chunk_pages
from app.utils import (
    contextual_text,
    normalize_text,
    pick_sources,
    rrf,
    safe_filename,
    sample_evenly,
    tokenize,
)


def test_tokenize():
    assert tokenize("Leave: 30 days!") == ["leave", "30", "days"]


def test_rrf_prefers_items_ranked_high_in_both():
    fused = rrf([["a", "b", "c"], ["b", "a", "d"]])
    assert set(fused[:2]) == {"a", "b"} and set(fused) == {"a", "b", "c", "d"}


def test_chunk_pages_keeps_page_and_overlap():
    words = " ".join(f"w{i}" for i in range(400))
    chunks = chunk_pages([(7, words)], "doc.pdf", chunk_words=100, overlap=20)
    assert all(c["page"] == 7 and c["source"] == "doc.pdf" for c in chunks)
    assert chunks[0]["text"].split()[80:] == chunks[1]["text"].split()[:20]
    assert len({c["id"] for c in chunks}) == len(chunks)


def test_normalize_text_handles_llm_quirks():
    assert normalize_text("**8,000** \u2013 days") == "8000 - days"


def test_sample_evenly_keeps_order_and_ends():
    picked = sample_evenly(list(range(10)), 3)
    assert picked[0] == 0 and picked[-1] == 9 and picked == sorted(picked)
    assert sample_evenly([1, 2], 5) == [1, 2] and sample_evenly([], 3) == []


def test_pick_sources_matches_document_names():
    docs = ["Acme_Leave_Policy.pdf", "Acme_IT_Security_Policy.pdf"]
    assert pick_sources("summarize the leave policy", docs) == ["Acme_Leave_Policy.pdf"]
    assert pick_sources("what is this about?", docs) == docs


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("a/b/../../etc/passwd.pdf", "passwd.pdf"),
        ("..\\evil.pdf", "evil.pdf"),
        ("my:doc::1.pdf", "my_doc_1.pdf"),
        ("Report (final).DOCX", "Report (final).DOCX"),
    ],
)
def test_safe_filename_accepts_and_cleans(raw, expected):
    assert safe_filename(raw) == expected


@pytest.mark.parametrize("raw", ["", ".pdf", "..", "run.exe", "noext"])
def test_safe_filename_rejects(raw):
    with pytest.raises(ValueError):
        safe_filename(raw)


def test_safe_filename_caps_length_and_keeps_extension():
    name = safe_filename("x" * 400 + ".pdf")
    assert len(name) <= 150 and name.endswith(".pdf")


def test_contextual_text_adds_title_and_heading():
    out = contextual_text(
        {"source": "Acme_Leave_Policy.pdf", "heading": "Sick Leave", "text": "12 days"}
    )
    assert out.startswith("Acme Leave Policy. Sick Leave\n") and out.endswith("12 days")
