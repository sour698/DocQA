"""Per-account document store on PostgreSQL + pgvector.

* Original files and their chunks are written in ONE transaction (``put_document``); deleting a
  document cascades to its chunks. There is no separate index to rebuild or to go stale, and any
  number of app processes can serve the same data.
* Dense search: pgvector cosine distance (HNSW index). Keyword search: Postgres full-text search.
  Both are fused with reciprocal-rank fusion in ``hybrid_search``, then reranked by the
  cross-encoder.
* Every query is scoped by ``user_id``: accounts cannot see each other's data.
"""

import hashlib
import re

import numpy as np

from . import db, models
from .config import EMBED_DIM, EXACT_SEARCH_MAX_CHUNKS, FTS_LANGUAGE, QUERY_PREFIX
from .utils import contextual_text, rrf, tokenize

_TOKEN_OK = re.compile(r"[a-z0-9]+")


def _vec(v) -> str:
    """pgvector text literal for one embedding."""
    arr = np.asarray(v, dtype="float32")
    if arr.shape[-1] != EMBED_DIM:
        raise RuntimeError(
            f"The embedding model produced {arr.shape[-1]}-dimensional vectors but EMBED_DIM="
            f"{EMBED_DIM}. Set EMBED_DIM to the model's size and run: "
            "python -m scripts.reindex --all --reset-vectors"
        )
    return "[" + ",".join(f"{x:.7g}" for x in arr) + "]"


def _tsquery(question: str) -> str | None:
    """OR-query from the question's words (only [a-z0-9] tokens, so it is injection-safe)."""
    words = [w for t in tokenize(question) for w in _TOKEN_OK.findall(t.lower())]
    return " | ".join(dict.fromkeys(words)) or None


class Store:
    def __init__(self, user_id: str = "default"):
        self.user_id = str(user_id)

    # ---------- writing ----------
    def put_document(self, name: str, data: bytes, chunks: list[dict], pages: int = 0) -> None:
        """Create or replace a document and its chunks atomically.

        Embedding (the slow part) happens before the transaction opens, so no database
        connection is held while the model runs.
        """
        embedder = models.get_embedder()
        vectors: list[str] = []
        for i in range(0, len(chunks), 64):
            batch = chunks[i : i + 64]
            embs = embedder.encode([contextual_text(c) for c in batch], normalize_embeddings=True)
            vectors += [_vec(e) for e in embs]
        rows = [
            (
                self.user_id,
                c["id"],
                name,
                int(c["page"]),
                int(c["id"].rsplit("::c", 1)[1]),
                c.get("heading", ""),
                c["text"],
                vec,
            )
            for c, vec in zip(chunks, vectors, strict=True)
        ]
        # One transaction, and the statements are PIPELINED: sent together instead of one network
        # round trip each (matters a lot when the database is not on the same machine).
        with db.connection() as conn, conn.pipeline():
            conn.execute(
                "INSERT INTO documents (user_id, name, content, size_bytes, sha256, pages) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (user_id, name) DO UPDATE SET content = EXCLUDED.content, "
                "size_bytes = EXCLUDED.size_bytes, sha256 = EXCLUDED.sha256, "
                "pages = EXCLUDED.pages, created_at = now()",
                (self.user_id, name, data, len(data), hashlib.sha256(data).hexdigest(), pages),
            )
            conn.execute(
                "DELETE FROM chunks WHERE user_id = %s AND source = %s", (self.user_id, name)
            )
            if rows:
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO chunks (user_id, id, source, page, idx, heading, text, embedding) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)",
                        rows,
                    )

    def delete_source(self, name: str) -> bool:
        """Delete a document (its chunks go with it). Returns False if it did not exist."""
        with db.autocommit() as conn:
            cur = conn.execute(
                "DELETE FROM documents WHERE user_id = %s AND name = %s", (self.user_id, name)
            )
            return cur.rowcount > 0

    # ---------- reading documents ----------
    def list_sources(self) -> list[str]:
        with db.autocommit() as conn:
            rows = conn.execute(
                "SELECT name FROM documents WHERE user_id = %s ORDER BY name", (self.user_id,)
            ).fetchall()
        return [r["name"] for r in rows]

    def get_document(self, name: str) -> bytes | None:
        with db.autocommit() as conn:
            row = conn.execute(
                "SELECT content FROM documents WHERE user_id = %s AND name = %s",
                (self.user_id, name),
            ).fetchone()
        return bytes(row["content"]) if row else None

    def count_chunks(self) -> int:
        with db.autocommit() as conn:
            return conn.execute(
                "SELECT count(*) AS n FROM chunks WHERE user_id = %s", (self.user_id,)
            ).fetchone()["n"]

    # ---------- retrieval ----------
    def dense_search(self, query: str, n: int, sources: list[str] | None = None) -> list[str]:
        """Nearest chunks by cosine distance, within this account only.

        A shared HNSW index with a per-account filter is NOT safe on its own: the index returns its
        nearest candidates across ALL accounts and the filter then discards most of them, so an
        account can get far fewer than ``n`` results (measured: 0 of 60 queries returned 20 rows
        with 20 accounts). So: accounts up to ``EXACT_SEARCH_MAX_CHUNKS`` chunks are searched
        exactly (no approximation at all); bigger ones use the index, and if it comes back short
        the exact search runs anyway.
        """
        qvec = _vec(
            models.get_embedder().encode([QUERY_PREFIX + query], normalize_embeddings=True)[0]
        )
        scope, params = "user_id = %s", [self.user_id]
        if sources:
            scope += " AND source = ANY(%s)"
            params.append(list(sources))
        # `+ 0` stops Postgres from choosing the HNSW index: it scans this account's rows and sorts
        exact_sql = (
            f"SELECT id FROM chunks WHERE {scope} ORDER BY (embedding <=> %s::vector) + 0 LIMIT %s"
        )
        ann_sql = f"SELECT id FROM chunks WHERE {scope} ORDER BY embedding <=> %s::vector LIMIT %s"
        with db.autocommit() as conn:
            size = conn.execute(
                f"SELECT count(*) AS n FROM chunks WHERE {scope}", params
            ).fetchone()["n"]
            if size == 0:
                return []
            if size <= EXACT_SEARCH_MAX_CHUNKS:
                rows = conn.execute(exact_sql, [*params, qvec, n]).fetchall()
                return [r["id"] for r in rows]
        # Large account: use the approximate index (SET LOCAL needs a transaction).
        with db.connection() as conn:
            conn.execute(f"SET LOCAL hnsw.ef_search = {min(1000, max(100, 8 * int(n)))}")
            if db.pgvector_version() >= (0, 8, 0):
                conn.execute("SET LOCAL hnsw.iterative_scan = 'strict_order'")
            rows = conn.execute(ann_sql, [*params, qvec, n]).fetchall()
            if len(rows) < min(n, size):  # the filtered index scan under-delivered: do it exactly
                rows = conn.execute(exact_sql, [*params, qvec, n]).fetchall()
        return [r["id"] for r in rows]

    def bm25_search(self, query: str, n: int, sources: list[str] | None = None) -> list[str]:
        """Keyword search (Postgres full-text ranking; the name is kept for the eval scripts)."""
        tsq = _tsquery(query)
        if tsq is None:
            return []
        sql = (
            "SELECT id FROM chunks, to_tsquery(%s::regconfig, %s) AS q "
            "WHERE user_id = %s AND tsv @@ q"
        )
        params: list = [FTS_LANGUAGE, tsq, self.user_id]
        if sources:
            sql += " AND source = ANY(%s)"
            params.append(list(sources))
        sql += " ORDER BY ts_rank_cd(tsv, q) DESC, id LIMIT %s"
        params.append(n)
        with db.autocommit() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [r["id"] for r in rows]

    def hybrid_search(self, query: str, n: int, sources: list[str] | None = None) -> list[str]:
        fused = rrf([self.dense_search(query, n, sources), self.bm25_search(query, n, sources)])
        return fused[:n]

    def _fetch(self, ids: list[str]) -> dict[str, dict]:
        if not ids:
            return {}
        with db.autocommit() as conn:
            rows = conn.execute(
                "SELECT id, text, source, page, heading FROM chunks "
                "WHERE user_id = %s AND id = ANY(%s)",
                (self.user_id, list(ids)),
            ).fetchall()
        return {r["id"]: dict(r) for r in rows}

    def get(self, ids: list[str]) -> list[dict]:
        found = self._fetch(ids)
        return [{**found[i], "score": None} for i in ids if i in found]

    def rerank(self, query: str, ids: list[str], top_k: int, neighbors: int = 0) -> list[dict]:
        """Cross-encoder rerank. With ``neighbors > 0`` every hit also carries a ``context``
        field: its own text plus the neighbouring chunks, for answers that span chunk borders."""
        found = self._fetch(ids)
        ids = [i for i in ids if i in found]
        if not ids:
            return []
        scores = models.get_reranker().predict([(query, contextual_text(found[i])) for i in ids])
        ranked = sorted(zip(ids, scores, strict=True), key=lambda x: x[1], reverse=True)[:top_k]
        hits = [{**found[i], "score": float(s)} for i, s in ranked]
        if neighbors > 0 and hits:
            contexts = self._contexts(hits, neighbors)
            for h in hits:
                h["context"] = contexts.get(h["id"], h["text"])
        return hits

    def _contexts(self, hits: list[dict], k: int) -> dict[str, str]:
        """Each hit's text plus its ``k`` neighbours on both sides, for ALL hits in ONE query."""
        with db.autocommit() as conn:
            rows = conn.execute(
                "WITH ordered AS ("
                "  SELECT id, source, text, row_number() OVER (PARTITION BY source ORDER BY page, idx) AS rn "
                "  FROM chunks WHERE user_id = %s AND source = ANY(%s)) "
                "SELECT h.id AS hit, o.text FROM ordered h "
                "JOIN ordered o ON o.source = h.source AND o.rn BETWEEN h.rn - %s AND h.rn + %s "
                "WHERE h.id = ANY(%s) ORDER BY h.id, o.rn",
                (self.user_id, sorted({h["source"] for h in hits}), k, k, [h["id"] for h in hits]),
            ).fetchall()
        joined: dict[str, list[str]] = {}
        for r in rows:
            joined.setdefault(r["hit"], []).append(r["text"])
        return {hit: "\n".join(texts) for hit, texts in joined.items()}

    def chunks_for_source(self, source: str) -> list[dict]:
        """All chunks of one document in reading order."""
        with db.autocommit() as conn:
            rows = conn.execute(
                "SELECT id, text, source, page, heading FROM chunks "
                "WHERE user_id = %s AND source = %s ORDER BY page, idx",
                (self.user_id, source),
            ).fetchall()
        return [dict(r) for r in rows]


def get_store(user_id: str = "default") -> Store:
    return Store(user_id)
