"""PostgreSQL access: one connection pool per process, migrations, startup checks.

Everything durable lives in PostgreSQL (+ pgvector), so application containers are stateless and
can be restarted, replaced or scaled out freely.
"""

import atexit
import logging
import re
import threading
import time
import weakref
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import (
    AUTO_MIGRATE,
    DATABASE_URL,
    DB_CHECK_IDLE_SECONDS,
    DB_CONNECT_WAIT,
    DB_MAX_IDLE,
    DB_MAX_LIFETIME,
    DB_POOL_MAX,
    DB_POOL_MIN,
    DB_POOL_TIMEOUT,
    DB_STATEMENT_TIMEOUT_MS,
    EMBED_DIM,
    EMBED_MODEL,
    FTS_LANGUAGE,
)

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
MIGRATION_LOCK = 727_001  # advisory-lock ids (arbitrary, app-wide unique)
SEED_LOCK = 727_002

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()
_pgvector_version: tuple[int, ...] | None = None
_atexit_registered = False
_last_used: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _touch(conn) -> None:
    """Remember when a connection was last handed back (pool configure/reset callback)."""
    _last_used[conn] = time.monotonic()


def _check(conn) -> None:
    """Pool health check, but only for connections that sat idle for a while. Checking on EVERY
    checkout costs a full network round trip each time (measured: 4 round trips for one trivial
    read instead of 3, and 1 once autocommit is used)."""
    if time.monotonic() - _last_used.get(conn, 0.0) >= DB_CHECK_IDLE_SECONDS:
        ConnectionPool.check_connection(conn)


def is_transaction_pooler(url: str) -> bool:
    """True for connection strings that go through a transaction-mode pooler (PgBouncer, Supavisor):
    Neon's ``-pooler`` hostnames and port 6543. Those poolers do not support session-level
    settings, so the connection startup option below must not be sent through them."""
    parsed = urlparse(url)
    return "-pooler." in (parsed.hostname or "") or parsed.port == 6543


def pool_kwargs(url: str = DATABASE_URL) -> dict:
    kwargs = {
        "row_factory": dict_row,
        "prepare_threshold": None,  # no server-side prepared statements (poolers can't keep them)
        "connect_timeout": 15,  # a serverless database may be waking up: wait, but not forever
        # Probe idle TCP connections so one silently dropped by the provider is noticed quickly.
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
    }
    if is_transaction_pooler(url):
        log.warning(
            "DATABASE_URL points at a transaction-mode pooler: statement_timeout is NOT applied. "
            "Prefer the direct (or session-mode) connection string; see README."
        )
    else:
        kwargs["options"] = f"-c statement_timeout={DB_STATEMENT_TIMEOUT_MS}"
    return kwargs


def get_pool() -> ConnectionPool:
    global _pool, _atexit_registered
    with _pool_lock:
        if not _atexit_registered:
            # Close the pool while the interpreter is still fully alive. Without this, a script that
            # forgets to (or exits early) triggers "cannot join thread at interpreter shutdown" on
            # newer Pythons (3.13+/3.14).
            atexit.register(close_pool)
            _atexit_registered = True
        if _pool is None:
            _pool = ConnectionPool(
                DATABASE_URL,
                min_size=DB_POOL_MIN,
                max_size=DB_POOL_MAX,
                timeout=DB_POOL_TIMEOUT,
                max_lifetime=DB_MAX_LIFETIME,  # retire connections before the provider does
                max_idle=DB_MAX_IDLE,
                name="docqa",
                open=True,
                kwargs=pool_kwargs(DATABASE_URL),
                configure=_touch,
                reset=_touch,
                check=_check,  # drop connections the server closed while they sat idle
            )
        return _pool


def close_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None


@contextmanager
def connection():
    """A pooled connection inside a transaction: commits on success, rolls back on any error."""
    with get_pool().connection() as conn:
        yield conn


@contextmanager
def autocommit():
    """A pooled connection WITHOUT a surrounding transaction: each statement commits by itself.

    Use it for a single read or a single write. It costs ONE network round trip, where
    ``connection()`` costs three (BEGIN, the statement, COMMIT). For several statements that must
    succeed or fail together, use ``connection()``.
    """
    with get_pool().connection() as conn:
        conn.autocommit = True
        try:
            yield conn
        finally:
            try:
                conn.autocommit = False
            except Exception:  # noqa: BLE001 - a broken connection is discarded by the pool anyway
                pass


@contextmanager
def advisory_lock(key: int):
    """Hold a cluster-wide lock for the duration of the block (released at commit/rollback)."""
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (key,))
        yield conn


def ping() -> bool:
    try:
        with autocommit() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001 - any failure means "not ready"
        return False


def wait_ready(timeout: float | None = None) -> None:
    """Block until the database accepts connections (the DB container often starts later)."""
    deadline = time.monotonic() + (DB_CONNECT_WAIT if timeout is None else timeout)
    delay = 0.5
    while True:
        try:
            with psycopg.connect(DATABASE_URL, connect_timeout=15) as conn:
                conn.execute("SELECT 1")
            return
        except psycopg.OperationalError as exc:
            if time.monotonic() + delay > deadline:
                raise RuntimeError(
                    f"Cannot reach the database ({str(exc).strip()[:200]}). "
                    "Check DATABASE_URL and that PostgreSQL is running."
                ) from exc
            log.info("Waiting for the database...")
            time.sleep(delay)
            delay = min(delay * 2, 5)


def _render(sql: str) -> str:
    if not re.fullmatch(r"[a-z_]+", FTS_LANGUAGE):
        raise RuntimeError("FTS_LANGUAGE must be a Postgres text-search configuration name.")
    return sql.replace("{EMBED_DIM}", str(int(EMBED_DIM))).replace("{FTS_LANGUAGE}", FTS_LANGUAGE)


def migrate() -> list[str]:
    """Apply pending ``migrations/*.sql`` files, in order, exactly once.

    Safe when several app processes start at the same moment: a transaction-level advisory lock
    makes them take turns, and a failed migration rolls everything back.
    """
    applied = []
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK,))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.stem in done:
                continue
            conn.execute(_render(path.read_text(encoding="utf-8")))
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,))
            applied.append(path.stem)
            log.info("Applied migration %s", path.stem)
    return applied


def pending_migrations() -> list[str]:
    with autocommit() as conn:
        exists = conn.execute("SELECT to_regclass('schema_migrations') AS t").fetchone()["t"]
        done = (
            {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
            if exists
            else set()
        )
    return [p.stem for p in sorted(MIGRATIONS_DIR.glob("*.sql")) if p.stem not in done]


def check_embedding_compat() -> None:
    """Fail fast (with the fix) if the configured embedding model no longer matches the stored
    vectors - mixing models would silently ruin search quality."""
    with connection() as conn:
        rows = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
        n_chunks = conn.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"]
        if int(rows.get("embed_dim", EMBED_DIM)) != EMBED_DIM:
            raise RuntimeError(
                f"EMBED_DIM={EMBED_DIM} but the database holds {rows['embed_dim']}-dimensional "
                "vectors. Rebuild them for the new model: "
                "python -m scripts.reindex --all --reset-vectors"
            )
        stored = rows.get("embed_model")
        if stored is None or n_chunks == 0:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('embed_model', %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (EMBED_MODEL,),
            )
        elif stored != EMBED_MODEL:
            raise RuntimeError(
                f"EMBED_MODEL is '{EMBED_MODEL}' but the stored vectors were made with '{stored}'. "
                "Rebuild them: python -m scripts.reindex --all --reset-vectors"
            )


def reset_vector_column() -> None:
    """Drop every chunk and resize the vector column for the CURRENT EMBED_DIM / EMBED_MODEL.
    Documents (the original files) are kept; re-create chunks with scripts/reindex.py."""
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK,))
        conn.execute("DROP INDEX IF EXISTS chunks_embedding")
        conn.execute("TRUNCATE chunks")
        conn.execute("ALTER TABLE chunks DROP COLUMN embedding")
        conn.execute(f"ALTER TABLE chunks ADD COLUMN embedding vector({int(EMBED_DIM)}) NOT NULL")
        conn.execute(
            "CREATE INDEX chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops)"
        )
        for key, value in (("embed_dim", str(EMBED_DIM)), ("embed_model", EMBED_MODEL)):
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, value),
            )


def pgvector_version() -> tuple[int, ...]:
    global _pgvector_version
    if _pgvector_version is None:
        with connection() as conn:
            row = conn.execute(
                "SELECT extversion FROM pg_extension WHERE extname='vector'"
            ).fetchone()
        _pgvector_version = (
            tuple(int(x) for x in re.findall(r"\d+", row["extversion"])) if row else (0,)
        )
    return _pgvector_version


def startup() -> None:
    """Everything the app needs from the database before it serves traffic."""
    wait_ready()
    if AUTO_MIGRATE:
        migrate()
    else:
        pending = pending_migrations()
        if pending:
            raise RuntimeError(
                f"Pending database migrations: {', '.join(pending)}. Run: python -m scripts.migrate"
            )
    check_embedding_compat()
