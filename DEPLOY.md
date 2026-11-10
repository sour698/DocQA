# Deploying DocQA: API on Render, UI on Streamlit Community Cloud

The UI and the API are two separate apps. The UI must point at the **API's** address, never at its own.

## 1. Database (Neon)
1. Create a Neon project. Copy the connection string (Dashboard -> Connect).
2. Nothing else: the API creates the `vector` extension and its tables on first start.

## 2. API on Render
1. Push this folder to GitHub (the real `.env` must NOT be in the repo; `.env.example` is fine).
2. Render -> New -> Blueprint, pick the repo (it reads `render.yaml`), or New -> Web Service -> Docker.
3. Set the environment variables: `DATABASE_URL`, `LLM_API_KEY`, `AUTH_SECRET`, and
   `CORS_ORIGINS` (your Streamlit address). Keep `WEB_CONCURRENCY=1` and `TRUST_PROXY=1`.
4. Use the **Starter** plan (512 MB) or larger. The free plan sleeps when idle; the UI waits up to
   75 s for it to wake, but it is slow and unsuitable for real use.
5. Check `https://<your-service>.onrender.com/health` -> `{"status":"ok"}` and `/ready` -> database ok.
   `/config` must return JSON. If any of them shows a web page, you are looking at the wrong service.

## 3. UI on Streamlit Community Cloud
1. New app -> same repo, main file `streamlit_app.py`. It installs `requirements.txt` (light, UI only).
2. App -> Settings -> Secrets:  `API_URL = "https://<your-service>.onrender.com"`
3. Open the app. First load after idle can take up to a minute while Render wakes.

## Troubleshooting
| Symptom | Cause |
|---|---|
| "Could not connect to the DocQA server" | `API_URL` wrong, or API down; open `<API_URL>/health` in a browser |
| "...is not the DocQA API" | `API_URL` points at the Streamlit app or another site |
| Sign-in works but refresh logs you out | cookie component blocked by the browser (private mode, blockers) |
| "server is busy indexing" (503) | another document is being indexed; retry in a minute |
| PDF refused | over 300 pages (`MAX_PDF_PAGES`) or password-protected |
| API restarts / killed | out of memory: keep `WEB_CONCURRENCY=1`; do not set `MODEL_BACKEND=torch` on 512 MB |
