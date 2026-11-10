"""Central configuration. Every setting can be overridden through environment variables / .env."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}


def _csv(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


BASE_DIR = Path(__file__).resolve().parent.parent

# ---- database (PostgreSQL + pgvector holds EVERYTHING: accounts, files, vectors, logs) --------
# Managed Postgres (Render, Neon, Supabase, RDS...) works: just paste its connection string, and
# add ?sslmode=require when the provider needs TLS. The server must offer the pgvector extension.
def _clean_database_url(raw: str) -> str:
    """Forgive the usual copy-paste mistakes: surrounding quotes/whitespace, a leading ``psql``
    command (Neon's "copy snippet" button gives ``psql 'postgresql://...'``) and ``postgres://``."""
    url = (raw or "").strip()
    if url.lower().startswith("psql "):
        url = url[5:].strip()
    url = url.strip("\"'").strip()
    return url


DATABASE_URL = _clean_database_url(
    os.getenv("DATABASE_URL", "postgresql://docqa:docqa@localhost:5432/docqa")
)
DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "1"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "8"))  # per process (each uvicorn worker has its own)
DB_POOL_TIMEOUT = float(os.getenv("DB_POOL_TIMEOUT", "20"))  # seconds to wait for a free connection
DB_STATEMENT_TIMEOUT_MS = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "30000"))
DB_CONNECT_WAIT = float(os.getenv("DB_CONNECT_WAIT", "90"))  # startup: how long to wait for the DB (serverless DBs wake slowly)
# A pooled connection is health-checked (one extra network round trip) only if it sat idle this long.
DB_CHECK_IDLE_SECONDS = float(os.getenv("DB_CHECK_IDLE_SECONDS", "20"))
# Recycle pooled connections before a serverless database (Neon, Supabase) or a firewall silently
# drops them, and ask the OS to probe idle TCP connections.
DB_MAX_LIFETIME = float(os.getenv("DB_MAX_LIFETIME", "1800"))
DB_MAX_IDLE = float(os.getenv("DB_MAX_IDLE", "300"))
AUTO_MIGRATE = _bool("AUTO_MIGRATE", True)  # apply pending schema migrations at startup
EMBED_DIM = int(os.getenv("EMBED_DIM", "384"))  # MUST match EMBED_MODEL's vector size
FTS_LANGUAGE = os.getenv("FTS_LANGUAGE", "english")  # Postgres text-search config; fixed at install

# ---- logging -------------------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_JSON = _bool("LOG_JSON", True)  # one JSON object per line (what log aggregators expect)

# ---- models -----------------------------------------------------------------------------
# "onnx"  = fastembed + onnxruntime (default): the SAME two models, no PyTorch, roughly 250 MB of RAM.
# "torch" = sentence-transformers (needs `pip install sentence-transformers`, roughly 800 MB+ of RAM).
MODEL_BACKEND = os.getenv("MODEL_BACKEND", "onnx").strip().lower()
MODEL_THREADS = int(os.getenv("MODEL_THREADS", "2"))  # CPU threads per ONNX session
MODEL_CACHE_DIR = os.getenv("MODEL_CACHE_DIR", "") or None  # where the ONNX models are stored
DEFAULT_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_MODEL = os.getenv("EMBED_MODEL", DEFAULT_EMBED_MODEL)
# Same model in both backends; the ONNX export of it is published as "Xenova/ms-marco-MiniLM-L-6-v2".
_DEFAULT_RERANK = (
    "cross-encoder/ms-marco-MiniLM-L-6-v2"
    if os.getenv("MODEL_BACKEND", "onnx").strip().lower() == "torch"
    else "Xenova/ms-marco-MiniLM-L-6-v2"
)
RERANK_MODEL = os.getenv("RERANK_MODEL", _DEFAULT_RERANK)
# Some embedding models (bge, e5, ...) need an instruction prefix on QUERIES only, e.g.
# QUERY_PREFIX="Represent this sentence for searching relevant passages: "
QUERY_PREFIX = os.getenv("QUERY_PREFIX", "")

# ---- retrieval --------------------------------------------------------------------------
CHUNK_WORDS = int(os.getenv("CHUNK_WORDS", "180"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "40"))
TOP_K = int(os.getenv("TOP_K", "5"))
CANDIDATES = int(os.getenv("CANDIDATES", "20"))
# Vector search is EXACT (perfect recall, ~1 ms for 1,500 chunks, ~20 ms for 30,000) for any account
# with at most this many chunks. Larger accounts use the approximate HNSW index, with an exact
# fallback whenever it returns too few rows (see store.dense_search).
EXACT_SEARCH_MAX_CHUNKS = int(os.getenv("EXACT_SEARCH_MAX_CHUNKS", "100000"))
NEIGHBORS = int(os.getenv("NEIGHBORS", "1"))  # chunks of context added before/after each hit
REFUSAL_THRESHOLD = float(os.getenv("REFUSAL_THRESHOLD", "-3.0"))
# Reranker-score cut-offs used only to label answers "high / medium / low" confidence.
CONFIDENCE_HIGH = float(os.getenv("CONFIDENCE_HIGH", "3.0"))
CONFIDENCE_MEDIUM = float(os.getenv("CONFIDENCE_MEDIUM", "0.0"))

# ---- ingestion limits ---------------------------------------------------------------------
# Table detection costs about 70 ms per PDF page. After this many seconds of it, the rest of the
# document is indexed as text only (tables on those pages are not extracted). 0 turns tables off.
TABLE_EXTRACT_SECONDS = float(os.getenv("TABLE_EXTRACT_SECONDS", "30"))
# Hard limits that keep one upload from exhausting a small (512 MB) server.
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "300"))  # longer PDFs are refused with a clear message
MAX_OCR_PAGES = int(os.getenv("MAX_OCR_PAGES", "15"))  # scanned pages OCR'd per document (OCR is RAM-hungry)
INGEST_CONCURRENCY = int(os.getenv("INGEST_CONCURRENCY", "1"))  # documents processed at the same time
INGEST_WAIT_SECONDS = float(os.getenv("INGEST_WAIT_SECONDS", "90"))  # queue time before "busy"
# Load the embedding + reranker models in the background at start, so the first question is not slow.
WARM_MODELS = _bool("WARM_MODELS", True)

# ---- LLM --------------------------------------------------------------------------------
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
LLM_DEADLINE = float(
    os.getenv("LLM_DEADLINE", "120")
)  # max seconds one call may take incl. retries

# ---- accounts / security ------------------------------------------------------------------
ALLOW_REGISTRATION = _bool("ALLOW_REGISTRATION", True)
TRUST_PROXY = _bool(
    "TRUST_PROXY", False
)  # honour X-Forwarded-For (only behind a proxy you control)
CORS_ORIGINS = _csv("CORS_ORIGINS", "http://localhost:8501")
# A verified login token is remembered in memory this long, saving one database round trip per request.
# Trade-off: a password change / disabled account takes effect in OTHER processes after at most this long
# (immediately in the process that handled it). 0 turns the cache off.
AUTH_CACHE_SECONDS = float(os.getenv("AUTH_CACHE_SECONDS", "10"))
AUTH_MAX_ATTEMPTS = int(os.getenv("AUTH_MAX_ATTEMPTS", "8"))  # per email, per window
AUTH_IP_MAX_ATTEMPTS = int(os.getenv("AUTH_IP_MAX_ATTEMPTS", "60"))  # per client IP, per window
AUTH_WINDOW_SECONDS = int(os.getenv("AUTH_WINDOW_SECONDS", "900"))

# ---- public-demo settings -----------------------------------------------------------------
DEMO_MODE = _bool("DEMO_MODE", False)  # no accounts/uploads/deletes; indexes DEMO_DOCS_DIR
DEMO_USER = "demo"  # demo traffic lives in its own namespace, never in a real account's
DEMO_DOCS_DIR = Path(os.getenv("DEMO_DOCS_DIR", BASE_DIR / "data" / "sample_docs"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "10"))  # files are stored in the database
DAILY_LLM_LIMIT = int(os.getenv("DAILY_LLM_LIMIT", "60"))  # questions per user per UTC day; 0 = off
GLOBAL_DAILY_LLM_LIMIT = int(os.getenv("GLOBAL_DAILY_LLM_LIMIT", "0"))  # whole app; 0 = off
MAX_SUMMARY_CHUNKS = int(os.getenv("MAX_SUMMARY_CHUNKS", "12"))

# ---- ingestion / analytics ---------------------------------------------------------------
ALLOW_URL_INGEST = _bool("ALLOW_URL_INGEST", False)  # fetches remote pages: off unless enabled
LOG_QUERIES = _bool("LOG_QUERIES", True)  # store question text for analytics/feedback
SEMANTIC_CITATION_AUDIT = _bool("SEMANTIC_CITATION_AUDIT", False)
