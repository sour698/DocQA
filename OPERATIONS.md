# Operations runbook

Everything durable is in PostgreSQL. The application containers can be killed and replaced at any time.

## What to watch

| Signal | Where | Healthy |
|---|---|---|
| Liveness | `GET /health` on the API (port 8000, localhost only) | `{"status":"ok"}` (does not touch the database) |
| Readiness | `GET /ready` | `{"status":"ready"}`; `503` = database down or migrations pending |
| Container health | Docker `HEALTHCHECK` (UI + `/ready`) | `healthy` |
| Logs | stdout, one JSON object per line (`LOG_JSON=1`) | every request has a `request_id`; the same id is returned in the `X-Request-ID` header, so a user can quote it |
| Errors to alert on | log lines with `"level":"ERROR"` or status 5xx; message `Database unavailable` | none |
| Security | table `audit_log` (`login_failed`, `rate_limited`, `password_changed`, `delete`...) | few `login_failed` / `rate_limited` |
| Cost / abuse | `budget` table, `GLOBAL_DAILY_LLM_LIMIT` | below the limits |

Example query for suspicious sign-in activity:

```sql
SELECT date_trunc('hour', ts) AS hour, ip, count(*) FROM audit_log
WHERE event IN ('login_failed', 'rate_limited') AND ts > now() - interval '1 day'
GROUP BY 1, 2 HAVING count(*) > 20 ORDER BY 3 DESC;
```

## Backups (do this before you have real users)

```bash
DATABASE_URL=postgresql://... ./deploy/backup.sh /path/to/backups     # custom-format dump, verified readable
KEEP_DAYS=30 ./deploy/backup.sh /path/to/backups                      # also prune dumps older than 30 days
```

* One dump contains accounts, original files, chunks, vectors, logs. Schedule it daily and copy the files
  **off** the database server.
* On managed PostgreSQL also enable the provider's automatic backups / point-in-time recovery.
* **Test a restore** into a scratch database every so often:

```bash
createdb docqa_scratch
DATABASE_URL=postgresql://.../docqa_scratch ./deploy/restore.sh backups/docqa-<timestamp>.dump
```

Restore into production (stop the app first):
`DATABASE_URL=postgresql://.../docqa ./deploy/restore.sh <dump>`, then start the app.

## Upgrades

1. Back up.
2. Deploy the new image. `start.sh` runs `python -m scripts.migrate` first (waits for the database, takes an
   advisory lock, applies pending `app/migrations/*.sql` in one transaction). A failed migration rolls back and
   the container stops with the reason in the log.
3. Prefer to migrate by hand? Set `AUTO_MIGRATE=0`; the API then refuses to start while migrations are pending.
   `python -m scripts.migrate --status` shows what is pending.

Migrations are forward-only. To add one, create the next numbered file (`0003_name.sql`); never edit an applied one.

## Changing the embedding model or chunking

```bash
# 1. set EMBED_MODEL (+ EMBED_DIM and QUERY_PREFIX if the model needs them) in the environment
# 2. stop the API (searches would see an empty index while it rebuilds)
python -m scripts.reindex --all --reset-vectors      # new model / new vector size
python -m scripts.reindex --all                      # only CHUNK_WORDS / CHUNK_OVERLAP changed
# 3. start the API
```

The API refuses to start if the configured model or vector size differs from what is stored, so a half-done
change cannot silently degrade search quality. Original files are never touched by a reindex.

## Routine jobs

| Job | Command | How often |
|---|---|---|
| Delete old logs (90 days) | `python -m scripts.maintenance` | daily |
| Backup | `./deploy/backup.sh` | daily |
| Reset a forgotten password | `python -m scripts.reset_password user@example.com` | on request |
| Erase an account and all its data | `python -m scripts.delete_account user@example.com` | on request |
| Disable an account without deleting it | `UPDATE users SET is_active = false WHERE email = '...';` | on request |
| Sign everyone out (e.g. after leaking `AUTH_SECRET`) | rotate `AUTH_SECRET` and restart | emergencies |

## Capacity and tuning

* Connections: each API worker opens up to `DB_POOL_MAX` (default 20). Keep `workers × DB_POOL_MAX × containers`
  below PostgreSQL `max_connections`. If you must use a pooler, use a session-mode one. Transaction-mode poolers are
  detected and tolerated (the statement timeout is skipped) but were not tried against a real one.
* Memory: each API worker holds the embedding + reranker models (~0.5 GB).
* Vector search is exact per account up to `EXACT_SEARCH_MAX_CHUNKS` chunks (about 1 ms at 1,500 chunks, 20 ms
  at 30,000); there is no index to tune for typical accounts.
* Uploads are capped by `MAX_UPLOAD_MB` (default 10) and stored in the database; at roughly 10 MB per document
  plan database storage accordingly (`SELECT pg_size_pretty(pg_total_relation_size('documents'))`).
* Slow statements are cancelled after `DB_STATEMENT_TIMEOUT_MS` (30 s) and answered with `503`.

## Speed: what it depends on

Run `python -m scripts.db_latency`. It prints the median network round trip to your database. Every database
call costs at least one round trip, so distance matters more than anything else: a database in another country
makes everything feel slow. Create the database in the region nearest to where the app runs.

What the app already does about it (measured with a simulated 100 ms round trip: a click went from 2.0 s to 0.3 s,
asking a question from 10 s to 1.9 s):

* single reads and writes use autocommit (1 round trip instead of 3-4); pooled connections are health-checked
  only after sitting idle (`DB_CHECK_IDLE_SECONDS`);
* a verified login token is remembered for `AUTH_CACHE_SECONDS` (10 s), so a request does not ask the database who
  you are. Trade-off: a password change or disabled account reaches OTHER app processes after at most that long
  (the process that handled the change forgets immediately). Set it to 0 to turn the cache off;
* the dashboard keeps the document list, config and usage statistics for the session instead of fetching them on every
  click (the list refreshes on upload/delete and every 30 s; usage statistics load only when you switch them on);
* retrieval fetches its context in one query; uploads send their statements pipelined;
* the AI models load in the background when the API starts (`WARM_MODELS`), so the first question is not the slow one;
* table detection in PDFs is limited to `TABLE_EXTRACT_SECONDS` per document (the rest is indexed as text).

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Container exits right after start | Read the first log lines: bad `DATABASE_URL` ("Cannot reach the database"), weak `AUTH_SECRET`, or a failed migration |
| `permission denied to create extension "vector"` | The database role may not create extensions. Ask the provider to enable pgvector, or run `CREATE EXTENSION vector;` once as a privileged user |
| `EMBED_DIM=... but the database holds ...` / `stored vectors were made with ...` | You changed the model: follow "Changing the embedding model" above |
| Everything feels slow | Run `python -m scripts.db_latency`; above ~100 ms the database region is the problem |
| Many `503 ... temporarily unavailable` | Database down, pool exhausted (raise `DB_POOL_MAX` or add capacity) or statements timing out |
| `/ready` is 503 but `/health` is 200 | The app is alive but cannot reach the database or migrations are pending |
| A user says "an answer was wrong" | Ask for the `X-Request-ID` (browser dev tools) and search the logs for it |

## Managed database (Neon or Supabase), step by step

1. **Create the project** (neon.com or supabase.com, free plan is enough to start). Pick the region closest to
   where the app runs. Note the database password.
2. **Get the right connection string**
   * Neon: Connect widget → turn **Connection pooling OFF** → copy the string. The hostname must NOT contain `-pooler`.
   * Supabase: **Connect** → *Direct connection* if your network has IPv6; otherwise *Session pooler*
     (host `aws-…pooler.supabase.com`, **port 5432**, user `postgres.<project-ref>`). Do not use the *Transaction pooler* (port 6543).
   * Keep `sslmode=require` in the string (add `?sslmode=require` if it is missing).
3. **Put it in `.env` as `DATABASE_URL`**, and make sure the password has no unescaped special characters
   (`@ : / ? #` must be URL-encoded, e.g. `@` → `%40`).
4. **Create the schema:** `python -m scripts.migrate`. It creates the `vector` extension (if the role may) and the tables.
   If it says *permission denied to create extension*, enable pgvector in the provider's dashboard (Supabase: Database →
   Extensions → `vector`) and run it again.
5. **Check:** `python -m scripts.migrate --status` prints `none (schema is current)`; start the API; `GET /ready` returns ready.
6. **Mind the limits:** free plans have small storage (about 0.5 GB), and uploaded files are stored in the database,
   so large documents use it up quickly. Neon free computes suspend when idle (the first request after a pause is slow;
   the app waits up to `DB_CONNECT_WAIT` seconds), and Supabase free projects pause after a week of inactivity.
   Both free tiers have limited backups: also run `deploy/backup.sh` (use the *direct* string for `pg_dump`).
