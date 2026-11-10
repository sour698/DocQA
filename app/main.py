"""FastAPI backend for DocQA."""

import contextvars
import json
import logging
import queue
import re
import tempfile
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlparse

import psycopg
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from . import agent, analytics, auth, db, fetch, llm, models, rag
from .config import (
    ALLOW_REGISTRATION,
    ALLOW_URL_INGEST,
    AUTH_IP_MAX_ATTEMPTS,
    AUTH_MAX_ATTEMPTS,
    AUTH_WINDOW_SECONDS,
    CHUNK_OVERLAP,
    CHUNK_WORDS,
    CORS_ORIGINS,
    DAILY_LLM_LIMIT,
    DEMO_DOCS_DIR,
    DEMO_MODE,
    DEMO_USER,
    GLOBAL_DAILY_LLM_LIMIT,
    INGEST_CONCURRENCY,
    INGEST_WAIT_SECONDS,
    LOG_JSON,
    LOG_LEVEL,
    MAX_UPLOAD_MB,
    TRUST_PROXY,
    WARM_MODELS,
)
from .ingest import IngestLimitError, chunk_pages, html_to_blocks, load_document
from .limits import Budget, RateLimiter
from .logging_config import request_id, setup_logging
from .render import render_page
from .store import get_store
from .utils import ALLOWED_EXTENSIONS, safe_filename

VERSION = "2.0.0"
log = logging.getLogger("docqa")

budget = Budget(DAILY_LLM_LIMIT, GLOBAL_DAILY_LLM_LIMIT)
email_limiter = RateLimiter(AUTH_MAX_ATTEMPTS, AUTH_WINDOW_SECONDS)
ip_limiter = RateLimiter(AUTH_IP_MAX_ATTEMPTS, AUTH_WINDOW_SECONDS)
bearer = HTTPBearer(auto_error=False)

MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/plain",
    ".html": "text/plain",  # served as text, never rendered as a page
    ".htm": "text/plain",
}


# ------------------------------------------------------------------------------ helpers
def client_ip(request: Request) -> str:
    if TRUST_PROXY:
        # Assumes exactly ONE trusted proxy (e.g. Render) in front: it appends the real client
        # address, so the RIGHTMOST entry is the one to trust (earlier ones can be forged).
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
    if DEMO_MODE:
        return DEMO_USER
    if credentials is None:
        raise HTTPException(401, "Sign in to access your documents.")
    try:
        return auth.verify(credentials.credentials)
    except ValueError as e:
        raise HTTPException(401, str(e)) from None


def index_folder(store, folder: Path) -> int:
    count = 0
    for path in sorted(Path(folder).iterdir()):
        if path.suffix.lower() not in ALLOWED_EXTENSIONS or path.name.startswith("."):
            continue
        pages = load_document(path)
        if pages:
            chunks = chunk_pages(pages, path.name, CHUNK_WORDS, CHUNK_OVERLAP)
            store.put_document(path.name, path.read_bytes(), chunks, pages=len(pages))
            count += 1
    return count


def spend_or_429(uid: str, request: Request) -> None:
    key = f"demo:{client_ip(request)}" if DEMO_MODE else uid
    if not budget.try_spend(key):
        raise HTTPException(429, "Daily question limit reached - please try again tomorrow.")


def _auth_guard(request: Request, email: str | None = None) -> None:
    keys = [(ip_limiter, f"ip:{client_ip(request)}")]
    if email:
        keys.append((email_limiter, f"email:{email.strip().lower()}"))
    for limiter, key in keys:
        if not limiter.allow(key):
            analytics.audit("rate_limited", None, client_ip(request), key.split(":")[0])
            raise HTTPException(
                429,
                "Too many attempts. Please wait a few minutes and try again.",
                headers={"Retry-After": str(AUTH_WINDOW_SECONDS)},
            )


def _validate_content(name: str, data: bytes) -> None:
    ext = Path(name).suffix.lower()
    if ext == ".pdf" and not data.startswith(b"%PDF-"):
        raise HTTPException(400, "Uploaded file is not a valid PDF.")
    if ext == ".docx" and not data.startswith(b"PK"):
        raise HTTPException(400, "Uploaded file is not a valid .docx document.")
    if ext not in (".pdf", ".docx") and b"\x00" in data[:4096]:
        raise HTTPException(400, "Uploaded file looks binary, not text.")


_ingest_slots = threading.BoundedSemaphore(max(1, INGEST_CONCURRENCY))


def _ingest_bytes(uid: str, name: str, data: bytes) -> dict:
    """Parse -> embed -> store file and chunks in ONE database transaction. A failed re-upload
    changes nothing, so the previous good copy survives.

    Only ``INGEST_CONCURRENCY`` documents are processed at once: parsing + embedding is the most
    memory-hungry thing the server does, and a handful of simultaneous uploads would otherwise
    get the whole process killed by the platform's out-of-memory guard.
    """
    if not _ingest_slots.acquire(timeout=INGEST_WAIT_SECONDS):
        raise HTTPException(
            503,
            "The server is busy indexing other documents. Please try again in a minute.",
            headers={"Retry-After": "30"},
        )
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / name  # `name` is sanitised: no directories, allow-listed extension
            path.write_bytes(data)
            try:
                pages = load_document(path)
            except IngestLimitError as e:
                raise HTTPException(422, str(e)) from None
            except Exception as e:  # noqa: BLE001 - any parser failure means "unreadable file"
                log.warning("Could not read %s: %s", name, e)
                raise HTTPException(
                    422, "Could not read this file. Is it damaged or password-protected?"
                ) from None
        if not pages:
            raise HTTPException(
                422,
                "No readable text found. For scanned PDFs, install Tesseract OCR and Poppler; see README.",
            )
        chunks = chunk_pages(pages, name, CHUNK_WORDS, CHUNK_OVERLAP)
        get_store(uid).put_document(name, data, chunks, pages=len(pages))
    finally:
        _ingest_slots.release()
    return {
        "document": name,
        "pages": len(pages),
        "chunks": len(chunks),
        "format": Path(name).suffix.lower().lstrip("."),
    }


def _log(uid, data: dict, started: float, route: str | None = None) -> None:
    analytics.log_query(
        uid,
        data.get("standalone_question") or data.get("question") or "",
        route=route or data.get("route"),
        refused=data.get("refused", False),
        from_documents=data.get("from_documents"),
        top_score=data.get("top_score"),
        latency_ms=int((time.monotonic() - started) * 1000),
    )


HEARTBEAT_SECONDS = 8.0


def _heartbeat(events, interval: float = HEARTBEAT_SECONDS):
    """Yield the events of ``events`` and, while it is busy (rewriting the question, waiting on the
    LLM provider, loading a model...), a ``{"type": "ping"}`` every ``interval`` seconds.

    Without this a long silent preamble looks like a dead connection to proxies and browsers, and
    the client reports "the server closed the connection". The first ping is sent immediately.
    """
    box: queue.Queue = queue.Queue()
    stop = threading.Event()

    def pump():
        try:
            for event in events:
                if stop.is_set():
                    break
                box.put(("event", event))
        except BaseException as exc:  # noqa: BLE001 - handed to the consumer, which re-raises it
            box.put(("error", exc))
        finally:
            box.put(("end", None))

    ctx = contextvars.copy_context()  # keep the request id in the worker thread's log lines
    threading.Thread(target=ctx.run, args=(pump,), name="stream-pump", daemon=True).start()
    try:
        yield {"type": "ping"}
        while True:
            try:
                kind, value = box.get(timeout=interval)
            except queue.Empty:
                yield {"type": "ping"}
                continue
            if kind == "event":
                yield value
            elif kind == "error":
                raise value
            else:
                return
    finally:
        stop.set()  # client went away: stop feeding it


def _ndjson(events, uid: str, started: float, question: str, route: str | None = None):
    """Turn answer events into newline-delimited JSON; failures become an 'error' event."""
    try:
        for event in _heartbeat(events):
            if event["type"] == "final":
                _log(uid, {"question": question, **event["data"]}, started, route)
            yield json.dumps(event, ensure_ascii=False) + "\n"
    except llm.LLMError as e:
        yield json.dumps({"type": "error", "status": 502, "detail": str(e)}) + "\n"
    except psycopg.OperationalError:
        log.exception("Database unavailable while answering")
        yield json.dumps(
            {
                "type": "error",
                "status": 503,
                "detail": "The database is temporarily unavailable. Please retry in a moment.",
            }
        ) + "\n"
    except Exception:  # noqa: BLE001
        log.exception("Streaming answer failed")
        yield json.dumps({"type": "error", "status": 500, "detail": "Internal error."}) + "\n"


# ------------------------------------------------------------------------------ app
@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(LOG_LEVEL, LOG_JSON)
    if not DEMO_MODE:
        problem = auth.secret_problem()
        if problem:
            raise RuntimeError(
                f"Refusing to start: {problem} Set a long random AUTH_SECRET in .env "
                '(python -c "import secrets; print(secrets.token_urlsafe(48))").'
            )
    db.startup()  # wait for PostgreSQL, apply pending migrations, verify the embedding setup
    if DEMO_MODE:
        with db.advisory_lock(db.SEED_LOCK):  # several workers start at once: seed only once
            if not get_store(DEMO_USER).list_sources():
                index_folder(get_store(DEMO_USER), DEMO_DOCS_DIR)
    warmer = None
    if WARM_MODELS:  # in the background: the app answers requests while the models load
        warmer = threading.Thread(target=models.warm_up, name="model-warm-up", daemon=True)
        warmer.start()
    log.info("DocQA %s started", VERSION)
    yield
    db.close_pool()


app = FastAPI(title="DocQA RAG", version=VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)

_RID_OK = re.compile(r"[A-Za-z0-9._-]{1,64}")


@app.middleware("http")
async def guard(request: Request, call_next):
    """Request id, access log (path only: never query strings or bodies), size guard, headers."""
    supplied = request.headers.get("x-request-id", "")
    rid = supplied if _RID_OK.fullmatch(supplied) else uuid.uuid4().hex[:16]
    token = request_id.set(rid)
    started = time.monotonic()
    try:
        limit = int((MAX_UPLOAD_MB + 1) * 1024 * 1024)
        try:
            too_big = int(request.headers.get("content-length", "0")) > limit
        except ValueError:
            too_big = True
        if too_big:
            response = JSONResponse(
                {"detail": f"Request too large (max {MAX_UPLOAD_MB} MB)."}, status_code=413
            )
        else:
            response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        ms = int((time.monotonic() - started) * 1000)
        level = logging.DEBUG if request.url.path in ("/health", "/ready") else logging.INFO
        log.log(
            level,
            f"{request.method} {request.url.path} {response.status_code} {ms}ms",
            extra={
                "extra_fields": {
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "ms": ms,
                    "ip": client_ip(request),
                }
            },
        )
        return response
    finally:
        request_id.reset(token)


@app.exception_handler(psycopg.OperationalError)
async def database_unavailable(request: Request, exc: psycopg.OperationalError):
    """Pool exhausted, statement timeout, connection lost... tell the client to retry."""
    log.error("Database unavailable: %s", exc)
    return JSONResponse(
        {"detail": "The database is temporarily unavailable. Please retry in a moment."},
        status_code=503,
        headers={"Retry-After": "5"},
    )


class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=auth.MAX_PASSWORD)


class PasswordChange(BaseModel):
    old_password: str = Field(max_length=auth.MAX_PASSWORD)
    new_password: str = Field(max_length=auth.MAX_PASSWORD)


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    mode: str = "hybrid_rerank"


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class AgentRequest(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    history: list[Message] = Field(default_factory=list, max_length=40)
    allow_general: bool = False


class UrlRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


class FeedbackRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    answer: str = Field(default="", max_length=8000)
    rating: Literal[1, -1]
    comment: str = Field(default="", max_length=1000)


# ------------------------------------------------------------------------------ accounts
@app.post("/auth/register")
def register(req: Credentials, request: Request):
    if DEMO_MODE or not ALLOW_REGISTRATION:
        raise HTTPException(403, "Registration is disabled on this server.")
    _auth_guard(request)
    if auth.secret_problem():
        raise HTTPException(503, "Accounts are not configured on this server.")
    try:
        uid = auth.register(req.email, req.password)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    analytics.audit("register", uid, client_ip(request))
    return {"token": auth.token_for(uid)}


@app.post("/auth/login")
def login(req: Credentials, request: Request):
    if DEMO_MODE:
        raise HTTPException(403, "Accounts are disabled in the public demo.")
    _auth_guard(request, req.email)
    try:
        token = auth.login(req.email, req.password)
    except ValueError as e:
        analytics.audit(
            "login_failed", None, client_ip(request), f"email#{analytics.hash_email(req.email)}"
        )
        raise HTTPException(401, str(e)) from None
    email_limiter.reset(f"email:{req.email.strip().lower()}")
    uid = auth.user_id_for(req.email)
    analytics.audit("login", uid, client_ip(request))
    return {"token": token}


@app.post("/auth/change-password")
def change_password(req: PasswordChange, request: Request, uid: str = Depends(current_user)):
    if DEMO_MODE:
        raise HTTPException(403, "Accounts are disabled in the public demo.")
    try:
        token = auth.change_password(uid, req.old_password, req.new_password)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    analytics.audit("password_changed", uid, client_ip(request))
    return {"token": token}


# ------------------------------------------------------------------------------ meta
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    """Readiness: the database answers and the schema is current (liveness is /health)."""
    if not db.ping():
        raise HTTPException(503, "Database unavailable.")
    pending = db.pending_migrations()
    if pending:
        raise HTTPException(503, f"Pending migrations: {', '.join(pending)}")
    return {"status": "ready"}


@app.get("/version")
def version():
    return {"version": VERSION}


@app.get("/config")
def config():
    return {
        "demo_mode": DEMO_MODE,
        "max_upload_mb": MAX_UPLOAD_MB,
        "auth_required": not DEMO_MODE,
        "allow_registration": ALLOW_REGISTRATION and not DEMO_MODE,
        "allow_url_ingest": ALLOW_URL_INGEST and not DEMO_MODE,
        "allowed_extensions": list(ALLOWED_EXTENSIONS),
    }


# ------------------------------------------------------------------------------ documents
@app.get("/documents")
def documents(uid: str = Depends(current_user)):
    return {"documents": get_store(uid).list_sources()}


@app.post("/upload")
def upload(request: Request, file: UploadFile = File(...), uid: str = Depends(current_user)):
    # A plain `def` (not async): parsing and embedding are CPU-heavy and must not block the
    # event loop; FastAPI runs this in its thread pool.
    if DEMO_MODE:
        raise HTTPException(403, "Uploads are disabled in the public demo.")
    try:
        name = safe_filename(file.filename or "")
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    cap = MAX_UPLOAD_MB * 1024 * 1024
    data = file.file.read(cap + 1)  # never read more than the cap into memory
    if len(data) > cap:
        raise HTTPException(413, f"File too large (max {MAX_UPLOAD_MB} MB).")
    _validate_content(name, data)
    result = _ingest_bytes(uid, name, data)
    analytics.audit("upload", uid, client_ip(request), f"{name} {len(data)}B")
    return result


@app.post("/ingest/url")
def ingest_url(req: UrlRequest, request: Request, uid: str = Depends(current_user)):
    if DEMO_MODE or not ALLOW_URL_INGEST:
        raise HTTPException(403, "URL ingestion is disabled on this server.")
    try:
        data, ctype, final_url = fetch.fetch(req.url)
    except fetch.FetchError as e:
        raise HTTPException(400, str(e)) from None
    parsed = urlparse(final_url)
    slug = (
        re.sub(r"[^A-Za-z0-9]+", "_", f"{parsed.hostname}{parsed.path}").strip("_")[:80] or "page"
    )
    if ctype == "application/pdf" or data.startswith(b"%PDF-"):
        name, payload = f"{slug}.pdf", data
    elif ctype in {"text/html", "application/xhtml+xml", "text/plain", "text/markdown"}:
        text = data.decode("utf-8", errors="replace")
        blocks = html_to_blocks(text) if "html" in ctype else [text]
        name = f"{slug}.md"
        payload = (f"# {final_url}\n\n" + "\n\n".join(blocks)).encode()
    else:
        raise HTTPException(400, f"Unsupported content type: {ctype or 'unknown'}.")
    _validate_content(name, payload)
    result = _ingest_bytes(uid, safe_filename(name), payload)
    analytics.audit("url_ingest", uid, client_ip(request), f"{parsed.hostname} {name}")
    return result


def _stored_document(uid: str, name: str) -> bytes:
    data = get_store(uid).get_document(Path(name).name)
    if data is None:
        raise HTTPException(404, "Document not found for this account.")
    return data


@app.get("/documents/{name}/file")
def document_file(name: str, uid: str = Depends(current_user)):
    data = _stored_document(uid, name)
    media = MEDIA_TYPES.get(Path(name).suffix.lower(), "application/octet-stream")
    return Response(
        data,
        media_type=media,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(Path(name).name)}"},
    )


@app.get("/documents/{name}/page/{page}")
def document_page(name: str, page: int, quote: str = "", uid: str = Depends(current_user)):
    """PNG of one PDF page with the cited passage highlighted (click-through citations)."""
    if Path(name).suffix.lower() != ".pdf":
        raise HTTPException(400, "Page previews are only available for PDF documents.")
    data = _stored_document(uid, name)
    try:
        png, found = render_page(data, page, quote[:600])
    except ValueError as e:
        raise HTTPException(404, str(e)) from None
    return Response(
        png,
        media_type="image/png",
        headers={"X-Highlights": str(found), "Cache-Control": "private, max-age=300"},
    )


@app.delete("/documents/{name}")
def delete_document(name: str, request: Request, uid: str = Depends(current_user)):
    if DEMO_MODE:
        raise HTTPException(403, "Deleting is disabled in the public demo.")
    safe = Path(name).name
    if not get_store(uid).delete_source(safe):
        raise HTTPException(404, "Document not found for this account.")
    analytics.audit("delete", uid, client_ip(request), safe)
    return {"deleted": safe}


# ------------------------------------------------------------------------------ questions
def _llm_guard(fn):
    try:
        return fn()
    except llm.LLMError as e:
        raise HTTPException(502, str(e)) from None
    except RuntimeError as e:
        raise HTTPException(500, str(e)) from None


@app.post("/ask")
def ask(req: AskRequest, request: Request, uid: str = Depends(current_user)):
    if req.mode not in ("naive", "hybrid_rerank"):
        raise HTTPException(400, "mode must be 'naive' or 'hybrid_rerank'")
    spend_or_429(uid, request)
    started = time.monotonic()
    out = _llm_guard(lambda: rag.answer(req.question, req.mode, uid))
    out.pop("retrieved", None)
    _log(uid, out, started, route="ask")
    return out


@app.post("/ask/stream")
def ask_stream(req: AskRequest, request: Request, uid: str = Depends(current_user)):
    if req.mode not in ("naive", "hybrid_rerank"):
        raise HTTPException(400, "mode must be 'naive' or 'hybrid_rerank'")
    spend_or_429(uid, request)
    events = rag.answer_stream(req.question, req.mode, uid)
    return StreamingResponse(
        _ndjson(events, uid, time.monotonic(), req.question, "ask"),
        media_type="application/x-ndjson; charset=utf-8",
    )


def _history(req: AgentRequest) -> list[dict]:
    return [m.model_dump() for m in req.history[-10:]]


@app.post("/agent/ask")
def agent_ask(req: AgentRequest, request: Request, uid: str = Depends(current_user)):
    spend_or_429(uid, request)
    started = time.monotonic()
    out = _llm_guard(
        lambda: agent.run(
            req.question, _history(req), get_store(uid).list_sources(), req.allow_general, uid
        )
    )
    _log(uid, out, started)
    return out


@app.post("/agent/ask/stream")
def agent_ask_stream(req: AgentRequest, request: Request, uid: str = Depends(current_user)):
    spend_or_429(uid, request)
    events = agent.run_stream(
        req.question, _history(req), get_store(uid).list_sources(), req.allow_general, uid
    )
    return StreamingResponse(
        _ndjson(events, uid, time.monotonic(), req.question), media_type="application/x-ndjson"
    )


# ------------------------------------------------------------------------------ feedback / stats
@app.post("/feedback")
def feedback(req: FeedbackRequest, uid: str = Depends(current_user)):
    analytics.log_feedback(uid, req.question, req.answer, req.rating, req.comment)
    return {"status": "recorded"}


@app.get("/stats")
def stats(days: int = 30, uid: str = Depends(current_user)):
    return analytics.stats(uid, days=max(1, min(days, 365)))
