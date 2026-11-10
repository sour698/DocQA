#!/usr/bin/env bash
# Default (Render API service): run ONLY the API, listening on the platform's $PORT.
# RUN_UI=1 (all-in-one container): API on 127.0.0.1:8000 plus the Streamlit UI on $PORT.
# If any process dies the container exits, so the platform restarts it.
set -u

# Wait for PostgreSQL and apply pending migrations BEFORE serving. A bad DATABASE_URL or a failed
# migration stops the container here, with the reason in the logs.
python -m scripts.migrate || exit 1

PORT="${PORT:-10000}"
WORKERS="${WEB_CONCURRENCY:-1}"

if [ "${RUN_UI:-0}" != "1" ]; then
    exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --workers "$WORKERS" \
        --no-access-log --timeout-keep-alive 75 --timeout-graceful-shutdown 20
fi

export API_URL="${API_URL:-http://127.0.0.1:8000}"
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers "$WORKERS" \
    --no-access-log --timeout-graceful-shutdown 20 &
api_pid=$!
streamlit run streamlit_app.py --server.port "$PORT" --server.address 0.0.0.0 --server.headless true &
ui_pid=$!
trap 'kill "$api_pid" "$ui_pid" 2>/dev/null' TERM INT
wait -n
code=$?
echo "A DocQA process exited (code $code); stopping the container so it gets restarted." >&2
kill "$api_pid" "$ui_pid" 2>/dev/null
wait 2>/dev/null
exit $(( code == 0 ? 1 : code ))
