"""Query log, answer feedback, audit trail and per-account usage statistics (PostgreSQL)."""

import hashlib
import logging

from . import db
from .config import LOG_QUERIES

log = logging.getLogger(__name__)


def hash_email(email: str) -> str:
    """Short, non-reversible tag for an email: lets the audit log correlate failed sign-ins
    without storing the address itself."""
    return hashlib.sha256((email or "").strip().lower().encode()).hexdigest()[:12]


def log_query(
    user_id,
    question,
    route=None,
    refused=False,
    from_documents=None,
    top_score=None,
    latency_ms=None,
) -> None:
    """Best effort: analytics must never break answering. Question text is stored only when
    LOG_QUERIES is on."""
    try:
        with db.autocommit() as conn:
            conn.execute(
                "INSERT INTO query_log (user_id, question, route, refused, from_documents, "
                "top_score, latency_ms) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    user_id,
                    question if LOG_QUERIES else "",
                    route,
                    bool(refused),
                    None if from_documents is None else bool(from_documents),
                    top_score,
                    latency_ms,
                ),
            )
    except Exception:  # noqa: BLE001
        log.warning("Could not write the query log", exc_info=True)


def audit(event: str, user_id: str | None = None, ip: str | None = None, detail: str = "") -> None:
    """Security-relevant events (sign-in, failures, password change, upload, delete...).
    Best effort, and never contains passwords or e-mail addresses."""
    try:
        with db.autocommit() as conn:
            conn.execute(
                "INSERT INTO audit_log (user_id, event, ip, detail) VALUES (%s, %s, %s, %s)",
                (user_id, event, ip, detail[:500]),
            )
    except Exception:  # noqa: BLE001
        log.warning("Could not write the audit log", exc_info=True)


def log_feedback(user_id, question, answer, rating: int, comment: str = "") -> None:
    with db.autocommit() as conn:
        conn.execute(
            "INSERT INTO feedback (user_id, question, answer, rating, comment) "
            "VALUES (%s, %s, %s, %s, %s)",
            (user_id, question[:4000], (answer or "")[:8000], rating, comment[:1000]),
        )


def stats(user_id: str, days: int = 30, recent: int = 10) -> dict:
    with db.autocommit() as conn:
        q = conn.execute(
            "SELECT count(*) AS total, count(*) FILTER (WHERE refused) AS refused, "
            "avg(latency_ms) AS latency, avg(top_score) AS score FROM query_log "
            "WHERE user_id = %s AND ts >= now() - make_interval(days => %s)",
            (user_id, days),
        ).fetchone()
        unanswered = [
            r["question"]
            for r in conn.execute(
                "SELECT question FROM query_log WHERE user_id = %s AND refused "
                "AND question <> '' AND ts >= now() - make_interval(days => %s) "
                "ORDER BY ts DESC LIMIT %s",
                (user_id, days, recent),
            )
        ]
        f = conn.execute(
            "SELECT count(*) FILTER (WHERE rating > 0) AS up, "
            "count(*) FILTER (WHERE rating < 0) AS down FROM feedback "
            "WHERE user_id = %s AND ts >= now() - make_interval(days => %s)",
            (user_id, days),
        ).fetchone()
    total = q["total"]
    return {
        "days": days,
        "questions": total,
        "refused": q["refused"],
        "refusal_rate": round(q["refused"] / total, 3) if total else None,
        "avg_latency_ms": round(float(q["latency"])) if q["latency"] is not None else None,
        "avg_top_score": round(float(q["score"]), 2) if q["score"] is not None else None,
        "recent_unanswered": unanswered,
        "feedback_up": f["up"],
        "feedback_down": f["down"],
    }
