# Release 3.2: speed (the app felt slow once documents were indexed)

Measured with a simulated 100 ms network round trip to the database (real PostgreSQL, real browser, real UI):

| Action | Before | After |
|---|---|---|
| any click / toggle | 2.0 s | 0.3 s |
| ask a question | 10.3 s | 1.9 s |
| sign in | 4.9 s | 1.4 s |
| upload + index a 30-page PDF | 8.5 s | 4.0 s (about 2 s of it is PDF table detection) |

Causes and fixes: every click re-fetched the document list and the usage statistics (about 16-20 database round trips);
one trivial read cost 4 round trips (health check, BEGIN, query, COMMIT); retrieval opened a separate transaction per hit;
the login rate limiter needed about 6 round trips per attempt. Now: session caches in the UI, autocommit for single
statements, a health check only for idle connections, a 10 s token-verification cache, one batched context query, pipelined
uploads, a one-statement rate limiter (migration `0003`), models warmed in the background, table detection capped at 30 s
per PDF, and `scripts/db_latency.py` to measure your own database distance. Details and trade-offs: OPERATIONS.md "Speed".

* Behaviour changes: usage statistics are opt-in (a toggle); a password change reaches other app processes within
  `AUTH_CACHE_SECONDS`; the login limiter is a fixed window and counts refused attempts; very long PDFs may index
  without tables on their later pages.
* Not verified: the real Groq/embedding latency (tests use fakes); a real hosted database (the delay was simulated with a
  local proxy); a real browser other than headless Chromium.

---

# Release 3.1: sign-in screen redesign + dark theme fix

* New sign-in / create-account screen: brand panel, pill tabs, dark inputs, a "confirm password" field, checks that run
  before anything is sent (email format, length, matching passwords), clear error messages, password-manager support,
  a layout that shows the whole form on a phone without scrolling. Verified by looking at real screenshots (headless
  Chromium, Streamlit 1.67): desktop and phone, both tabs, empty/wrong/invalid submissions.
* `.streamlit/config.toml` sets Streamlit's dark theme. Without it the dashboard had real contrast defects (white upload
  box and "Send question" button with invisible text, near-invisible question box text, red toggles, light-green status
  boxes); all fixed. It also hides the "Deploy" menu and Python tracebacks from end users, and makes the uploader show the
  real 10 MB limit (it said 200 MB). If you raise `MAX_UPLOAD_MB`, raise `maxUploadSize` there too (a test guards this).
* CSS covers both the new (React Aria) and older (BaseWeb) Streamlit widget markup; only 1.67 was looked at visually.

---

# Release 3: PostgreSQL for everything, production hardening

## Architecture

SQLite + Chroma + files on disk are gone. **PostgreSQL + pgvector** now holds accounts, the original uploaded
files, chunks, vectors, budgets, rate limits, query log, feedback and a new audit log. The app container is
stateless (no volume), so it can be restarted, replaced or scaled out, and one `pg_dump` backs up everything.

* Versioned SQL migrations (`app/migrations/`), applied under a PostgreSQL advisory lock so several workers can
  start at the same moment; a failed migration rolls back completely.
* A document and its chunks are written in one transaction; deleting a document cascades to its chunks (before,
  the file, the vector index and the keyword index could drift apart).
* Search: pgvector (cosine) + PostgreSQL full-text search fused with reciprocal-rank fusion, then the same reranker.
  No in-memory index to rebuild or to go stale across processes (rank-bm25 and chromadb are no longer dependencies).
* Rate limiting and the daily budget moved into PostgreSQL, so they are exact across workers and restarts
  (tested with 16 parallel threads, and mutation-tested: breaking the SQL makes the tests fail).
* Startup verifies the embedding model and vector size against what is stored, and refuses to start on a mismatch
  instead of silently mixing models. `scripts/reindex.py --all --reset-vectors` is the supported way to change them.

## A real bug found and fixed on the way

A shared approximate (HNSW) index with a per-account filter returns the nearest chunks of *all* accounts and then
discards most of them: in a load test with 20 accounts and 30,000 chunks, **60 of 60 searches returned fewer than the
20 requested rows**. Searches are now exact per account (about 1 ms at 1,500 chunks, 21 ms at 30,000), the index is
only used for very large accounts, and an exact fallback runs whenever it under-delivers. Covered by a regression test.

## Production hardening

* `/health` (liveness) vs `/ready` (database + migrations); database outages answer `503` with `Retry-After`
  instead of crashing, and the pool recovers by itself (tested by terminating the database under a running server).
* Structured JSON logs with a request id (also returned as `X-Request-ID`); access log records paths only.
* Connection pool with statement timeout and dead-connection detection; no server-side prepared statements; connection
  strings that go through a transaction-mode pooler are detected and the statement-timeout startup option is skipped
  (unit-tested; not tried against a real pooler).
* Audit log for sign-ins, failures, password changes, uploads, deletions; account disable flag; last sign-in time.
* Several uvicorn workers (`WEB_CONCURRENCY`); `start.sh` migrates first and exits if any process dies (tested).
* Container: no volume, health check requires a ready database; `docker-compose.yml` (pgvector image),
  `render.yaml` (managed database, no disk), CI with a pgvector service.
* Operations tooling: `deploy/backup.sh` + `restore.sh` (a real backup/restore round trip was verified, including
  login and search on the restored database), `scripts/maintenance.py`, `delete_account.py`, `migrate.py`,
  `migrate_from_sqlite.py` (moves accounts with working passwords, history and documents), `OPERATIONS.md`.

## Verification done

* ~210 tests against **real PostgreSQL 16 + pgvector** (fake models, mocked LLM), plus `tests/e2e/smoke.py`: real
  sockets, two uvicorn workers booting simultaneously, upload/ask/stream/page-image, full process restart with data
  intact, and a database outage with recovery.
* Real `start.sh` run (migrate, 2 API workers, Streamlit) with health checks and clean SIGTERM shutdown.
* Backup → restore into a new database → login, original file, dense and keyword search all work.

## Not verified — please run these yourself

* Tested on PostgreSQL 16.15 with pgvector 0.6.0. Newer pgvector (0.8+, used by most managed providers) enables
  iterative index scans, which the code uses automatically for very large accounts, but that path ran here only through
  the fallback.
* **Docker build, `docker compose`, the Render blueprint, the CI workflow** and managed providers (Neon, Supabase, RDS)
  were not run; `CREATE EXTENSION vector` needs a role that is allowed to do it.
* Real embedding models and a real LLM call (tests use fakes); OCR; the UI in a real browser.
* Load test: 30,000 chunks / 20 accounts on one small machine. Test with your own data volume before launch.
* Files are stored unencrypted in the database: enable encryption at rest at your provider if documents are sensitive.
* Single PostgreSQL primary: high availability / failover is the provider's job, not this repository's.

---

# Release 2 (earlier): review fixes, features, evaluation

## Do this first (only you can)

1. **Revoke the Groq API key that was inside the zip's `.env`** and create a new one. The new zip contains no
   `.env`; copy `.env.example` to `.env` and fill it in.
2. **Generate a real `AUTH_SECRET`.** The old value was the placeholder `replace_with_a_long_random_secret`,
   which let anyone forge login tokens. The API now refuses to start with a weak one.
3. The old zip also held `data/accounts.sqlite3` (one real e-mail address) plus uploads and vector data.
   None of that is included here. Existing databases migrate automatically on first start.

## Security

- Weak/placeholder secret → API refuses to start. Tokens now carry a version; **changing a password signs out
  every other session**. Login rate limiting (per email and per client address); same error and same work for
  unknown email / wrong password; password length 10–256.
- Uploads: extension allow-list, content sniffing, size cap read without loading oversized bodies, sanitized file
  names (also protects the `source::p1::c0` chunk-id format), failed re-uploads keep the old copy.
- `DELETE` was possible in demo mode: now blocked. Demo traffic lives in its own `demo` namespace.
- CORS restricted, security headers added, request-size guard, container runs as non-root with the API bound to
  localhost, prompts treat retrieved text as untrusted.
- Optional URL ingestion with SSRF protection (**off by default**).

## Bugs fixed

- `/upload` was `async` but did heavy CPU work, **blocking the whole server**; now runs in the thread pool.
  (Correction to my earlier review: the other endpoints already ran in the thread pool; the real cost there was
  long retry sleeps, now capped by `LLM_DEADLINE`.)
- The daily budget was one in-memory counter for the entire app (reset on restart); now **per user, persisted**,
  with an optional global cap.
- Citations listed every retrieved passage; now only the ones the answer used (with a fallback when the model
  forgot to cite).
- Refusal detection was a fragile substring match; now normalized.
- `eval` keyword extraction dropped numbers shorter than 3 characters, so "24 days" was graded on just "days".
- README said `EVAL_USER_ID` was supported, but it wasn't; it is now (eval, retrieval eval, threshold tuning).
- `st.pdf` needs `streamlit[pdf]`, which `requirements.txt` did not install; fixed.
- An expired or revoked session now returns to the sign-in screen instead of showing a backend error.
- `tests/test_agent.py` replaced `app.store` in `sys.modules` for the whole test session; removed.
- Agent: a failed rewrite/route LLM call falls back instead of failing the request; history is validated (it used
  to crash with a KeyError on malformed input).

## Performance

- PDFs opened once instead of once per page; embedder, reranker and Chroma client are shared singletons (not one
  per user); BM25 is rebuilt lazily instead of on every upload; stores are created under a lock; LLM calls have a
  total deadline.

## Retrieval quality

- Structure-aware chunking (headings), tables as their own chunks with the header row repeated and the duplicate
  plain-text copy removed, document title + section heading embedded with every passage, neighbour-chunk context,
  automatic search inside a named document, configurable query prefix for bge/e5 models, per-model collections.

## Features

Streaming answers · click-through citations with highlighted page · confidence label · 👍/👎 feedback ·
DOCX/TXT/MD/HTML ingestion · optional URL ingestion · change password · usage analytics panel ·
`scripts/reindex.py`, `reset_password.py`, `export_feedback.py`.

## Evaluation

95 % Wilson confidence intervals, paired sign test between modes, optional LLM judge (`JUDGE=1`), unanswerable
question merging + warning when there are fewer than 20, template for hand-written unanswerable questions.

## Code quality / ops

`black` + `ruff` clean; `streamlit_app.py` went from 1,348 to 83 lines (logic in `ui/`); SQLite schema now has
versioned migrations; ~175 offline tests (was 10) incl. real Chroma/SQLite, API, auth, SSRF, streaming and a
headless UI run; CI workflow; pinned version ranges + a tested-versions snapshot; Dockerfile (non-root,
health check), `start.sh` (container exits if either process dies — tested), `render.yaml` with a persistent disk.

## Not verified — please run these yourself

- **Real models and a real LLM.** The sandbox could not download the embedding/reranker models or call a provider,
  so tests use fakes. Streaming was tested against the provider's *documented* SSE format, not a live endpoint.
  Run the app once end to end with your key.
- **No benchmark numbers.** I did not run the eval, tune `REFUSAL_THRESHOLD`, or compare embedding models.
  The README results table is empty on purpose. `-3.0` is still just the original default.
- **OCR** (Tesseract/Poppler not installed here), the **Docker build**, the **Render deploy**, and the UI in a **real
  browser** (only Streamlit's headless test runner was used) were not exercised.
- Existing indexed documents keep working, but re-upload (or `python -m scripts.reindex`) to get the new chunking.

## Deliberately not done

- **E-mail verification / e-mail password reset** — needs a mail provider and domain. Added change-password and an
  operator reset CLI instead.
- **Postgres / object storage** — SQLite + local disk stays single-instance (documented).
- **A torch-inclusive lock file** — generate `requirements.lock` in your own environment.
- **Switching the default embedding model** — only worth doing after you measure it on your own questions.

## Deployment hardening (Render + Streamlit Cloud)
* API-only container by default (`start.sh` binds `0.0.0.0:$PORT`); `RUN_UI=1` restores all-in-one.
* ONNX models via fastembed (same models, no PyTorch) instead of sentence-transformers: far less RAM.
* One uploaded document is indexed at a time; PDF page and OCR caps; clear messages for oversized or
  password-protected PDFs.
* Streaming keepalive pings so proxies do not drop slow answers.
* Database pool sized for serverless Postgres (keepalives, recycling, longer cold-start wait).
* UI: backend wake-up wait, explicit connection error (no more skipped sign-in when the backend is
  unreachable), `API_URL` from env or Streamlit secrets, fail-safe login cookie.
* Split requirements: `requirements.txt` (UI) and `requirements-api.txt` (backend).
