"""Moving an old SQLite + uploads installation into PostgreSQL."""

import sqlite3
import time

import pytest

from app import auth, db
from app.store import get_store
from app.utils import user_key
from scripts import migrate_from_sqlite

pytestmark = pytest.mark.usefixtures("pg")

PW = "a-very-long-test-password"


def build_legacy(tmp_path):
    """The schema used before the PostgreSQL release, with one account that really can log in."""
    email = "legacy@example.com"
    uid = auth.user_id_for(email)
    salt = b"\x01" * 16
    con = sqlite3.connect(tmp_path / "accounts.sqlite3")
    con.executescript("""
        CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, salt BLOB NOT NULL,
                            password_hash BLOB NOT NULL, created REAL NOT NULL);
        CREATE TABLE budget (user_id TEXT NOT NULL, day TEXT NOT NULL, used INTEGER NOT NULL,
                             PRIMARY KEY (user_id, day));
        CREATE TABLE query_log (id INTEGER PRIMARY KEY, ts REAL, user_id TEXT, question TEXT,
                                route TEXT, refused INTEGER, from_documents INTEGER,
                                top_score REAL, latency_ms INTEGER);
        CREATE TABLE feedback (id INTEGER PRIMARY KEY, ts REAL, user_id TEXT, question TEXT,
                               answer TEXT, rating INTEGER, comment TEXT);
        """)
    con.execute(
        "INSERT INTO users VALUES (?,?,?,?,?)",
        (uid, email, salt, auth._hash(PW, salt), time.time()),
    )
    con.execute("INSERT INTO budget VALUES (?,?,?)", (uid, "2026-01-05", 7))
    con.execute(
        "INSERT INTO query_log VALUES (1,?,?,?,?,?,?,?,?)",
        (time.time(), uid, "old question?", "SEARCH", 1, 0, -4.2, 800),
    )
    con.execute(
        "INSERT INTO feedback VALUES (1,?,?,?,?,?,?)", (time.time(), uid, "q", "a", -1, "wrong")
    )
    con.commit()
    con.close()
    folder = tmp_path / "uploads" / user_key(uid)
    folder.mkdir(parents=True)
    (folder / "leave.txt").write_text(
        "Employees receive 24 days of annual leave each year.", encoding="utf-8"
    )
    (folder / "scan.txt").write_text("   ", encoding="utf-8")  # unreadable -> skipped, not fatal
    (tmp_path / "uploads" / "unrelated-folder").mkdir()
    return uid


def test_accounts_documents_and_history_move_over(tmp_path):
    uid = build_legacy(tmp_path)
    report = migrate_from_sqlite.migrate(
        tmp_path / "accounts.sqlite3", tmp_path / "uploads", log=lambda *_: None
    )
    assert report["users"] == 1 and report["documents"] == 1 and report["skipped"] == 1
    assert report["budget"] == 1 and report["query_log"] == 1 and report["feedback"] == 1

    assert auth.verify(auth.login("legacy@example.com", PW)) == uid  # same password still works
    store = get_store(uid)
    assert store.list_sources() == ["leave.txt"]
    assert store.get_document("leave.txt").startswith(b"Employees receive")
    assert store.hybrid_search("annual leave days", 3)  # searchable in the new database
    with db.connection() as conn:
        assert (
            conn.execute("SELECT used FROM budget WHERE user_key=%s", (uid,)).fetchone()["used"]
            == 7
        )
        assert conn.execute("SELECT refused FROM query_log").fetchone()["refused"] is True


def test_migration_can_be_run_twice(tmp_path):
    build_legacy(tmp_path)
    args = (tmp_path / "accounts.sqlite3", tmp_path / "uploads")
    migrate_from_sqlite.migrate(*args, log=lambda *_: None)
    again = migrate_from_sqlite.migrate(*args, log=lambda *_: None)
    assert again["users"] == 0  # already there: not duplicated
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"] == 1
    assert len(get_store(auth.user_id_for("legacy@example.com")).list_sources()) == 1


def test_works_with_the_oldest_schema_and_no_uploads(tmp_path):
    con = sqlite3.connect(tmp_path / "old.sqlite3")
    con.execute(
        "CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT, salt BLOB, password_hash BLOB, created REAL)"
    )
    con.execute("INSERT INTO users VALUES ('u1','x@y.zz',x'00',x'00',0)")
    con.commit()
    con.close()
    report = migrate_from_sqlite.migrate(tmp_path / "old.sqlite3", log=lambda *_: None)
    assert report["users"] == 1 and report["documents"] == 0
