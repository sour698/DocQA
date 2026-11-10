"""One-off: move an older SQLite + Chroma + uploads installation into PostgreSQL.

    python -m scripts.migrate_from_sqlite --sqlite data/accounts.sqlite3 --uploads data/uploads

What moves:
  * accounts (password hashes included, so nobody has to reset anything),
  * budgets, question log and feedback (when the old database has them),
  * documents: each account's original files are re-read from ``--uploads`` and re-indexed
    (re-embedded) into PostgreSQL. The old Chroma data is NOT copied: it is rebuilt from the
    files, which also gives you the new chunking.

Safe to re-run: existing accounts are skipped and documents are replaced.
Run it with the API stopped, after `python -m scripts.migrate`.
"""

import argparse
import sqlite3
from pathlib import Path

from app import db
from app.config import CHUNK_OVERLAP, CHUNK_WORDS
from app.ingest import chunk_pages, load_document
from app.store import get_store
from app.utils import ALLOWED_EXTENSIONS, user_key


def _rows(src: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    exists = src.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return src.execute(f"SELECT * FROM {table}").fetchall() if exists else []


def migrate(sqlite_path, uploads_dir=None, log=print) -> dict:
    src = sqlite3.connect(str(sqlite_path))
    src.row_factory = sqlite3.Row
    report = {"users": 0, "budget": 0, "query_log": 0, "feedback": 0, "documents": 0, "skipped": 0}

    users = _rows(src, "users")
    with db.connection() as conn:
        for u in users:
            cols = u.keys()
            cur = conn.execute(
                "INSERT INTO users (id, email, salt, password_hash, token_ver, created_at) "
                "VALUES (%s, %s, %s, %s, %s, to_timestamp(%s)) ON CONFLICT (id) DO NOTHING",
                (
                    u["id"],
                    u["email"],
                    bytes(u["salt"]),
                    bytes(u["password_hash"]),
                    u["token_ver"] if "token_ver" in cols else 0,
                    u["created"],
                ),
            )
            report["users"] += cur.rowcount
        for b in _rows(src, "budget"):
            report["budget"] += conn.execute(
                "INSERT INTO budget (user_key, day, used) VALUES (%s, %s::date, %s) "
                "ON CONFLICT (user_key, day) DO NOTHING",
                (b["user_id"], b["day"], b["used"]),
            ).rowcount
        for q in _rows(src, "query_log"):
            conn.execute(
                "INSERT INTO query_log (ts, user_id, question, route, refused, from_documents, "
                "top_score, latency_ms) VALUES (to_timestamp(%s), %s, %s, %s, %s, %s, %s, %s)",
                (
                    q["ts"],
                    q["user_id"],
                    q["question"],
                    q["route"],
                    None if q["refused"] is None else bool(q["refused"]),
                    None if q["from_documents"] is None else bool(q["from_documents"]),
                    q["top_score"],
                    q["latency_ms"],
                ),
            )
            report["query_log"] += 1
        for f in _rows(src, "feedback"):
            conn.execute(
                "INSERT INTO feedback (ts, user_id, question, answer, rating, comment) "
                "VALUES (to_timestamp(%s), %s, %s, %s, %s, %s)",
                (f["ts"], f["user_id"], f["question"], f["answer"], f["rating"], f["comment"]),
            )
            report["feedback"] += 1
    log(f"accounts: {report['users']} new ({len(users)} in the old database)")

    if uploads_dir:
        uploads = Path(uploads_dir)
        # The old layout used a one-way hash of the account id as the folder name.
        for u in users:
            folder = uploads / user_key(u["id"])
            if not folder.is_dir():
                continue
            store = get_store(u["id"])
            for path in sorted(folder.iterdir()):
                if path.suffix.lower() not in ALLOWED_EXTENSIONS or path.name.startswith("."):
                    continue
                try:
                    pages = load_document(path)
                except Exception as e:  # noqa: BLE001
                    log(f"  SKIP unreadable {path.name}: {e}")
                    report["skipped"] += 1
                    continue
                if not pages:
                    log(f"  SKIP (no readable text): {path.name}")
                    report["skipped"] += 1
                    continue
                chunks = chunk_pages(pages, path.name, CHUNK_WORDS, CHUNK_OVERLAP)
                store.put_document(path.name, path.read_bytes(), chunks, len(pages))
                report["documents"] += 1
                log(f"  moved {path.name} for account {u['id'][:8]}...")
    src.close()
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", required=True, type=Path, help="old accounts.sqlite3")
    ap.add_argument("--uploads", type=Path, help="old data/uploads folder (to move the documents)")
    a = ap.parse_args()
    if not a.sqlite.is_file():
        raise SystemExit(f"Not found: {a.sqlite}")
    db.startup()
    print(migrate(a.sqlite, a.uploads))
    db.close_pool()


if __name__ == "__main__":
    main()
