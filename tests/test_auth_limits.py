import datetime

import pytest

from app import analytics, auth, db
from app.limits import Budget, RateLimiter

PW = "a-very-long-test-password"


pytestmark = pytest.mark.usefixtures("pg")


def test_account_password_and_token_roundtrip():
    uid = auth.register("Test@Example.com", PW)
    token = auth.login("test@example.com", PW)
    assert auth.verify(token) == uid == auth.user_id_for("TEST@example.com")
    with pytest.raises(ValueError):
        auth.login("test@example.com", "wrong-password")


def test_duplicate_and_invalid_registrations():
    auth.register("a@b.co", PW)
    with pytest.raises(ValueError, match="already exists"):
        auth.register("A@B.co", PW)
    with pytest.raises(ValueError, match="valid email"):
        auth.register("not-an-email", PW)
    with pytest.raises(ValueError, match="at least"):
        auth.register("c@d.co", "short")
    with pytest.raises(ValueError, match="at most"):
        auth.register("c@d.co", "x" * 300)
    with pytest.raises(ValueError, match="same as the email"):
        auth.register("longer.address@domain.com", "longer.address@domain.com")


def test_unknown_email_gets_same_error_as_wrong_password():
    auth.register("a@b.co", PW)
    for email in ("a@b.co", "nobody@b.co"):
        with pytest.raises(ValueError, match="Incorrect email or password"):
            auth.login(email, "wrong-password-123")


def test_tampered_or_garbage_tokens_are_rejected():
    uid = auth.register("a@b.co", PW)
    token = auth.token_for(uid)
    payload, sig = token.split(".")
    for bad in ("", "abc", token + "x", payload + ".AAAA", "x." + sig, payload[::-1] + "." + sig):
        with pytest.raises(ValueError):
            auth.verify(bad)


def test_expired_token_rejected(monkeypatch):
    uid = auth.register("a@b.co", PW)
    monkeypatch.setattr(auth, "TOKEN_TTL", -10)
    with pytest.raises(ValueError, match="expired"):
        auth.verify(auth.token_for(uid))


def test_token_for_deleted_user_is_rejected():
    uid = auth.register("a@b.co", PW)
    token = auth.token_for(uid)
    with db.connection() as conn:
        conn.execute("DELETE FROM users")
    with pytest.raises(ValueError):
        auth.verify(token)


def test_change_password_invalidates_old_tokens():
    uid = auth.register("a@b.co", PW)
    old = auth.login("a@b.co", PW)
    new = auth.change_password(uid, PW, "a-brand-new-password")
    assert auth.verify(new) == uid
    with pytest.raises(ValueError, match="no longer valid"):
        auth.verify(old)
    with pytest.raises(ValueError):
        auth.login("a@b.co", PW)
    assert auth.verify(auth.login("a@b.co", "a-brand-new-password")) == uid
    with pytest.raises(ValueError, match="Current password"):
        auth.change_password(uid, "wrong-old-password", "another-new-password")


def test_admin_reset_password():
    uid = auth.register("a@b.co", PW)
    token = auth.login("a@b.co", PW)
    auth.admin_set_password("A@B.co", "reset-by-the-operator")
    assert auth.verify(auth.login("a@b.co", "reset-by-the-operator")) == uid
    with pytest.raises(ValueError):
        auth.verify(token)
    with pytest.raises(ValueError, match="No account"):
        auth.admin_set_password("ghost@b.co", "reset-by-the-operator")


@pytest.mark.parametrize(
    "secret,ok",
    [
        ("", False),
        ("short", False),
        ("replace_with_a_long_random_secret_xxxxxxxx", False),
        ("changeme" + "x" * 30, False),
        ("a-really-long-and-random-secret-0123456789", True),
    ],
)
def test_weak_secrets_are_flagged(secret, ok):
    assert (auth.secret_problem(secret) is None) is ok


def test_no_tokens_without_a_usable_secret(monkeypatch):
    uid = auth.register("a@b.co", PW)
    monkeypatch.setattr(auth, "SECRET", "replace_with_a_long_random_secret")
    with pytest.raises(RuntimeError, match="placeholder"):
        auth.token_for(uid)
    with pytest.raises(ValueError):
        auth.verify("a.b")


def test_disabled_account_cannot_sign_in_or_use_old_tokens():
    uid = auth.register("a@b.co", PW)
    token = auth.login("a@b.co", PW)
    with db.connection() as conn:
        conn.execute("UPDATE users SET is_active = false WHERE id = %s", (uid,))
    with pytest.raises(ValueError, match="Incorrect email or password"):
        auth.login("a@b.co", PW)
    with pytest.raises(ValueError):
        auth.verify(token)


def test_login_records_last_sign_in():
    uid = auth.register("a@b.co", PW)
    auth.login("a@b.co", PW)
    with db.connection() as conn:
        row = conn.execute("SELECT last_login_at FROM users WHERE id = %s", (uid,)).fetchone()
    assert row["last_login_at"] is not None


# ------------------------------------------------------------------ limits
def test_budget_is_per_user_and_resets_daily():
    b = Budget(per_user=2)
    day = datetime.date(2026, 1, 1)
    assert b.try_spend("alice", day) and b.try_spend("alice", day)
    assert not b.try_spend("alice", day)
    assert b.try_spend("bob", day)  # one heavy user does not lock out others
    assert b.try_spend("alice", datetime.date(2026, 1, 2))


def test_budget_persists_across_instances():
    day = datetime.date(2026, 1, 1)
    assert Budget(1).try_spend("u", day)
    assert not Budget(1).try_spend("u", day)  # a "restart" does not reset the count


def test_global_cap_and_no_double_counting():
    b = Budget(per_user=5, global_limit=2)
    day = datetime.date(2026, 1, 1)
    assert b.try_spend("a", day) and b.try_spend("b", day)
    assert not b.try_spend("c", day)  # whole-app cap reached
    with db.connection() as conn:
        used = {r["user_key"]: r["used"] for r in conn.execute("SELECT user_key, used FROM budget")}
    assert used == {"*": 2, "a": 1, "b": 1}  # the refused question counted nowhere


def test_budget_zero_means_unlimited():
    b = Budget(0)
    assert all(b.try_spend("u") for _ in range(100))


def test_rate_limiter_window():
    rl = RateLimiter(2, 10)
    t0 = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    at = lambda sec: t0 + datetime.timedelta(seconds=sec)  # noqa: E731
    assert rl.allow("k", now=at(0)) and rl.allow("k", now=at(1))
    assert not rl.allow("k", now=at(2))
    assert rl.allow("other", now=at(2))
    assert rl.allow("k", now=at(12))  # the window slid past the old attempts
    rl.reset("k")
    assert rl.allow("k", now=at(12.5)) and rl.allow("k", now=at(12.6))


# ------------------------------------------------------------------ analytics
def test_audit_log_records_events_without_secrets():
    analytics.audit("login_failed", None, "203.0.113.9", f"email#{analytics.hash_email('A@B.co')}")
    analytics.audit("upload", "u1", "203.0.113.9", "leave.pdf 1234B")
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT event, user_id, ip, detail FROM audit_log ORDER BY id"
        ).fetchall()
    assert [r["event"] for r in rows] == ["login_failed", "upload"]
    assert (
        rows[0]["detail"] == f"email#{analytics.hash_email('a@b.co')}"
        and "@" not in rows[0]["detail"]
    )


def test_stats_and_feedback():
    analytics.log_query("u1", "q1?", route="SEARCH", refused=False, top_score=4.0, latency_ms=100)
    analytics.log_query(
        "u1", "unanswered?", route="SEARCH", refused=True, top_score=-5.0, latency_ms=300
    )
    analytics.log_query("u2", "someone else", refused=True)
    analytics.log_feedback("u1", "q1?", "a", 1)
    analytics.log_feedback("u1", "q2?", "b", -1, "wrong page")
    s = analytics.stats("u1")
    assert s["questions"] == 2 and s["refused"] == 1 and s["refusal_rate"] == 0.5
    assert s["avg_latency_ms"] == 200 and s["recent_unanswered"] == ["unanswered?"]
    assert (s["feedback_up"], s["feedback_down"]) == (1, 1)
    assert analytics.stats("nobody")["questions"] == 0


def test_question_text_not_stored_when_logging_disabled(monkeypatch):
    monkeypatch.setattr(analytics, "LOG_QUERIES", False)
    analytics.log_query("u1", "private question", refused=True)
    assert analytics.stats("u1")["recent_unanswered"] == []
    assert analytics.stats("u1")["questions"] == 1
