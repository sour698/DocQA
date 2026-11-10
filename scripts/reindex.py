"""Rebuild chunks and vectors from the original files stored in the database.

Needed after changing CHUNK_WORDS / CHUNK_OVERLAP, or the embedding model:

    python -m scripts.reindex --user-id <account-id>
    python -m scripts.reindex --all
    python -m scripts.reindex --all --reset-vectors     # new EMBED_MODEL / EMBED_DIM

``--reset-vectors`` drops every chunk, resizes the vector column for the CURRENT EMBED_DIM and
records the new model; the original documents are untouched and re-embedded straight away.
Stop (or pause) the API while doing this: searches see an empty index until it finishes.
"""

import argparse
import tempfile
from pathlib import Path

from app import db
from app.config import CHUNK_OVERLAP, CHUNK_WORDS
from app.ingest import chunk_pages, load_document
from app.store import get_store


def reindex_user(user_id: str, log=print) -> int:
    store, done = get_store(user_id), 0
    for name in store.list_sources():
        data = store.get_document(name)
        if data is None:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / name
            path.write_bytes(data)
            pages = load_document(path)
        if not pages:
            log(f"  SKIP (no readable text): {name}")
            continue
        store.put_document(
            name, data, chunk_pages(pages, name, CHUNK_WORDS, CHUNK_OVERLAP), len(pages)
        )
        done += 1
        log(f"  re-indexed {name}")
    return done


def reindex_all(log=print) -> int:
    with db.connection() as conn:
        users = [
            r["user_id"] for r in conn.execute("SELECT DISTINCT user_id FROM documents ORDER BY 1")
        ]
    total = 0
    for uid in users:
        log(f"account {uid}:")
        total += reindex_user(uid, log)
    return total


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--user-id")
    g.add_argument("--all", action="store_true")
    ap.add_argument("--reset-vectors", action="store_true", help="resize the vector column first")
    a = ap.parse_args()
    db.startup() if not a.reset_vectors else db.wait_ready()
    if a.reset_vectors:
        db.migrate()
        db.reset_vector_column()
        print("Vector column reset for the configured EMBED_MODEL / EMBED_DIM.")
        if a.user_id:
            raise SystemExit(
                "--reset-vectors empties the index for EVERY account, so use it with --all."
            )
    n = reindex_all() if a.all else reindex_user(a.user_id)
    print(f"{n} documents re-indexed.")
    db.close_pool()


if __name__ == "__main__":
    main()
