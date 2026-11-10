"""Things that only break under parallel load: counters, limits, simultaneous writers."""

import datetime
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import db
from app.ingest import chunk_pages
from app.limits import Budget, RateLimiter
from app.store import get_store

pytestmark = pytest.mark.usefixtures("pg")


def run_parallel(fn, n, workers=16):
    with ThreadPoolExecutor(workers) as pool:
        return list(pool.map(fn, range(n)))


def test_budget_never_overspends_under_parallel_requests():
    budget = Budget(per_user=5)
    day = datetime.date(2026, 3, 1)
    results = run_parallel(lambda _: budget.try_spend("u", day), 40)
    assert sum(results) == 5
    with db.connection() as conn:
        assert conn.execute("SELECT used FROM budget WHERE user_key='u'").fetchone()["used"] == 5


def test_global_budget_is_exact_across_many_users():
    budget = Budget(per_user=100, global_limit=7)
    day = datetime.date(2026, 3, 1)
    results = run_parallel(lambda i: budget.try_spend(f"user{i % 5}", day), 50)
    assert sum(results) == 7
    with db.connection() as conn:
        rows = {r["user_key"]: r["used"] for r in conn.execute("SELECT user_key, used FROM budget")}
    assert rows["*"] == 7 and sum(v for k, v in rows.items() if k != "*") == 7  # no double counting


def test_rate_limiter_is_exact_under_parallel_attempts():
    limiter = RateLimiter(3, 900)
    assert sum(run_parallel(lambda _: limiter.allow("attacker"), 30)) == 3


def test_simultaneous_uploads_of_one_name_leave_one_consistent_version(user_id):
    store = get_store(user_id)

    def upload(i):
        text = " ".join(f"version{i}" for _ in range(80))  # several chunks, all tagged with i
        chunks = chunk_pages([(1, text)], "same.txt", 30, 5)
        store.put_document("same.txt", f"v{i}".encode(), chunks, 1)
        return i

    run_parallel(upload, 8, workers=8)
    chunks = store.chunks_for_source("same.txt")
    tags = {w for c in chunks for w in c["text"].split()}
    assert len(tags) == 1  # chunks from ONE writer only: never a mixture of versions
    winner = tags.pop().removeprefix("version")
    assert store.get_document("same.txt") == f"v{winner}".encode()
    assert len({c["id"] for c in chunks}) == len(chunks)


def test_parallel_reads_and_writes_do_not_break_search(user_id):
    store = get_store(user_id)
    store.put_document(
        "base.txt",
        b"x",
        chunk_pages([(1, "Annual leave is 24 days per year.")], "base.txt", 30, 5),
        1,
    )

    def work(i):
        if i % 4 == 0:
            store.put_document(
                f"d{i}.txt",
                b"x",
                chunk_pages([(1, f"Extra document number {i} text")], f"d{i}.txt", 30, 5),
                1,
            )
        return store.hybrid_search("annual leave days", 5)

    results = run_parallel(work, 24, workers=12)
    assert all(r for r in results)
