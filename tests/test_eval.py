import json

import pytest

from eval import build_real_corpus as brc
from eval import judge as judge_mod
from eval.metrics import compare_modes, sign_test, summarize, wilson_ci


def test_wilson_interval_matches_known_values():
    assert wilson_ci(8, 10) == (49.0, 94.3)
    assert wilson_ci(0, 10)[0] == 0.0 and wilson_ci(10, 10)[1] == 100.0
    assert wilson_ci(0, 0) is None
    lo, hi = wilson_ci(60, 100)
    assert lo < 60 < hi and hi - lo > 15  # ~100 questions => a wide interval


def test_sign_test():
    assert sign_test(10, 2) == pytest.approx(0.0386, abs=1e-4)
    assert sign_test(0, 0) == 1.0 and sign_test(5, 5) == 1.0


def _row(q, correct, answerable=True):
    return {
        "question": q,
        "answerable": answerable,
        "correct": correct,
        "hit": correct,
        "refused": False,
    }


def test_compare_modes_counts_discordant_pairs():
    a = [_row("1", True), _row("2", False), _row("3", False), _row("4", True)]
    b = [_row("1", True), _row("2", True), _row("3", True), _row("4", False)]
    out = compare_modes(a, b)
    assert out["only_a_right"] == 1 and out["only_b_right"] == 2 and out["p_value"] == 1.0


def test_summarize_reports_cis_and_judge():
    rows = [
        {**_row("1", True), "judge_correct": True},
        {**_row("2", False), "judge_correct": False},
        {"question": "u", "answerable": False, "refused": True},
        {"question": "v", "answerable": False, "refused": False},
    ]
    s = summarize(rows)
    assert s["answer_accuracy_%"] == 50.0 and s["hallucination_%"] == 50.0
    assert s["judge_accuracy_%"] == 50.0 and "-" in s["answer_accuracy_95ci"]
    assert "judge_accuracy_%" not in summarize([_row("1", True)])


def test_judge_parsing_and_failure(monkeypatch):
    assert judge_mod.parse_verdict('noise {"correct": true, "reason": "same"} noise') is True
    assert judge_mod.parse_verdict('{"correct": false}') is False
    assert (
        judge_mod.parse_verdict("no json") is None
        and judge_mod.parse_verdict('{"correct": "yes"}') is None
    )
    monkeypatch.setattr(judge_mod.llm, "chat", lambda *a, **k: '{"correct": true}')
    assert judge_mod.judge("q", "24 days", "It is 24 days.") is True

    def boom(*a, **k):
        raise judge_mod.llm.LLMError("down")

    monkeypatch.setattr(judge_mod.llm, "chat", boom)
    assert judge_mod.judge("q", "r", "a") is None
    assert judge_mod.reference_for({"gold_answer": "x", "keywords": ["y"]}) == "x"
    assert judge_mod.reference_for({"keywords": ["a", "b"]}) == "a, b"


BATCH = [{"source": "a.pdf", "page": 1, "text": "Employees receive  24 days\nof leave per year."}]


def test_generated_items_need_a_verbatim_quote_on_the_cited_page():
    good = {
        "question": "How many days?",
        "answer": "24 days",
        "evidence": "receive 24 days of leave",
        "source": "a.pdf",
        "page": 1,
    }
    row = brc.validate_item(good, BATCH, set())
    assert row and row["answerable"] and row["keywords"][0] == "24"
    assert (
        brc.validate_item({**good, "evidence": "receive 30 days"}, BATCH, set()) is None
    )  # invented quote
    assert brc.validate_item({**good, "page": 2}, BATCH, set()) is None  # wrong page
    assert brc.validate_item({**good, "source": "b.pdf"}, BATCH, set()) is None
    assert brc.validate_item(good, BATCH, {"how many days?"}) is None  # duplicate
    assert brc.validate_item({**good, "answer": ""}, BATCH, set()) is None
    assert brc.validate_item("junk", BATCH, set()) is None


def test_unanswerable_loader(tmp_path):
    f = tmp_path / "u.json"
    f.write_text(
        json.dumps([{"question": " Q1? ", "note": "x"}, {"question": ""}]), encoding="utf-8"
    )
    assert brc.load_unanswerable(f) == [{"question": "Q1?", "answerable": False}]
    f.write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit):
        brc.load_unanswerable(f)
