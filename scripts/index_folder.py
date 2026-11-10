"""Index a directory of documents (PDF, DOCX, TXT, MD, HTML) into one account.

Usage: python -m scripts.index_folder ./docs --user-id <account-id>
"""

import argparse
from pathlib import Path

from app import db
from app.config import CHUNK_OVERLAP, CHUNK_WORDS
from app.ingest import chunk_pages, load_document
from app.store import get_store
from app.utils import ALLOWED_EXTENSIONS


def main():
    p = argparse.ArgumentParser()
    p.add_argument("folder", type=Path)
    p.add_argument("--user-id", required=True)
    a = p.parse_args()
    if not a.folder.is_dir():
        raise SystemExit(f"Folder not found: {a.folder}")
    db.startup()
    store, count = get_store(a.user_id), 0
    for path in sorted(a.folder.iterdir()):
        if path.suffix.lower() not in ALLOWED_EXTENSIONS or path.name.startswith("."):
            continue
        pages = load_document(path)
        if not pages:
            print(f"SKIP: no readable text: {path.name}")
            continue
        chunks = chunk_pages(pages, path.name, CHUNK_WORDS, CHUNK_OVERLAP)
        store.put_document(path.name, path.read_bytes(), chunks, len(pages))
        count += 1
        print(f"Indexed {path.name}: {len(pages)} pages")
    print(f"Finished: {count} documents indexed in the selected account.")
    db.close_pool()


if __name__ == "__main__":
    main()
