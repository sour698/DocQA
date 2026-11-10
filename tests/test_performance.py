"""Guards for the round-trip optimisations (each database round trip is ~1 ms locally but 50-300 ms
to a hosted database, so the NUMBER of them is what matters)."""

import time

import pytest
from psycopg_pool import ConnectionPool

from app import auth, db, rag
from app.ingest import chunk_pages
from app.limits import Budget
from app.store import get_store

pytestmark = pytest.mark.usefixtures("pg")
PW = "a-very-long-test-password"


def put(store, name, text):
    store.put_document(name, b"x", chunk_pages([(1, text)], name, 30, 5), 1)


class Counter:
    """Counts database checkouts made through db.autocommit() / db.connection()."""

    def __init__(self, monkeypatch):
        self.autocommit = self.transaction = 0
        real_auto, real_tx = db.autocommit, db.connection
        outer = self

        def auto():
            outer.autocommit += 1
            return real_auto()

        def tx():
            outer.transaction += 1
            return real_tx()

        monkeypatch.setattr(db, "autocommit", auto)
        monkeypatch.setattr(db, "connection", tx)

    @property
    def total(self):
        return self.autocommit + self.transaction


# ------------------------------------------------------------------------- autocommit helper
def test_autocommit_statements_are_committed_immediately():
    with db.autocommit() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('perf_probe', 'yes')")
        assert conn.autocommit is True
    with db.connection() as other:  # a different connection already sees it
        assert (
            other.execute("SELECT value FROM settings WHERE key='perf_probe'").fetchone()["value"]
            == "yes"
        )
        other.execute("DELETE FROM settings WHERE key='perf_probe'")


def test_connections_go_back_to_normal_transaction_mode():
    with db.autocommit() as conn:
        conn.execute("SELECT 1")
    with db.connection() as conn:
        assert conn.autocommit is False  # a later transactional user must not inherit autocommit


def test_an_error_inside_autocommit_does_not_poison_the_pool():
    with pytest.raises(Exception, match="division by zero"), db.autocommit() as conn:
        conn.execute("SELECT 1/0")
    for _ in range(5):  # every pooled connection still works
        with db.autocommit() as conn:
            assert conn.execute("SELECT 1 AS one").fetchone()["one"] == 1


def test_transactions_still_roll_back_together():
    with pytest.raises(RuntimeError), db.connection() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('perf_tx', 'x')")
        raise RuntimeError("boom")
    with db.autocommit() as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM settings WHERE key='perf_tx'").fetchone()["n"]
            == 0
        )


# ------------------------------------------------------------------------- pool health check
def test_recently_used_connections_skip_the_health_check(monkeypatch):
    checks = []
    monkeypatch.setattr(
        ConnectionPool, "check_connection", staticmethod(lambda conn: checks.append(1))
    )
    with db.autocommit() as conn:
        db._touch(conn)
        recent = conn
        assert db._check(recent) is None
    assert checks == []  # just used: no extra network round trip


def test_idle_connections_are_health_checked(monkeypatch):
    checks = []
    monkeypatch.setattr(
        ConnectionPool, "check_connection", staticmethod(lambda conn: checks.append(1))
    )
    with db.autocommit() as conn:
        db._last_used[conn] = time.monotonic() - 3600  # idle for an hour
        db._check(conn)
        db._last_used.pop(conn, None)  # never seen before: also checked
        db._check(conn)
    assert checks == [1, 1]


# ------------------------------------------------------------------------- token cache
@pytest.fixture
def cached_auth(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_CACHE_SECONDS", 10.0)
    auth._verify_cache.clear()
    yield
    auth._verify_cache.clear()


def test_verified_tokens_are_remembered_without_asking_the_database(cached_auth, monkeypatch):
    uid = auth.register("a@b.co", PW)
    token = auth.login("a@b.co", PW)
    assert auth.verify(token) == uid  # fills the cache
    counter = Counter(monkeypatch)
    for _ in range(5):
        assert auth.verify(token) == uid
    assert counter.total == 0


def test_changing_the_password_drops_cached_tokens_immediately(cached_auth):
    uid = auth.register("a@b.co", PW)
    old = auth.login("a@b.co", PW)
    auth.verify(old)
    new = auth.change_password(uid, PW, "a-brand-new-password")
    with pytest.raises(ValueError, match="no longer valid"):
        auth.verify(old)  # not served from the cache
    assert auth.verify(new) == uid


def test_admin_password_reset_drops_cached_tokens(cached_auth):
    auth.register("a@b.co", PW)
    token = auth.login("a@b.co", PW)
    auth.verify(token)
    auth.admin_set_password("a@b.co", "reset-by-the-operator")
    with pytest.raises(ValueError):
        auth.verify(token)


def test_other_processes_notice_a_disabled_account_after_the_cache_expires(cached_auth):
    """The documented trade-off: a change made elsewhere takes effect within AUTH_CACHE_SECONDS."""
    uid = auth.register("a@b.co", PW)
    token = auth.login("a@b.co", PW)
    auth.verify(token)
    with db.autocommit() as conn:
        conn.execute("UPDATE users SET is_active = false WHERE id = %s", (uid,))
    assert auth.verify(token) == uid  # still inside the cache window
    for key, (u, ver, _) in list(auth._verify_cache.items()):
        auth._verify_cache[key] = (u, ver, time.monotonic() - 1)  # window over
    with pytest.raises(ValueError):
        auth.verify(token)


def test_a_cached_entry_is_never_used_for_a_different_token_version(cached_auth):
    uid = auth.register("a@b.co", PW)
    token = auth.login("a@b.co", PW)
    auth.verify(token)
    key = next(iter(auth._verify_cache))
    auth._verify_cache[key] = (uid, 99, time.monotonic() + 100)  # a newer version was cached
    assert auth.verify(token) == uid  # version mismatch -> the database is asked, token is valid
    assert auth._verify_cache[key][1] == 0  # and the cache entry is corrected


def test_cache_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_CACHE_SECONDS", 0.0)
    auth._verify_cache.clear()
    auth.register("a@b.co", PW)
    token = auth.login("a@b.co", PW)
    auth.verify(token)
    assert auth._verify_cache == {}


# ------------------------------------------------------------------------- number of checkouts
def test_one_budget_charge_is_one_statement_when_there_is_no_global_limit(monkeypatch):
    counter = Counter(monkeypatch)
    assert Budget(per_user=5).try_spend("u")
    assert (counter.autocommit, counter.transaction) == (1, 0)


def test_retrieval_uses_a_small_constant_number_of_checkouts(monkeypatch, user_id):
    store = get_store(user_id)
    for i in range(8):
        put(
            store,
            f"doc{i}.txt",
            f"Leave policy number {i}. Employees receive {20 + i} days of annual leave.",
        )
    counter = Counter(monkeypatch)
    hits = rag.retrieve("How many days of annual leave?", "hybrid_rerank", user_id)
    assert len(hits) == 5 and all("context" in h for h in hits)
    # list_sources + dense + keyword + fetch + ONE batched context query (it used to be one per hit)
    assert counter.total <= 6, (counter.autocommit, counter.transaction)


def test_batched_context_matches_neighbours_for_every_hit(user_id):
    store = get_store(user_id)
    text = " ".join(f"word{i}" for i in range(150))
    put(store, "long.txt", text)
    chunks = store.chunks_for_source("long.txt")
    assert len(chunks) >= 4
    q = "word80 word81"
    ids = store.hybrid_search(q, 5)
    hits = store.rerank(q, ids, 3, neighbors=1)
    order = [c["id"] for c in chunks]
    for h in hits:
        pos = order.index(h["id"])
        expected = "\n".join(c["text"] for c in chunks[max(0, pos - 1) : pos + 2])
        assert h["context"] == expected


def test_context_of_a_hit_never_leaks_another_account(user_id):
    mine, other = get_store(user_id), get_store(user_id + "-other")
    put(mine, "same.txt", "annual leave is twenty four days in my company policy")
    put(other, "same.txt", "SECRET annual leave is thirty days at the other company")
    hits = mine.rerank("annual leave", mine.hybrid_search("annual leave", 5), 3, neighbors=2)
    assert hits and all("SECRET" not in h["context"] for h in hits)


def test_uploading_is_still_all_or_nothing_with_pipelined_statements(user_id):
    import psycopg

    store = get_store(user_id)
    put(store, "doc.txt", "Original text about annual leave days.")
    dup = [
        {"id": "doc.txt::p1::c0", "text": "a", "source": "doc.txt", "page": 1, "heading": ""},
        {"id": "doc.txt::p1::c0", "text": "b", "source": "doc.txt", "page": 1, "heading": ""},
    ]
    with pytest.raises(psycopg.errors.UniqueViolation):
        store.put_document("doc.txt", b"new", dup, 1)
    assert store.get_document("doc.txt") == b"x"
    assert [c["text"] for c in store.chunks_for_source("doc.txt")] == [
        "Original text about annual leave days."
    ]


# ------------------------------------------------------------------------- start-up warm-up
def test_warm_up_loads_both_models_and_never_raises(monkeypatch):
    from app import models

    seen = []

    class E:
        def encode(self, texts, normalize_embeddings=True):
            seen.append("embedder")

    class R:
        def predict(self, pairs):
            seen.append("reranker")

    monkeypatch.setattr(models, "get_embedder", lambda: E())
    monkeypatch.setattr(models, "get_reranker", lambda: R())
    models.warm_up()
    assert seen == ["embedder", "reranker"]

    def broken():
        raise RuntimeError("download failed")

    monkeypatch.setattr(models, "get_embedder", broken)
    models.warm_up()  # swallowed: the first real request would simply load the model itself


def test_the_api_warms_the_models_in_the_background_at_startup(monkeypatch):
    import threading

    from fastapi.testclient import TestClient

    from app import main

    started = threading.Event()
    monkeypatch.setattr(main, "WARM_MODELS", True)
    monkeypatch.setattr(main.models, "warm_up", started.set)
    with TestClient(main.app):
        assert started.wait(5)
    started.clear()
    monkeypatch.setattr(main, "WARM_MODELS", False)
    with TestClient(main.app):
        assert not started.wait(0.3)


# ------------------------------------------------------------------------- rate limiter
def test_rate_limiter_is_a_single_statement_per_attempt(monkeypatch):
    from app.limits import RateLimiter

    counter = Counter(monkeypatch)
    limiter = RateLimiter(3, 900)
    assert [limiter.allow("k") for _ in range(5)] == [True, True, True, False, False]
    assert (counter.autocommit, counter.transaction) == (5, 0)


def test_refused_attempts_do_not_extend_the_window():
    import datetime

    from app.limits import RateLimiter

    t0 = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    limiter = RateLimiter(1, 10)
    assert limiter.allow("k", now=t0)
    for s in (1, 3, 5, 9):  # hammering while locked out ...
        assert not limiter.allow("k", now=t0 + datetime.timedelta(seconds=s))
    assert limiter.allow("k", now=t0 + datetime.timedelta(seconds=11))  # ... still ends on schedule


def test_latency_tool_measures_and_explains():
    from scripts import db_latency

    ms = db_latency.measure(samples=5)
    assert 0 < ms < 1000
    assert "excellent" in db_latency.verdict(1) and "SLOW" in db_latency.verdict(400)
    assert db_latency.verdict(30) == "good" and "closer region" in db_latency.verdict(100)
