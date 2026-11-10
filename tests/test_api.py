import io
import json

import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from app import llm, main

PW = "a-very-long-test-password"


def make_pdf(text: str = "Employees receive 24 days of annual leave each year.") -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 750, "LEAVE POLICY")
    c.drawString(72, 720, text)
    c.save()
    return buf.getvalue()


@pytest.fixture
def client(pg, monkeypatch):
    monkeypatch.setattr(llm, "chat", lambda s, p, temperature=0.0: "Employees get 24 days [1].")
    monkeypatch.setattr(
        llm, "chat_stream", lambda *a, **k: iter(["Employees get ", "24 days [1]."])
    )
    return TestClient(main.app)


def signup(client, email="user@example.com"):
    r = client.post("/auth/register", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def upload(client, headers, name="leave.pdf", data=None, ctype="application/pdf"):
    return client.post(
        "/upload", headers=headers, files={"file": (name, data or make_pdf(), ctype)}
    )


def lines(resp):
    return [json.loads(x) for x in resp.text.splitlines() if x.strip()]


# --------------------------------------------------------------------------- meta
def test_health_ready_version_config(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready"}
    assert client.get("/version").json()["version"] == main.VERSION
    cfg = client.get("/config").json()
    assert cfg["auth_required"] is True and ".docx" in cfg["allowed_extensions"]
    assert cfg["allow_url_ingest"] is False


def test_security_headers_present(client):
    r = client.get("/health")
    assert (
        r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    )


def test_oversized_request_rejected_early(client):
    r = client.post("/health", headers={"content-length": str(500 * 1024 * 1024)})
    assert r.status_code == 413


def test_startup_refuses_weak_secret(monkeypatch):
    monkeypatch.setattr(main.auth, "SECRET", "replace_with_a_long_random_secret")
    with pytest.raises(RuntimeError, match="Refusing to start"):
        with TestClient(main.app):
            pass


# --------------------------------------------------------------------------- auth
def test_protected_routes_need_a_token(client):
    for method, url in [
        ("get", "/documents"),
        ("post", "/ask"),
        ("get", "/stats"),
        ("post", "/feedback"),
    ]:
        assert getattr(client, method)(url).status_code == 401
    assert client.get("/documents", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_register_login_and_validation_errors(client):
    signup(client)
    assert (
        client.post("/auth/login", json={"email": "user@example.com", "password": PW}).status_code
        == 200
    )
    assert (
        client.post(
            "/auth/login", json={"email": "user@example.com", "password": "nope-nope-nope"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/auth/register", json={"email": "user@example.com", "password": PW}
        ).status_code
        == 400
    )
    assert (
        client.post("/auth/register", json={"email": "x@y.zz", "password": "short"}).status_code
        == 400
    )


def test_login_is_rate_limited_per_email(client, monkeypatch):
    signup(client)
    monkeypatch.setattr(main, "email_limiter", main.RateLimiter(3, 900))
    codes = [
        client.post(
            "/auth/login", json={"email": "user@example.com", "password": "wrong-password-1"}
        ).status_code
        for _ in range(5)
    ]
    assert codes == [401, 401, 401, 429, 429]
    # while locked out even the right password is refused (stops guessing, at a small cost)
    assert (
        client.post("/auth/login", json={"email": "user@example.com", "password": PW}).status_code
        == 429
    )


def test_successful_login_resets_the_counter(client, monkeypatch):
    signup(client)
    monkeypatch.setattr(main, "email_limiter", main.RateLimiter(3, 900))
    bad = {"email": "user@example.com", "password": "wrong-password-1"}
    good = {"email": "user@example.com", "password": PW}
    for _ in range(2):
        client.post("/auth/login", json=bad)
    assert client.post("/auth/login", json=good).status_code == 200
    assert [client.post("/auth/login", json=bad).status_code for _ in range(2)] == [401, 401]


def test_registration_can_be_disabled(client, monkeypatch):
    monkeypatch.setattr(main, "ALLOW_REGISTRATION", False)
    r = client.post("/auth/register", json={"email": "u@e.co", "password": PW})
    assert r.status_code == 403


def test_change_password_endpoint_revokes_old_token(client):
    old = signup(client)
    r = client.post(
        "/auth/change-password",
        headers=old,
        json={"old_password": PW, "new_password": "another-long-password"},
    )
    assert r.status_code == 200
    new = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.get("/documents", headers=old).status_code == 401
    assert client.get("/documents", headers=new).status_code == 200
    bad = client.post(
        "/auth/change-password",
        headers=new,
        json={"old_password": "wrong-one-here", "new_password": "x" * 12},
    )
    assert bad.status_code == 400


# --------------------------------------------------------------------------- documents
def test_upload_list_download_delete(client):
    h = signup(client)
    r = upload(client, h)
    assert r.status_code == 200 and r.json()["document"] == "leave.pdf" and r.json()["chunks"] >= 1
    assert client.get("/documents", headers=h).json() == {"documents": ["leave.pdf"]}
    f = client.get("/documents/leave.pdf/file", headers=h)
    assert f.status_code == 200 and f.content.startswith(b"%PDF-")
    assert client.delete("/documents/leave.pdf", headers=h).status_code == 200
    assert client.delete("/documents/leave.pdf", headers=h).status_code == 404  # already gone
    assert client.get("/documents", headers=h).json() == {"documents": []}
    assert client.get("/documents/leave.pdf/file", headers=h).status_code == 404


def test_documents_are_private_between_accounts(client):
    alice, bob = signup(client, "alice@example.com"), signup(client, "bob@example.com")
    upload(client, alice)
    assert client.get("/documents", headers=bob).json() == {"documents": []}
    assert client.get("/documents/leave.pdf/file", headers=bob).status_code == 404
    out = client.post("/agent/ask", headers=bob, json={"question": "how many leave days?"}).json()
    assert out["route"] == "NODOCS"


@pytest.mark.parametrize(
    "name,data,code",
    [
        ("evil.exe", b"MZ", 400),
        ("fake.pdf", b"not really a pdf", 400),
        ("fake.docx", b"plain text", 400),
        ("bin.txt", b"abc\x00\x01\x02", 400),
        ("empty.txt", b"   \n  ", 422),
        ("broken.pdf", b"%PDF-1.4 garbage", 422),
    ],
)
def test_bad_uploads_are_rejected(client, name, data, code):
    h = signup(client)
    assert upload(client, h, name, data).status_code == code
    assert client.get("/documents", headers=h).json() == {"documents": []}


def test_path_traversal_in_filename_is_neutralised(client):
    h = signup(client)
    r = upload(client, h, "../../../evil.txt", b"Totally harmless text about leave days.")
    assert r.status_code == 200 and r.json()["document"] == "evil.txt"
    assert client.get("/documents", headers=h).json() == {"documents": ["evil.txt"]}


def test_upload_size_limit(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_MB", 0)
    h = signup(client)
    assert upload(client, h, "big.txt", b"x" * 10).status_code == 413


def test_failed_reupload_keeps_the_previous_copy(client):
    h = signup(client)
    original = make_pdf()  # reportlab embeds a timestamp, so keep the exact bytes we uploaded
    assert upload(client, h, data=original).status_code == 200
    assert upload(client, h, "leave.pdf", b"%PDF-1.4 garbage").status_code == 422
    assert client.get("/documents/leave.pdf/file", headers=h).content == original
    assert client.get("/documents", headers=h).json() == {"documents": ["leave.pdf"]}


def test_text_docx_html_uploads(client, tmp_path):
    from docx import Document

    h = signup(client)
    d = Document()
    d.add_heading("Travel", 1)
    d.add_paragraph("Book flights 14 days ahead.")
    docx_path = tmp_path / "t.docx"
    d.save(docx_path)
    assert (
        upload(client, h, "notes.md", b"# Notes\n\nSick leave is 12 days.").json()["format"] == "md"
    )
    assert upload(client, h, "page.html", b"<h1>Hi</h1><p>Some words here.</p>").status_code == 200
    assert upload(client, h, "t.docx", docx_path.read_bytes()).json()["format"] == "docx"
    assert client.get("/documents", headers=h).json()["documents"] == [
        "notes.md",
        "page.html",
        "t.docx",
    ]
    assert (
        client.get("/documents/page.html/file", headers=h)
        .headers["content-type"]
        .startswith("text/plain")
    )


def test_page_preview_png_with_highlight(client):
    h = signup(client)
    upload(client, h)
    r = client.get(
        "/documents/leave.pdf/page/1",
        headers=h,
        params={"quote": "24 days of annual leave each year"},
    )
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n" and int(r.headers["x-highlights"]) >= 1
    assert client.get("/documents/leave.pdf/page/9", headers=h).status_code == 404
    upload(client, h, "n.txt", b"hello there friend")
    assert client.get("/documents/n.txt/page/1", headers=h).status_code == 400


def test_url_ingestion_disabled_by_default(client):
    h = signup(client)
    assert (
        client.post("/ingest/url", headers=h, json={"url": "https://example.com/x"}).status_code
        == 403
    )


def test_url_ingestion_when_enabled(client, monkeypatch):
    monkeypatch.setattr(main, "ALLOW_URL_INGEST", True)
    page = b"<html><body><h1>Policy</h1><p>Employees get 24 days of leave.</p></body></html>"
    monkeypatch.setattr(
        main.fetch, "fetch", lambda url: (page, "text/html", "https://example.com/policy")
    )
    h = signup(client)
    r = client.post("/ingest/url", headers=h, json={"url": "https://example.com/policy"})
    assert r.status_code == 200 and r.json()["document"] == "example_com_policy.md"

    def blocked(url):
        raise main.fetch.FetchError("That address is not allowed")

    monkeypatch.setattr(main.fetch, "fetch", blocked)
    r = client.post("/ingest/url", headers=h, json={"url": "http://127.0.0.1/admin"})
    assert r.status_code == 400 and "not allowed" in r.json()["detail"]


# --------------------------------------------------------------------------- questions
def test_ask_agent_and_feedback_flow(client):
    h = signup(client)
    upload(client, h)
    r = client.post("/ask", headers=h, json={"question": "How many days of annual leave?"})
    assert r.status_code == 200
    out = r.json()
    assert "retrieved" not in out and out["citations"][0]["page"] == 1 and out["confidence"]
    assert (
        client.post("/ask", headers=h, json={"question": "x?", "mode": "bogus"}).status_code == 400
    )
    assert client.post("/ask", headers=h, json={"question": "?"}).status_code == 422

    r = client.post(
        "/feedback",
        headers=h,
        json={"question": "q", "answer": "a", "rating": -1, "comment": "wrong"},
    )
    assert r.status_code == 200
    assert (
        client.post("/feedback", headers=h, json={"question": "q", "rating": 5}).status_code == 422
    )
    stats = client.get("/stats", headers=h).json()
    assert stats["questions"] == 1 and stats["feedback_down"] == 1


def test_agent_rejects_malformed_history(client):
    h = signup(client)
    bad = {"question": "hello there", "history": [{"role": "system", "content": "x"}]}
    assert client.post("/agent/ask", headers=h, json=bad).status_code == 422
    assert (
        client.post(
            "/agent/ask", headers=h, json={"question": "hello", "history": [{"content": "x"}]}
        ).status_code
        == 422
    )


def test_ask_stream_ndjson(client):
    h = signup(client)
    upload(client, h)
    r = client.post("/ask/stream", headers=h, json={"question": "How many days of annual leave?"})
    events = lines(r)
    assert r.headers["content-type"].startswith("application/x-ndjson")
    # "ping" keepalive events are interleaved on purpose (see main._heartbeat)
    assert [e["type"] for e in events if e["type"] != "ping"] == ["token", "token", "final"]
    assert events[0]["type"] == "ping"  # bytes flow immediately, before any slow work
    assert events[-1]["data"]["answer"] == "Employees get 24 days [1]."


def test_agent_stream_search_route(client, monkeypatch):
    monkeypatch.setattr(
        llm, "chat", lambda s, p, temperature=0.0: "SEARCH" if "routing" in s else "x"
    )
    h = signup(client)
    upload(client, h)
    events = lines(
        client.post(
            "/agent/ask/stream", headers=h, json={"question": "How many days of annual leave?"}
        )
    )
    assert events[-1]["type"] == "final" and events[-1]["data"]["route"] == "SEARCH"
    assert any(e["type"] == "token" for e in events)


def test_stream_reports_llm_failure_as_error_event(client, monkeypatch):
    def boom(*a, **k):
        raise llm.LLMError("provider down")
        yield  # pragma: no cover

    monkeypatch.setattr(llm, "chat_stream", boom)
    h = signup(client)
    upload(client, h)
    events = lines(
        client.post("/ask/stream", headers=h, json={"question": "How many days of annual leave?"})
    )
    assert events[-1] == {"type": "error", "status": 502, "detail": "provider down"}


def test_llm_failure_becomes_502(client, monkeypatch):
    def boom(*a, **k):
        raise llm.LLMError("provider down")

    monkeypatch.setattr(llm, "chat", boom)
    h = signup(client)
    upload(client, h)
    assert (
        client.post(
            "/ask", headers=h, json={"question": "How many days of annual leave?"}
        ).status_code
        == 502
    )


def test_daily_budget_per_user(client, monkeypatch):
    monkeypatch.setattr(main, "budget", main.Budget(per_user=2))
    a, b = signup(client, "a@example.com"), signup(client, "b@example.com")
    for h in (a, a):
        assert (
            client.post("/agent/ask", headers=h, json={"question": "hello there"}).status_code
            == 200
        )
    assert client.post("/agent/ask", headers=a, json={"question": "hello there"}).status_code == 429
    assert client.post("/agent/ask", headers=b, json={"question": "hello there"}).status_code == 200


# --------------------------------------------------------------------------- demo mode
def test_demo_mode_is_isolated_and_read_only(client, monkeypatch, tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "Acme_Leave_Policy.txt").write_text(
        "Employees get 24 days of leave.", encoding="utf-8"
    )
    monkeypatch.setattr(main, "DEMO_MODE", True)
    monkeypatch.setattr(main, "DEMO_DOCS_DIR", tmp_path / "docs")
    with TestClient(main.app) as demo:  # runs lifespan -> indexes the demo docs
        assert demo.get("/documents").json() == {"documents": ["Acme_Leave_Policy.txt"]}
        assert (
            demo.post("/upload", files={"file": ("a.txt", b"x y z", "text/plain")}).status_code
            == 403
        )
        assert demo.delete("/documents/Acme_Leave_Policy.txt").status_code == 403
        assert (
            demo.post("/auth/register", json={"email": "u@e.co", "password": PW}).status_code == 403
        )
        assert demo.post("/auth/login", json={"email": "u@e.co", "password": PW}).status_code == 403
        assert demo.get("/config").json()["demo_mode"] is True
    monkeypatch.setattr(main, "DEMO_MODE", False)
    assert main.current_user.__defaults__  # real accounts are never 'demo'
    assert main.DEMO_USER not in {main.auth.user_id_for("demo@example.com")}
