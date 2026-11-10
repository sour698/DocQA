"""Usage limits, kept in PostgreSQL so every app process and every restart sees the same counts.

* ``Budget``      - questions per user per UTC day (plus an optional whole-app cap).
* ``RateLimiter`` - sliding window, used against password guessing.
"""

import datetime

from . import db

GLOBAL_KEY = "*"


class Budget:
    def __init__(self, per_user: int, global_limit: int = 0):
        self.per_user = per_user
        self.global_limit = global_limit

    def try_spend(self, user_key: str, today: datetime.date | None = None) -> bool:
        """Atomically count one question for ``user_key``; False when a limit is reached.

        Both counters are updated in one transaction: a refused question is counted nowhere.
        The single conditional upsert per counter is race-free across processes.
        """
        if self.per_user <= 0 and self.global_limit <= 0:
            return True
        day = today or datetime.datetime.now(datetime.UTC).date()
        upsert = (
            "INSERT INTO budget (user_key, day, used) VALUES (%s, %s, 1) "
            "ON CONFLICT (user_key, day) DO UPDATE SET used = budget.used + 1 "
            "WHERE budget.used < %s RETURNING used"
        )
        if self.global_limit <= 0:  # the usual case: ONE atomic statement, one round trip
            with db.autocommit() as conn:
                return conn.execute(upsert, (user_key, day, self.per_user)).fetchone() is not None
        with db.connection() as conn:
            for key, limit in ((GLOBAL_KEY, self.global_limit), (user_key, self.per_user)):
                if limit <= 0:
                    continue
                row = conn.execute(upsert, (key, day, limit)).fetchone()
                if row is None:
                    conn.rollback()
                    return False
        return True


class RateLimiter:
    """Allow ``max_events`` per ``window`` seconds per key (e.g. an email or a client address).

    A fixed window kept in ONE row per key and updated with a single atomic statement: exact under
    concurrency (the row lock serialises parallel attempts) and one network round trip. Attempts that
    are refused still count, and never extend the window.
    """

    def __init__(self, max_events: int, window: float):
        self.max_events, self.window = max_events, window

    def allow(self, key: str, now: datetime.datetime | None = None) -> bool:
        now = now or datetime.datetime.now(datetime.UTC)
        cutoff = now - datetime.timedelta(seconds=self.window)
        with db.autocommit() as conn:
            row = conn.execute(
                "INSERT INTO rate_limits AS r (key, window_start, hits) VALUES (%s, %s, 1) "
                "ON CONFLICT (key) DO UPDATE SET "
                "  hits = CASE WHEN r.window_start <= %s THEN 1 ELSE r.hits + 1 END, "
                "  window_start = CASE WHEN r.window_start <= %s THEN EXCLUDED.window_start "
                "                      ELSE r.window_start END "
                "RETURNING hits",
                (key, now, cutoff, cutoff),
            ).fetchone()
        return row["hits"] <= self.max_events

    def reset(self, key: str) -> None:
        with db.autocommit() as conn:
            conn.execute("DELETE FROM rate_limits WHERE key = %s", (key,))
