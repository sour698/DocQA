"""Measure how far away your database is, and what that means for the app's speed.

    python -m scripts.db_latency

Prints the median network round trip to DATABASE_URL. Every database call in the app costs at least
one round trip, so a database in another country is the usual reason for a slow app. Choose the
provider region closest to where the app (and your users) run.
"""

import statistics
import time

from app import db


def measure(samples: int = 15) -> float:
    """Median round-trip time in milliseconds of a trivial query on an already open connection."""
    with db.autocommit() as conn:
        conn.execute("SELECT 1")  # warm up: connection + TLS already established
        times = []
        for _ in range(samples):
            started = time.perf_counter()
            conn.execute("SELECT 1").fetchone()
            times.append((time.perf_counter() - started) * 1000)
    return statistics.median(times)


def verdict(ms: float) -> str:
    if ms < 15:
        return "excellent (database on the same machine or network)"
    if ms < 60:
        return "good"
    if ms < 150:
        return "acceptable, but a closer region would feel noticeably quicker"
    return "SLOW: the database is far away. Create it in the region nearest to where the app runs."


def main():
    db.wait_ready()
    ms = measure()
    print(f"Median round trip to the database: {ms:.0f} ms - {verdict(ms)}")
    print(
        f"Rough cost of common actions: opening the dashboard ~{2 * ms / 1000:.1f} s, "
        f"asking a question ~{(6 * ms) / 1000 + 1:.1f} s of database time, signing in ~{7 * ms / 1000:.1f} s."
    )
    db.close_pool()


if __name__ == "__main__":
    main()
