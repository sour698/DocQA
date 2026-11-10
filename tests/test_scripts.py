"""Operator scripts that touch the database."""

import pytest

from app import analytics, auth, db
from app.ingest import chunk_pages
from app.store import get_store
from scripts import delete_account, maintenance, reindex

pytestmark = pytest.mark.usefixtures("pg")


def test_reindex_rebuilds_chunks_from_the_stored_files(user_id, monkeypatch):
    store = get_store(user_id)
    text = b"Annual leave is 24 days.\n\nSick leave is 12 days."
    store.put_document(
        "notes.txt", text, chunk_pages([(1, "stale old chunk text here")], "notes.txt", 30, 5), 1
    )
    assert [c["text"] for c in store.chunks_for_source("notes.txt")] == [
        "stale old chunk text here"
    ]
    assert reindex.reindex_user(user_id, log=lambda *_: None) == 1
    assert "24 days" in " ".join(c["text"] for c in store.chunks_for_source("notes.txt"))
    assert store.get_document("notes.txt") == text


def test_reindex_all_covers_every_account(monkeypatch):
    for uid in ("acct-1", "acct-2"):
        get_store(uid).put_document(
            "n.txt", b"Some searchable words.", chunk_pages([(1, "old")], "n.txt", 30, 5), 1
        )
    assert reindex.reindex_all(log=lambda *_: None) == 2


def test_reindex_skips_documents_with_no_readable_text(user_id):
    get_store(user_id).put_document("blank.txt", b"   \n  ", [], 0)
    assert reindex.reindex_user(user_id, log=lambda *_: None) == 0
    assert get_store(user_id).list_sources() == ["blank.txt"]  # the file itself is kept


def test_maintenance_prunes_only_old_rows():
    analytics.log_query("u", "recent question")
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO query_log (ts, user_id, question) VALUES (now() - interval '200 days', 'u', 'ancient')"
        )
        conn.execute(
            "INSERT INTO audit_log (ts, event) VALUES (now() - interval '200 days', 'old_event')"
        )
        conn.execute("INSERT INTO audit_log (event) VALUES ('fresh_event')")
        conn.execute(
            "INSERT INTO rate_limits (key, window_start, hits) VALUES ('k', now() - interval '3 days', 5)"
        )
        conn.execute(
            "INSERT INTO budget VALUES ('u', current_date - 60, 3), ('u', current_date, 1)"
        )
    counts = maintenance.prune(keep_days=90)
    assert counts["query_log"] == 1 and counts["audit_log"] == 1
    assert counts["rate_limits"] == 1 and counts["budget"] == 1
    with db.connection() as conn:
        assert [r["question"] for r in conn.execute("SELECT question FROM query_log")] == [
            "recent question"
        ]
        assert [r["event"] for r in conn.execute("SELECT event FROM audit_log")] == ["fresh_event"]


def test_delete_account_removes_everything_about_it(user_id):
    uid = auth.register("gone@example.com", "a-very-long-test-password")
    other = auth.register("stays@example.com", "a-very-long-test-password")
    for who in (uid, other):
        get_store(who).put_document(
            "n.txt", b"x", chunk_pages([(1, "Some searchable words.")], "n.txt", 30, 5), 1
        )
        analytics.log_query(who, "a question")
        analytics.log_feedback(who, "q", "a", 1)
        analytics.audit("login", who)
    counts = delete_account.delete_account("GONE@example.com")
    assert counts["documents"] == 1 and counts["query_log"] == 1 and counts["feedback"] == 1
    with db.connection() as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM users WHERE id=%s", (uid,)).fetchone()["n"] == 0
        )
        assert (
            conn.execute("SELECT count(*) AS n FROM chunks WHERE user_id=%s", (uid,)).fetchone()[
                "n"
            ]
            == 0
        )
        assert (
            conn.execute("SELECT count(*) AS n FROM audit_log WHERE user_id=%s", (uid,)).fetchone()[
                "n"
            ]
            == 0
        )
        assert (
            conn.execute("SELECT count(*) AS n FROM users WHERE id=%s", (other,)).fetchone()["n"]
            == 1
        )
        assert (
            conn.execute("SELECT count(*) AS n FROM chunks WHERE user_id=%s", (other,)).fetchone()[
                "n"
            ]
            >= 1
        )
    with pytest.raises(ValueError, match="No account"):
        delete_account.delete_account("gone@example.com")
