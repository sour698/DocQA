# DocQA — Secure RAG Document Assistant

Ask questions about your own documents and get answers with **page-level citations**, or an honest
"I don't know" when the documents do not contain the answer.

- **Formats:** PDF (text, scanned pages via OCR, tables), Word `.docx`, `.txt`, `.md`, `.html`; optional URL ingestion.
- **Retrieval:** dense embeddings + BM25 → reciprocal-rank fusion → cross-encoder rerank → refusal threshold.
  Structure-aware chunks (split at headings, tables kept as their own chunks), document/section titles
  added to every passage, neighbouring chunks added as context, and automatic search inside the one
  document a question names.
- **Answers:** streamed as they are written; every citation is validated (optional LLM support audit);
  only the passages the answer actually cites are listed; a retrieval-confidence label; 👍/👎 feedback.
- **Click-through citations:** "Show this page" renders the cited PDF page with the passage highlighted.
- **Agent mode:** follow-up questions, summaries, small talk, optional general-knowledge answers (always labelled).
- **One database for everything:** PostgreSQL + pgvector holds accounts, the original files, chunks, vectors,
  budgets, rate limits, analytics and the audit trail. App containers keep no state, so they can be
  restarted, replaced or scaled out; one `pg_dump` backs up the whole system.
- **Accounts:** per-user isolation, scrypt password hashes, signed expiring tokens that are revoked when the
  password changes, login rate limiting shared across processes, per-user daily question budget, audit log.
- **Evaluation:** naive vs hybrid+rerank on your own questions, with 95 % confidence intervals, an optional
  LLM judge, a paired significance test, and an evidence-validated question builder.

## 1. Install

Use Python 3.11 or 3.12 and a PostgreSQL 14+ server **with the pgvector extension**.

```bash
docker compose up -d db              # easiest: PostgreSQL + pgvector (+ a docqa_test database)
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                 # Windows: copy .env.example .env
```

No Docker? Install PostgreSQL yourself plus pgvector (Debian/Ubuntu: `apt install postgresql postgresql-16-pgvector`),
or use a free managed database (Neon, Supabase, Render) and paste its connection string into `DATABASE_URL`.
Use the **direct** connection string (Neon: hostname *without* `-pooler`; Supabase: direct, or the *Session* pooler
on an IPv4-only network, never the port-6543 transaction pooler). Step-by-step: `OPERATIONS.md` → "Managed database".

Edit `.env`:

| Setting | What to put |
|---|---|
| `DATABASE_URL` | `postgresql://user:password@host:5432/dbname` (add `?sslmode=require` for managed databases). The default matches `docker compose up -d db`. |
| `LLM_API_KEY` | A **new** key from your provider (e.g. https://console.groq.com/keys). Never commit `.env`. |
| `AUTH_SECRET` | A long random value: `python -c "import secrets; print(secrets.token_urlsafe(48))"`. **The API refuses to start** if it is empty, shorter than 32 characters, or a placeholder. |
| `LLM_MODEL`, `LLM_BASE_URL` | Your provider's current OpenAI-compatible model/endpoint. |

The schema is created and upgraded automatically at start (`python -m scripts.migrate` does it by hand).

Coming from the earlier SQLite/Chroma version? `python -m scripts.migrate_from_sqlite --sqlite data/accounts.sqlite3 --uploads data/uploads`
moves accounts (passwords keep working), history and documents into PostgreSQL.

### OCR for scanned PDFs (optional)

Install the **Tesseract** and **Poppler** programs and put them on `PATH` (Windows: `tesseract.exe`, and
Poppler's `Library\bin`; Debian/Ubuntu: `apt install tesseract-ocr poppler-utils`). Without them, normal
text and tables still work; a scanned-only PDF is rejected with a message explaining what to install.

## 2. Run

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000     # terminal 1  (add --workers 2 for more throughput)
streamlit run streamlit_app.py                         # terminal 2
```

Open the Streamlit URL, create an account (email + a password of at least 10 characters, typed twice) and upload documents.
The look of the app (dark theme, upload limit shown) comes from `.streamlit/config.toml`, so start Streamlit from the project folder. Forgot a password? There is no e-mail
reset flow; the operator runs `python -m scripts.reset_password you@example.com`.
Day-2 operations (backups, upgrades, scaling, monitoring) are in **OPERATIONS.md**.

## 3. Tests

```bash
docker compose up -d db                                  # the tests use the docqa_test database
pytest -q                                                # ~260 tests, offline except for PostgreSQL
python -m tests.e2e.smoke                                # real sockets, 2 uvicorn workers, restart, DB outage
ruff check . && black --check .
```

Tests use fake models and a mocked LLM but a **real** PostgreSQL + pgvector (set `TEST_DATABASE_URL` for another
server; its database name must end in `_test` because the schema is wiped). Without a reachable database the
database tests are skipped with instructions. CI (`.github/workflows/ci.yml`) runs everything against a
pgvector service container on Python 3.11 and 3.12. Exact versions of the last passing run: `requirements-tested.txt`.

## 4. Evaluate (this is the part that makes the project credible — fill in your numbers)

```bash
python -m scripts.make_sample_corpus                 # fictional Acme PDFs (smoke test only)
python -m scripts.index_folder data/sample_docs --user-id default
python -m eval.retrieval_eval eval/questions.sample.json     # retrieval only: no LLM calls
python -m scripts.tune_threshold eval/questions.sample.json  # pick REFUSAL_THRESHOLD from data
JUDGE=1 python -m eval.run_eval eval/questions.sample.json   # full run + LLM judge
```

For a real benchmark use your own permitted documents:

```bash
python -m eval.build_real_corpus ./my_docs --count 120 --unanswerable eval/unanswerable.mine.json
EVAL_USER_ID=<account id> python -m scripts.index_folder ./my_docs --user-id <account id>
EVAL_USER_ID=<account id> JUDGE=1 python -m eval.run_eval eval/questions.real.json
```

- The account id is the first 24 hex characters of `SHA-256(lowercased email)`.
- Answerable questions are only kept when their verbatim evidence quote is on the cited page.
- **Write 20–30 unanswerable questions by hand** (`eval/unanswerable.template.json` shows the format) and
  check that no document answers them. Without them the hallucination rate is meaningless.
- With ~100 questions, a few points of difference can be noise. The harness prints 95 % Wilson intervals
  and a paired sign-test p-value between the two modes; quote those, not bare percentages.
- The keyword check is a cheap proxy. `JUDGE=1` adds an LLM judge (use a different model family than the
  one under test if you can) — and still read a random sample of answers yourself.
- 👎 feedback can seed new questions: `python -m scripts.export_feedback`.

### Results

> **Not yet measured.** No benchmark numbers ship with this repository on purpose. After running the
> commands above, paste `eval/results.md` and `eval/retrieval_results.md` here, with the model names,
> corpus size and date.

| Mode | n answerable / unanswerable | hit@k | accuracy (keywords / judge) | false refusal | hallucination |
|---|---|---|---|---|---|
| naive |36/18 |100.0 |90.4-100.0 |0.0-9.6 |0.5-14.2 |
| hybrid_rerank |36/18 |100.0 |81.9-98.5  |0.5-14.2 |0.5-14.2 |

## 5. Configuration highlights

Everything is in `.env.example` with comments. The ones worth knowing:

- `NEIGHBORS` (default 1): neighbouring chunks added around each hit; `0` disables.
- `EMBED_MODEL` / `EMBED_DIM` / `QUERY_PREFIX`: try e.g. `BAAI/bge-small-en-v1.5` with
  `QUERY_PREFIX="Represent this sentence for searching relevant passages: "`, then
  `python -m scripts.reindex --all --reset-vectors` and compare on your eval set. The API refuses to start
  when the model or vector size no longer matches what is stored, so vectors are never mixed. (A model with
  a different vector size, e.g. 768, also needs `EMBED_DIM=768`.)
- `EXACT_SEARCH_MAX_CHUNKS` (default 100000): accounts up to this size get exact vector search (about 1 ms for
  1,500 chunks, 20 ms for 30,000); larger ones use the HNSW index with an exact fallback.
- `DAILY_LLM_LIMIT` (per user) and `GLOBAL_DAILY_LLM_LIMIT` (whole app): stored in PostgreSQL, so they are exact across processes and restarts.
- `ALLOW_REGISTRATION=0` closes sign-ups once your users exist.
- `LOG_QUERIES=0` stops storing question text (counts and latency are still recorded).

## 6. Deploy

The application container is **stateless**: all state is in PostgreSQL (`DATABASE_URL`). That is what makes
restarts, rolling deploys and several replicas safe.

- **Docker Compose (one server):** put `LLM_API_KEY`, `AUTH_SECRET`, `POSTGRES_PASSWORD` in `.env`, then
  `docker compose up -d --build` → http://localhost:7860. Database data lives in the `pgdata` volume.
- **Docker image alone:** `docker build -t docqa .` and run it with `--env-file .env` and a `DATABASE_URL`.
  Non-root user; the API listens on `127.0.0.1` only; it applies migrations before serving; if either the UI or
  API dies the container exits so it is restarted; the health check requires the database to be reachable.
- **Render:** `render.yaml` creates a managed PostgreSQL (pgvector included) plus the web service, wires
  `DATABASE_URL` and generates `AUTH_SECRET`. Set `LLM_API_KEY` in the dashboard; pick a database plan with backups.
- Put HTTPS in front of anything public (Render does). Set `TRUST_PROXY=1` only when exactly one proxy you
  control sits in front of the app — it is then used to rate-limit per visitor.
- **Public demo:** `DEMO_MODE=1` (no accounts, uploads or deletes; its own `demo` namespace that real accounts can
  never reach). Also set `GLOBAL_DAILY_LLM_LIMIT` so a demo cannot burn your API quota.
- **Scaling:** raise `WEB_CONCURRENCY` (each API worker loads the models, roughly 0.5 GB RAM) or run more
  containers; keep `DB_POOL_MAX` × workers × containers below PostgreSQL's `max_connections` (or use a session-mode pooler).
  Transaction-mode poolers (Neon `-pooler` hosts, Supabase port 6543) are detected: the app then skips the
  statement timeout, which they cannot accept. This path is covered by unit tests only, not tried against a real pooler.

## 7. Security notes

- **Rotate any key that was ever in a zip, chat or repository.** Keep `.env` out of version control (`.gitignore`
  and `.dockerignore` already do; zipping a folder by hand bypasses `.gitignore`, so check before sharing).
- Passwords: scrypt, 10–256 characters; the same error for unknown email and wrong password, with equal work done;
  logins limited per email and per client address; changing a password signs out every other session.
- Retrieved text is treated as untrusted in the prompts (basic prompt-injection hardening, not a guarantee).
- Uploads: allow-listed extensions, content sniffing, size cap read without loading oversized bodies, sanitized
  file names; a document and its chunks are written in one transaction, so a failed re-upload changes nothing.
- Every database query is scoped by account id; tests cover cross-account reads, searches and deletes.
- `audit_log` records sign-ins, failures, password changes, uploads and deletions (no passwords, no e-mail
  addresses); `scripts/maintenance.py` prunes old logs; `scripts/delete_account.py` erases an account and its data.
- **URL ingestion is off by default** (`ALLOW_URL_INGEST=1` to enable). It validates every hop and only fetches
  public IPs, but a hostile DNS server could still try DNS rebinding — if you enable it publicly, also restrict
  the container's outbound network.
- Requests with a `Content-Length` over the upload limit are refused early; a chunked upload without a length is
  only bounded by the endpoint's read cap, so also set a body-size limit on your reverse proxy.
- Question logging (`/stats`, feedback) stores question text unless `LOG_QUERIES=0`. Tell your users. Uploaded files are stored
  in the database **unencrypted**: use disk/database encryption at rest from your provider if you hold sensitive documents.
- Do not upload confidential or copyrighted documents unless you have permission and have reviewed your LLM
  provider's data-handling terms. The bundled Acme corpus is fictional and for smoke tests only.

## 8. Project layout

```
app/        FastAPI backend: main, auth, limits, db (pool + migrations), migrations/*.sql, ingest, store (pgvector),
            rag, agent, llm, render, fetch, analytics, logging_config
ui/         Streamlit pieces: api client, panels, components, styles
streamlit_app.py   dashboard entry point
eval/       metrics, judge, run_eval, retrieval_eval, build_real_corpus, question sets
scripts/    migrate, migrate_from_sqlite, index_folder, reindex, maintenance, delete_account, reset_password,
            export_feedback, tune_threshold, make_sample_corpus
deploy/     backup.sh, restore.sh, initdb/
tests/      test suite (+ tests/e2e/smoke.py)
```

See `OPERATIONS.md` for the runbook and `CHANGES.md` for what changed and what has **not** been verified.
