"""End-to-end smoke test over real sockets: several uvicorn workers + real PostgreSQL.

    python -m tests.e2e.smoke            # needs a reachable PostgreSQL (see tests/conftest.py)

It starts the API twice (to prove that nothing lives in process memory), checks that workers share
state, that data survives a restart, that concurrent startups migrate safely, and that the
database/readiness failure modes behave.
"""

import io
import json
import os
import signal
import subprocess
import sys
import time

import psycopg
import requests
from reportlab.pdfgen import canvas

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
BASE_URL = os.getenv("TEST_DATABASE_URL", "postgresql://docqa:docqa@127.0.0.1:5432/docqa_test")
E2E_DB = "docqa_e2e_test"
E2E_URL = BASE_URL.rsplit("/", 1)[0] + "/" + E2E_DB


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        raise SystemExit(1)


def fresh_database():
    admin = psycopg.connect(BASE_URL, autocommit=True)
    admin.execute(f"DROP DATABASE IF EXISTS {E2E_DB} WITH (FORCE)")
    admin.execute(f"CREATE DATABASE {E2E_DB}")
    admin.close()


def start(workers=2, extra_env=None):
    env = {
        **os.environ,
        "TEST_DATABASE_URL": E2E_URL,
        "AUTH_SECRET": "e2e-secret-that-is-long-enough-0123456789-abcdefghij",
        "LOG_JSON": "1",
        **(extra_env or {}),
    }
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.e2e.fake_app:app",
            "--port",
            str(PORT),
            "--workers",
            str(workers),
            "--no-access-log",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.time() + 40
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit("server died:\n" + proc.stdout.read())
        try:
            if requests.get(f"{BASE}/ready", timeout=1).status_code == 200:
                return proc
        except requests.RequestException:
            time.sleep(0.3)
    proc.kill()
    raise SystemExit("server did not become ready")


def stop(proc):
    proc.send_signal(signal.SIGTERM)
    try:
        out = proc.communicate(timeout=25)[0]
    except subprocess.TimeoutExpired:
        proc.kill()
        out = proc.communicate()[0]
    return out


def pdf_bytes():
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 750, "LEAVE POLICY")
    c.drawString(72, 720, "Employees receive 24 days of annual leave each year.")
    c.save()
    return buf.getvalue()


def main():
    fresh_database()
    proc = start(
        workers=2
    )  # two workers boot at the same moment and migrate under an advisory lock
    try:
        check(
            requests.get(f"{BASE}/ready").json() == {"status": "ready"},
            "ready after concurrent startup",
        )
        r = requests.post(
            f"{BASE}/auth/register",
            json={"email": "e2e@example.com", "password": "a-very-long-password"},
        )
        check(r.status_code == 200, "register")
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        r = requests.post(
            f"{BASE}/upload",
            headers=h,
            files={"file": ("leave.pdf", pdf_bytes(), "application/pdf")},
        )
        check(r.status_code == 200 and r.json()["chunks"] >= 1, "upload a PDF")

        pids = set()
        for _ in range(30):  # many requests -> served by both workers; all see the same data
            r = requests.get(f"{BASE}/documents", headers=h)
            (
                check(r.json() == {"documents": ["leave.pdf"]}, "document visible")
                if _ in (0, 29)
                else None
            )
            pids.add(r.headers.get("x-request-id"))
        a = requests.post(
            f"{BASE}/ask", headers=h, json={"question": "How many days of annual leave?"}
        ).json()
        check(not a["refused"] and a["citations"][0]["page"] == 1, "ask returns a cited answer")

        lines = requests.post(
            f"{BASE}/agent/ask/stream",
            headers=h,
            stream=True,
            json={"question": "How many days of annual leave?"},
        ).iter_lines()
        events = [json.loads(x) for x in lines if x]
        check(
            events[-1]["type"] == "final" and any(e["type"] == "token" for e in events),
            "streaming works",
        )
        page = requests.get(
            f"{BASE}/documents/leave.pdf/page/1",
            headers=h,
            params={"quote": "24 days of annual leave"},
        )
        check(
            page.status_code == 200 and int(page.headers["x-highlights"]) >= 1,
            "page image with highlight",
        )
        stats = requests.get(f"{BASE}/stats", headers=h).json()
        check(stats["questions"] >= 2, "analytics recorded the questions")
        out = stop(proc)
        check('"request_id"' in out and "started" in out, "JSON logs with request ids were written")
        check("Traceback" not in out, "no tracebacks in the server log")
    finally:
        if proc.poll() is None:
            proc.kill()

    proc = start(workers=2)  # a brand-new process set: everything must come from the database
    try:
        r = requests.post(
            f"{BASE}/auth/login",
            json={"email": "e2e@example.com", "password": "a-very-long-password"},
        )
        check(r.status_code == 200, "login works after a full restart")
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        check(
            requests.get(f"{BASE}/documents", headers=h).json() == {"documents": ["leave.pdf"]},
            "documents survived the restart",
        )
        check(
            requests.get(f"{BASE}/documents/leave.pdf/file", headers=h).content.startswith(
                b"%PDF-"
            ),
            "original file survived",
        )

        # database outage: liveness stays up, readiness and data calls fail cleanly (503, no crash)
        admin = psycopg.connect(BASE_URL, autocommit=True)
        admin.execute(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='{E2E_DB}'"
        )
        admin.execute(f"ALTER DATABASE {E2E_DB} WITH ALLOW_CONNECTIONS false")
        admin.execute(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='{E2E_DB}'"
        )
        time.sleep(0.5)
        check(
            requests.get(f"{BASE}/health").status_code == 200,
            "liveness stays up while the database is down",
        )
        check(
            requests.get(f"{BASE}/ready").status_code == 503,
            "readiness reports 503 while the database is down",
        )
        r = requests.get(f"{BASE}/documents", headers=h)
        check(
            r.status_code == 503, "data calls answer 503 (not a crash) while the database is down"
        )
        admin.execute(f"ALTER DATABASE {E2E_DB} WITH ALLOW_CONNECTIONS true")
        admin.close()
        for _ in range(20):
            if requests.get(f"{BASE}/ready").status_code == 200:
                break
            time.sleep(0.5)
        check(
            requests.get(f"{BASE}/documents", headers=h).status_code == 200,
            "the pool recovers by itself afterwards",
        )
    finally:
        out = stop(proc)
    print("\nALL END-TO-END CHECKS PASSED")


if __name__ == "__main__":
    main()
