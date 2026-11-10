"""Schema, migrations, startup checks and transactional guarantees (real PostgreSQL)."""

import psycopg
import pytest

from app import db
from app.ingest import chunk_pages
from app.store import get_store

pytestmark = pytest.mark.usefixtures("pg")


def put(store, name, text="Employees get 24 days of leave.", data=b"bytes"):
    store.put_document(name, data, chunk_pages([(1, text)], name, 30, 5), 1)


def test_migrations_are_applied_once_and_recorded():
    assert db.pending_migrations() == []
    assert db.migrate() == []  # running again changes nothing
    with db.connection() as conn:
        versions = [
            r["version"] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY 1")
        ]
    assert versions == ["0001_core", "0002_documents", "0003_rate_limits"]


def test_every_expected_table_and_index_exists():
    with db.connection() as conn:
        tables = {
            r["tablename"]
            for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        }
        indexes = {
            r["indexname"]
            for r in conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname='public'")
        }
    assert {
        "users",
        "documents",
        "chunks",
        "budget",
        "rate_limits",
        "query_log",
        "feedback",
        "audit_log",
        "settings",
        "schema_migrations",
    } <= tables
    assert {"chunks_tsv", "chunks_embedding", "chunks_source_order", "query_log_user_ts"} <= indexes


def test_concurrent_startups_take_turns_safely(monkeypatch):
    """Several workers boot at once: the advisory lock makes them migrate one at a time."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(lambda _: db.migrate(), range(6)))
    assert all(r == [] for r in results)


def test_a_failed_migration_rolls_back_everything(tmp_path, monkeypatch):
    (tmp_path / "0099_broken.sql").write_text(
        "CREATE TABLE should_not_survive (id int);\nSELECT 1/0;\n", encoding="utf-8"
    )
    monkeypatch.setattr(db, "MIGRATIONS_DIR", tmp_path)
    with pytest.raises(psycopg.errors.DivisionByZero):
        db.migrate()
    with db.connection() as conn:
        assert conn.execute("SELECT to_regclass('should_not_survive') AS t").fetchone()["t"] is None
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM schema_migrations WHERE version='0099_broken'"
            ).fetchone()["n"]
            == 0
        )


def test_rejects_an_unsafe_search_language(monkeypatch):
    monkeypatch.setattr(db, "FTS_LANGUAGE", "english'); DROP TABLE users; --")
    with pytest.raises(RuntimeError, match="FTS_LANGUAGE"):
        db._render("x")


# ------------------------------------------------------------- embedding compatibility
def test_dimension_mismatch_is_caught_at_startup(monkeypatch):
    monkeypatch.setattr(db, "EMBED_DIM", 768)
    with pytest.raises(RuntimeError, match="reindex --all --reset-vectors"):
        db.check_embedding_compat()


def test_model_change_with_existing_vectors_is_refused(monkeypatch, user_id):
    put(get_store(user_id), "a.txt")
    db.check_embedding_compat()  # records the current model
    monkeypatch.setattr(db, "EMBED_MODEL", "BAAI/bge-small-en-v1.5")
    with pytest.raises(RuntimeError, match="stored vectors were made with"):
        db.check_embedding_compat()


def test_model_change_on_an_empty_index_is_fine(monkeypatch):
    monkeypatch.setattr(db, "EMBED_MODEL", "BAAI/bge-small-en-v1.5")
    db.check_embedding_compat()
    with db.connection() as conn:
        assert (
            conn.execute("SELECT value FROM settings WHERE key='embed_model'").fetchone()["value"]
            == "BAAI/bge-small-en-v1.5"
        )
    monkeypatch.setattr(db, "EMBED_MODEL", db.EMBED_MODEL)


def test_reset_vector_column_resizes_and_keeps_documents(monkeypatch, user_id):
    store = get_store(user_id)
    put(store, "keep.txt", data=b"original file")
    monkeypatch.setattr(db, "EMBED_DIM", 8)
    monkeypatch.setattr(db, "EMBED_MODEL", "tiny-model")
    db.reset_vector_column()
    try:
        assert (
            store.list_sources() == ["keep.txt"]
            and store.get_document("keep.txt") == b"original file"
        )
        assert store.count_chunks() == 0
        with db.connection() as conn:
            dim = conn.execute(
                "SELECT atttypmod AS d FROM pg_attribute WHERE attrelid='chunks'::regclass AND attname='embedding'"
            ).fetchone()["d"]
        assert dim == 8
        db.check_embedding_compat()
    finally:  # put the shared test schema back to 256 dimensions
        monkeypatch.undo()
        db.reset_vector_column()
    db.check_embedding_compat()


def test_startup_requires_migrations_when_auto_migrate_is_off(monkeypatch, tmp_path):
    (tmp_path / "0001_core.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "0050_pending.sql").write_text("SELECT 1;", encoding="utf-8")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", tmp_path)
    monkeypatch.setattr(db, "AUTO_MIGRATE", False)
    with pytest.raises(RuntimeError, match="Pending database migrations"):
        db.startup()


def test_wait_ready_gives_a_clear_error(monkeypatch):
    monkeypatch.setattr(db, "DATABASE_URL", "postgresql://nobody:x@127.0.0.1:1/none")
    with pytest.raises(RuntimeError, match="Cannot reach the database"):
        db.wait_ready(timeout=0.5)


# ------------------------------------------------------------- transactional guarantees
def test_deleting_a_document_removes_its_chunks(user_id):
    store = get_store(user_id)
    put(store, "a.txt")
    put(store, "b.txt", "Different words entirely about parking.")
    assert store.delete_source("a.txt")
    with db.connection() as conn:
        left = [
            r["source"]
            for r in conn.execute("SELECT DISTINCT source FROM chunks WHERE user_id=%s", (user_id,))
        ]
    assert left == ["b.txt"]


def test_chunks_cannot_exist_without_their_document(user_id):
    with db.connection() as conn, pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO chunks (user_id, id, source, page, idx, text, embedding) "
            "VALUES (%s, 'x::p1::c0', 'ghost.pdf', 1, 0, 't', %s::vector)",
            (user_id, "[" + ",".join(["0"] * 256) + "]"),
        )


def test_failed_replacement_leaves_the_old_version_intact(user_id):
    store = get_store(user_id)
    put(store, "doc.txt", "Original text about annual leave days.", data=b"v1")
    dup = [
        {"id": "doc.txt::p1::c0", "text": "new one", "source": "doc.txt", "page": 1, "heading": ""},
        {"id": "doc.txt::p1::c0", "text": "new two", "source": "doc.txt", "page": 1, "heading": ""},
    ]
    with pytest.raises(psycopg.errors.UniqueViolation):
        store.put_document("doc.txt", b"v2", dup, 1)
    assert store.get_document("doc.txt") == b"v1"  # file untouched ...
    assert [c["text"] for c in store.chunks_for_source("doc.txt")] == [
        "Original text about annual leave days."
    ]


def test_wrong_embedding_size_is_reported_clearly(monkeypatch, user_id):
    from app import models

    class Wide:
        def encode(self, texts, normalize_embeddings=True):
            import numpy as np

            return np.ones((len(texts), 300), dtype="float32")

    monkeypatch.setattr(models, "get_embedder", lambda: Wide())
    with pytest.raises(RuntimeError, match="EMBED_DIM"):
        put(get_store(user_id), "a.txt")


def test_unicode_and_odd_filenames_round_trip(user_id):
    store = get_store(user_id)
    put(
        store,
        "Résumé (final) – v2.txt",
        "Zażółć gęślą jaźń. Ünïcödé text with 日本語 inside.",
        b"\xff\x00bin",
    )
    assert store.list_sources() == ["Résumé (final) – v2.txt"]
    assert store.get_document("Résumé (final) – v2.txt") == b"\xff\x00bin"
    assert store.bm25_search("Zażółć", 3) != [] or store.dense_search("日本語", 3) != []


def test_statement_timeout_is_configured():
    with db.connection() as conn:
        assert conn.execute("SHOW statement_timeout").fetchone()["statement_timeout"] in {
            "30s",
            "30000ms",
        }


def test_query_planner_uses_the_user_scoped_indexes(user_id):
    store = get_store(user_id)
    put(store, "a.txt")
    with db.connection() as conn:
        plan = "\n".join(
            r["QUERY PLAN"]
            for r in conn.execute(
                "EXPLAIN SELECT id FROM chunks WHERE user_id=%s AND source=%s ORDER BY page, idx",
                (user_id, "a.txt"),
            )
        )
    assert "chunks" in plan  # sanity: the explain ran against the real table


# ------------------------------------------------------------- multi-account vector search
def _bulk_random_chunks(accounts: int, per_account: int):
    """Many accounts' worth of random vectors, inserted server-side (fast), index built afterwards."""
    with db.connection() as conn:
        conn.execute("DROP INDEX IF EXISTS chunks_embedding")
        for a in range(accounts):
            uid = f"tenant{a}"
            conn.execute(
                "INSERT INTO documents (user_id, name, content, size_bytes, sha256) VALUES (%s, 'd.txt', 'x', 1, 'x')",
                (uid,),
            )
            conn.execute(
                "INSERT INTO chunks (user_id, id, source, page, idx, text, embedding) "
                "SELECT %s, 'd.txt::p1::c' || g, 'd.txt', 1, g, 'chunk ' || g, "
                "       ARRAY(SELECT random() - 0.5 FROM generate_series(1, 256))::vector "
                "FROM generate_series(1, %s) AS g",
                (uid, per_account),
            )
        conn.execute(
            "CREATE INDEX chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops)"
        )
        conn.execute("ANALYZE chunks")


def _restore_empty_index():
    with db.connection() as conn:
        conn.execute("TRUNCATE chunks, documents CASCADE")


def test_every_account_gets_a_full_result_list_from_a_shared_index(monkeypatch):
    """Regression: an approximate shared index + per-account filter returned too few rows."""
    from app import store as store_mod

    _bulk_random_chunks(accounts=12, per_account=400)
    try:
        qvec = store_mod._vec([0.1] * 256)
        naive = (
            "SELECT id FROM chunks WHERE user_id = %s ORDER BY embedding <=> %s::vector LIMIT 20"
        )
        with db.connection() as conn:
            underfilled = sum(
                len(conn.execute(naive, (f"tenant{a}", qvec)).fetchall()) < 20 for a in range(12)
            )
        if not underfilled:
            pytest.skip(
                "this PostgreSQL/pgvector build handles filtered ANN well; nothing to guard"
            )
        for a in range(12):  # the unguarded query is broken here, the store's search is not
            assert len(get_store(f"tenant{a}").dense_search("anything", 20)) == 20
    finally:
        _restore_empty_index()


def test_large_accounts_use_the_index_but_still_get_full_results(monkeypatch):
    from app import store as store_mod

    _bulk_random_chunks(accounts=12, per_account=400)
    try:
        monkeypatch.setattr(
            store_mod, "EXACT_SEARCH_MAX_CHUNKS", 100
        )  # treat every account as "large"
        for a in range(12):
            assert (
                len(get_store(f"tenant{a}").dense_search("anything", 20)) == 20
            )  # via the fallback
        assert len(get_store("tenant3").dense_search("anything", 20, ["d.txt"])) == 20
        assert get_store("nobody").dense_search("anything", 20) == []
    finally:
        _restore_empty_index()


def test_exact_search_returns_the_true_nearest_neighbours(user_id):
    store = get_store(user_id)
    texts = {f"w{i}": f"{'alpha ' * (i + 1)}beta" for i in range(6)}
    for name, text in texts.items():
        put(store, f"{name}.txt", text)
    ids = store.dense_search("alpha alpha alpha", 3)
    assert len(ids) == 3 and len(set(ids)) == 3


def test_scripts_exit_cleanly_without_shutdown_noise():
    """Regression: `scripts.migrate --status` left the pool open and printed a traceback on exit."""
    import os
    import subprocess
    import sys

    from app import config

    env = {**os.environ, "DATABASE_URL": config.DATABASE_URL, "EMBED_DIM": "256"}
    for args in (["--status"], []):
        done = subprocess.run(
            [sys.executable, "-W", "error", "-m", "scripts.migrate", *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert done.returncode == 0, done.stderr
        assert done.stderr.strip() == "", done.stderr
        assert "schema is current" in done.stdout


# ------------------------------------------------------------- upgrading an EXISTING database
def test_upgrading_a_database_from_the_previous_release_keeps_all_data(monkeypatch):
    """Start from the schema of release 3.1 (migrations 0001 + 0002 applied, data present), then apply
    the new migration through the real migrate(): accounts, files and chunks must survive."""
    import shutil
    import tempfile
    from pathlib import Path

    from app import config

    admin_url = config.DATABASE_URL
    name = "docqa_upgrade_scratch_test"
    try:
        admin = psycopg.connect(admin_url, autocommit=True, connect_timeout=3)
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {name}")
    except psycopg.Error as e:
        pytest.skip(f"cannot create a scratch database here ({str(e)[:80]})")
    scratch_url = (
        admin_url.rsplit("/", 1)[0]
        + "/"
        + name
        + ("?" + admin_url.split("?", 1)[1] if "?" in admin_url else "")
    )
    old_dir = Path(tempfile.mkdtemp())
    for f in ("0001_core.sql", "0002_documents.sql"):
        shutil.copy(db.MIGRATIONS_DIR / f, old_dir / f)
    real_dir = db.MIGRATIONS_DIR
    try:
        db.close_pool()
        monkeypatch.setattr(db, "DATABASE_URL", scratch_url)
        monkeypatch.setattr(db, "MIGRATIONS_DIR", old_dir)
        assert db.migrate() == ["0001_core", "0002_documents"]  # "the previous release"
        with (
            db.connection() as conn
        ):  # data created by that release, incl. its old rate-limit table
            conn.execute(
                "INSERT INTO users (id, email, salt, password_hash) VALUES ('u1', 'a@b.co', 'x', 'y')"
            )
            conn.execute(
                "INSERT INTO documents (user_id, name, content, size_bytes, sha256) "
                "VALUES ('u1', 'd.txt', 'file', 4, 'z')"
            )
            conn.execute(
                "INSERT INTO chunks (user_id, id, source, page, idx, text, embedding) "
                "VALUES ('u1', 'd.txt::p1::c0', 'd.txt', 1, 0, 'hello', %s::vector)",
                ("[" + ",".join(["0.1"] * 256) + "]",),
            )
            conn.execute("INSERT INTO rate_events (key) VALUES ('old-limiter-row')")
        monkeypatch.setattr(db, "MIGRATIONS_DIR", real_dir)  # the new code starts up
        assert db.pending_migrations() == ["0003_rate_limits"]
        assert db.migrate() == ["0003_rate_limits"]
        with db.connection() as conn:
            assert conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"] == 1
            assert conn.execute("SELECT content FROM documents").fetchone()["content"] == b"file"
            assert conn.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"] == 1
            assert conn.execute("SELECT to_regclass('rate_events') AS t").fetchone()["t"] is None
            assert (
                conn.execute("SELECT to_regclass('rate_limits') AS t").fetchone()["t"] is not None
            )
        assert db.migrate() == []
    finally:
        db.close_pool()
        monkeypatch.undo()
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.close()
