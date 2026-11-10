"""Operator tool: permanently delete an account and ALL of its data (right-to-erasure requests).

    python -m scripts.delete_account someone@example.com

Removes the account, its documents and chunks, question log, feedback and budget counters.
Audit-log rows are kept (they hold no e-mail address) but are detached from the account id.
"""

import sys

from app import analytics, auth, db


def delete_account(email: str) -> dict:
    uid = auth.user_id_for(email)
    with db.connection() as conn:
        if not conn.execute("SELECT 1 FROM users WHERE id = %s", (uid,)).fetchone():
            raise ValueError("No account with that email.")
        counts = {
            "documents": conn.execute("DELETE FROM documents WHERE user_id = %s", (uid,)).rowcount,
            "query_log": conn.execute("DELETE FROM query_log WHERE user_id = %s", (uid,)).rowcount,
            "feedback": conn.execute("DELETE FROM feedback WHERE user_id = %s", (uid,)).rowcount,
            "budget": conn.execute("DELETE FROM budget WHERE user_key = %s", (uid,)).rowcount,
            "audit_log": conn.execute(
                "UPDATE audit_log SET user_id = NULL WHERE user_id = %s", (uid,)
            ).rowcount,
        }
        conn.execute("DELETE FROM users WHERE id = %s", (uid,))
    analytics.audit("account_deleted", None, None, f"id#{uid[:6]}")
    return counts


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m scripts.delete_account <email>")
    if (
        input(f"Permanently delete {sys.argv[1]} and all data? Type 'delete' to confirm: ")
        != "delete"
    ):
        raise SystemExit("Cancelled.")
    db.wait_ready()
    try:
        counts = delete_account(sys.argv[1])
    except ValueError as e:
        raise SystemExit(str(e)) from None
    print("Deleted:", ", ".join(f"{k}={v}" for k, v in counts.items()))
    db.close_pool()


if __name__ == "__main__":
    main()
