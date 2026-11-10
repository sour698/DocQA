"""Housekeeping: delete old log rows so the database does not grow forever.

    python -m scripts.maintenance                 # keep 90 days of logs
    python -m scripts.maintenance --keep-days 30

Run it daily (cron, Render cron job, GitHub Actions schedule...). Budgets older than 35 days
and rate-limit counters older than a day are always removed. Documents are never touched.
"""

import argparse

from app import db


def prune(keep_days: int = 90) -> dict:
    with db.connection() as conn:
        counts = {}
        for table, where, params in (
            ("query_log", "ts < now() - make_interval(days => %s)", (keep_days,)),
            ("audit_log", "ts < now() - make_interval(days => %s)", (keep_days,)),
            ("feedback", "ts < now() - make_interval(days => %s)", (keep_days * 2,)),
            ("rate_limits", "window_start < now() - interval '1 day'", ()),
            ("budget", "day < current_date - 35", ()),
        ):
            counts[table] = conn.execute(f"DELETE FROM {table} WHERE {where}", params).rowcount
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-days", type=int, default=90)
    a = ap.parse_args()
    db.wait_ready()
    for table, n in prune(a.keep_days).items():
        print(f"{table}: {n} rows deleted")
    db.close_pool()


if __name__ == "__main__":
    main()
