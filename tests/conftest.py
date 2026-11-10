"""Test setup. Everything runs offline: the embedder and reranker are tiny deterministic fakes and
the LLM is monkeypatched. The database is REAL PostgreSQL + pgvector (tests that need it are skipped,
with instructions, when none is reachable).

Point the tests at a database whose name ends in ``_test`` - its schema is wiped at the start of
every run:

    docker compose up -d db                       # or any local PostgreSQL with pgvector
    TEST_DATABASE_URL=postgresql://docqa:docqa@localhost:5432/docqa_test pytest
"""

import os
import re
import zlib

# Must happen BEFORE the app is imported (config reads these at import time).
os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL", "postgresql://docqa:docqa@127.0.0.1:5432/docqa_test"
)
os.environ["AUTH_SECRET"] = "test-secret-that-is-long-enough-for-the-tests-0123456789"
os.environ["LLM_API_KEY"] = ""
os.environ["DEMO_MODE"] = "0"
os.environ["NEIGHBORS"] = "1"
os.environ["EMBED_DIM"] = "256"  # FakeEmbedder.DIM
os.environ["WARM_MODELS"] = "0"  # tests use fakes; a startup test switches it on
os.environ["AUTH_CACHE_SECONDS"] = "0"  # tests that need the token cache switch it on themselves
os.environ.setdefault("LOG_JSON", "0")

import numpy as np  # noqa: E402
import psycopg  # noqa: E402
import pytest  # noqa: E402

_STOP = {"the", "a", "an", "of", "to", "is", "are", "in", "for", "and", "how", "many", "what", "do"}


def _tokens(text):
    return [t for t in re.findall(r"\w+", text.lower()) if t not in _STOP]


class FakeEmbedder:
    """Bag-of-words hashing embedder: similar words -> similar vectors (identical in every process)."""

    DIM = 256

    def encode(self, texts, normalize_embeddings=True):
        out = np.zeros((len(texts), self.DIM), dtype="float32")
        for row, text in enumerate(texts):
            for tok in _tokens(text):
                out[row, zlib.crc32(tok.encode()) % self.DIM] += 1.0  # stable across processes
            norm = np.linalg.norm(out[row]) or 1.0
            out[row] /= norm
        return out


class FakeReranker:
    """Score = 2 * shared-token count - 4, so unrelated passages fall below the -3 threshold."""

    def predict(self, pairs):
        return np.array(
            [2.0 * len(set(_tokens(q)) & set(_tokens(p))) - 4.0 for q, p in pairs], dtype="float32"
        )


@pytest.fixture(autouse=True)
def fake_models(monkeypatch):
    from app import models

    models.reset()
    monkeypatch.setattr(models, "get_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(models, "get_reranker", lambda: FakeReranker())
    yield
    models.reset()


@pytest.fixture(scope="session")
def _schema():
    """A pristine, fully migrated schema once per test run."""
    from app import config, db

    try:
        conn = psycopg.connect(config.DATABASE_URL, connect_timeout=3, autocommit=True)
    except psycopg.OperationalError as e:
        pytest.skip(
            f"PostgreSQL is not reachable ({str(e).strip()[:120]}). Start one with "
            "`docker compose up -d db` or set TEST_DATABASE_URL."
        )
    name = conn.execute("SELECT current_database()").fetchone()[0]
    if not name.endswith("_test"):  # never wipe a real database by accident
        conn.close()
        pytest.exit(f"Refusing to run tests against database '{name}': its name must end in _test.")
    conn.execute("DROP SCHEMA public CASCADE")
    conn.execute("CREATE SCHEMA public")
    conn.close()
    db.close_pool()
    db.migrate()
    db.check_embedding_compat()
    yield
    db.close_pool()


@pytest.fixture
def pg(_schema):
    """Empty tables for every test (the schema and install-time settings are kept)."""
    from app import db

    with db.connection() as conn:
        conn.execute(
            "TRUNCATE users, documents, chunks, budget, rate_limits, query_log, feedback, audit_log "
            "RESTART IDENTITY CASCADE"
        )
    yield


@pytest.fixture
def user_id(request):
    """A unique account id per test."""
    return "user-" + re.sub(r"\W+", "-", request.node.name)[:60]
