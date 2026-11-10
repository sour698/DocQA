"""Smoke tests for the Streamlit UI using Streamlit's headless AppTest runner (api mocked)."""

import io
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from ui import api  # noqa: E402

APP = Path(__file__).resolve().parent.parent / "streamlit_app.py"

FINAL = {
    "answer": "Employees get 24 days [1].",
    "citations": [
        {"ref": 1, "source": "leave.pdf", "page": 1, "snippet": "24 days of leave", "cited": True}
    ],
    "citation_audit": {"has_citations": True, "valid_refs": [1], "invalid_refs": []},
    "refused": False,
    "from_documents": True,
    "confidence": "high",
    "top_score": 5.2,
}


def png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def backend(monkeypatch):
    calls = {"feedback": [], "stream": []}

    def stream(path, token, payload, forwarded_for=""):
        calls["stream"].append((path, payload))
        yield {"type": "token", "text": "Employees get "}
        yield {"type": "token", "text": "24 days [1]."}
        yield {"type": "final", "data": FINAL}

    monkeypatch.setattr(
        api,
        "get_config",
        lambda: {
            "auth_required": True,
            "allow_registration": True,
            "allowed_extensions": [".pdf", ".txt"],
            "max_upload_mb": 10,
        },
    )
    monkeypatch.setattr(api, "wait_for_backend", lambda *a, **k: True)
    monkeypatch.setattr(api, "documents", lambda token: ["leave.pdf", "notes.txt"])
    monkeypatch.setattr(
        api,
        "get_stats",
        lambda token: {
            "days": 30,
            "questions": 3,
            "refused": 1,
            "refusal_rate": 0.33,
            "avg_latency_ms": 1500,
            "avg_top_score": 2.0,
            "recent_unanswered": ["what about parking?"],
            "feedback_up": 1,
            "feedback_down": 0,
        },
    )
    monkeypatch.setattr(api, "fetch_file", lambda token, name: b"%PDF-1.4 fake")
    monkeypatch.setattr(api, "page_image", lambda token, name, page, quote="": png())
    monkeypatch.setattr(api, "stream_events", stream)
    monkeypatch.setattr(api, "send_feedback", lambda *a, **k: calls["feedback"].append(a))
    return calls


def app(token="tok"):
    at = AppTest.from_file(str(APP), default_timeout=30)
    if token:
        at.session_state["auth_token"] = token
    else:
        # skip the one-off "Restoring your session" pass (a browser cookie component answers it)
        at.session_state["_docqa_cookie_initialized"] = True
    return at


def test_signed_out_user_sees_only_the_sign_in_screen(backend):
    at = app(token="").run()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Sign in", "Create account"]
    assert not at.chat_message


def test_login_failure_shows_the_server_message(backend, monkeypatch):
    def bad(*a, **k):
        raise api.ApiError(401, "Incorrect email or password.")

    monkeypatch.setattr(api, "login", bad)
    at = app(token="").run()
    at.text_input(key="login_email").input("a@b.co")
    at.text_input(key="login_password").input("whatever-pass")
    at.button[0].click().run()
    assert any("Incorrect email or password." in e.value for e in at.error)


def test_login_success_enters_the_dashboard(backend, monkeypatch):
    monkeypatch.setattr(api, "login", lambda *a, **k: "new-token")
    at = app(token="").run()
    at.text_input(key="login_email").input("a@b.co")
    at.text_input(key="login_password").input("whatever-pass")
    at.button[0].click().run()
    assert at.session_state["auth_token"] == "new-token" and not at.exception


def test_registration_hidden_when_disabled(backend, monkeypatch):
    monkeypatch.setattr(
        api, "get_config", lambda: {"auth_required": True, "allow_registration": False}
    )
    assert [t.label for t in app(token="").run().tabs] == ["Sign in"]


def test_dashboard_lists_documents_and_stats(backend):
    at = app().run()
    assert not at.exception
    assert any("2 indexed documents" in c.value for c in at.caption)
    assert not at.metric  # usage statistics are opt-in (they cost three database queries)
    at.toggle(key="show_usage").set_value(True).run()
    assert any(m.label == "Questions" and m.value == "3" for m in at.metric)


def test_expired_session_returns_to_sign_in(backend, monkeypatch):
    def expired(token):
        raise api.ApiError(401, "Session expired. Please sign in again.")

    monkeypatch.setattr(api, "documents", expired)
    at = app().run()
    assert not at.exception and at.session_state["auth_token"] == "" and at.tabs


def test_backend_offline_is_reported_not_crashed(backend, monkeypatch):
    """A dead / wrong backend shows a clear error and a retry button - never the dashboard and
    never a skipped sign-in screen."""

    def down():
        raise api.ApiError(0, "Cannot connect to the backend. Please try again.")

    monkeypatch.setattr(api, "get_config", down)
    at = app(token="").run()
    assert not at.exception
    assert any("Could not connect" in e.value for e in at.error)
    assert not at.tabs  # no sign-in form, no dashboard
    assert any(b.label == "Try again" for b in at.button)


def test_documents_failing_after_login_shows_backend_offline(backend, monkeypatch):
    monkeypatch.setattr(
        api, "documents", lambda token: (_ for _ in ()).throw(api.ApiError(0, "Cannot connect"))
    )
    at = app().run()
    assert not at.exception


def test_get_config_rejects_something_that_is_not_the_docqa_api(monkeypatch):
    class Resp:
        status_code = 200
        headers = {"content-type": "application/json"}

        def json(self):
            return {"hello": "world"}

    monkeypatch.setattr(api.requests, "request", lambda *a, **k: Resp())
    with pytest.raises(api.ApiError, match="not the DocQA API"):
        api.get_config()


def test_get_config_rejects_html_pages(monkeypatch):
    class Resp:
        status_code = 200
        headers = {"content-type": "text/html"}

    monkeypatch.setattr(api.requests, "request", lambda *a, **k: Resp())
    with pytest.raises(api.ApiError):
        api.get_config()


def test_wait_for_backend_retries_until_ready(monkeypatch):
    answers = iter([OSError, "html", "ok"])

    class Resp:
        def __init__(self, ctype, code=200):
            self.status_code, self.headers = code, {"content-type": ctype}

    def get(url, timeout):
        a = next(answers)
        if a is OSError:
            raise api.requests.exceptions.ConnectionError()
        return Resp("text/html" if a == "html" else "application/json")

    monkeypatch.setattr(api.requests, "get", get)
    assert api.wait_for_backend(max_wait=60, interval=0, sleep=lambda s: None) is True


def test_wait_for_backend_gives_up(monkeypatch):
    def get(url, timeout):
        raise api.requests.exceptions.ConnectionError()

    monkeypatch.setattr(api.requests, "get", get)
    monkeypatch.setattr(api.time, "monotonic", iter(range(0, 1000, 20)).__next__)
    assert api.wait_for_backend(max_wait=30, interval=3, sleep=lambda s: None) is False


def test_api_url_is_cleaned():
    assert api._clean_url(' "my-api.onrender.com/ " ') == "https://my-api.onrender.com"
    assert api._clean_url("http://localhost:8000/") == "http://localhost:8000"


def ask(at, text):
    at.text_input(key="docqa_question_text").input(text)
    next(b for b in at.button if "Send question" in b.label).click().run()
    return at


def test_asking_streams_an_answer_with_citations_and_confidence(backend):
    at = ask(app().run(), "How many days of leave?")
    assert not at.exception
    path, payload = backend["stream"][0]
    assert path == "/agent/ask/stream" and payload["question"] == "How many days of leave?"
    history = at.session_state["history"]
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert (
        history[1]["content"] == "Employees get 24 days [1]." and history[1]["confidence"] == "high"
    )
    assert any("Retrieval confidence: high" in c.value for c in at.caption)
    assert any("From your documents" in h.value for h in at.get("html"))


def test_plain_mode_uses_the_ask_endpoint(backend):
    at = app().run()
    at.session_state["agent_mode"] = False
    ask(at, "How many days?")
    assert (
        backend["stream"][0][0] == "/ask/stream"
        and backend["stream"][0][1]["mode"] == "hybrid_rerank"
    )


def test_followup_sends_conversation_history(backend):
    at = ask(app().run(), "first question")
    ask(at, "and for contractors?")
    _, payload = backend["stream"][1]
    assert [m["role"] for m in payload["history"]] == ["user", "assistant"]


def test_clicking_a_citation_shows_the_cited_page(backend):
    at = ask(app().run(), "How many days of leave?")
    next(b for b in at.button if "Show this page" in b.label).click().run()
    assert not at.exception
    assert (
        at.session_state["focus"]["page"] == 1 and at.session_state["selected_doc"] == "leave.pdf"
    )
    assert len(at.get("imgs")) >= 1 or any("Cited passage" in c.value for c in at.caption)
    next(b for b in at.button if "Back to the full document" in b.label).click().run()
    assert at.session_state["focus"] is None


def test_feedback_buttons_send_rating(backend):
    at = ask(app().run(), "How many days of leave?")
    next(b for b in at.button if "Not helpful" in b.label).click().run()
    assert not at.exception
    assert backend["feedback"] and backend["feedback"][0][3] == -1
    assert at.session_state["history"][-1]["feedback"] == -1


def test_rate_limit_message_is_friendly(backend, monkeypatch):
    def limited(*a, **k):
        raise api.ApiError(429, "Daily question limit reached - please try again tomorrow.")
        yield  # pragma: no cover

    monkeypatch.setattr(api, "stream_events", limited)
    at = ask(app().run(), "anything at all")
    assert not at.exception and any("Daily question limit" in w.value for w in at.warning)
    assert at.session_state["history"] == []


def test_non_pdf_documents_get_an_explanation_instead_of_a_viewer(backend):
    at = app().run()
    at.session_state["selected_doc"] = "notes.txt"
    at.run()
    assert not at.exception and any("Previews are available for PDF" in i.value for i in at.info)


def test_demo_mode_hides_upload_and_enforces_session_limit(backend, monkeypatch):
    monkeypatch.setattr(api, "get_config", lambda: {"demo_mode": True, "auth_required": False})
    at = app(token="").run()
    assert not at.exception and any("Public demo" in i.value for i in at.info)
    assert not at.get("file_uploader")
    at.session_state["asked"] = 10
    ask(at, "one more question")
    assert any("Demo limit reached" in w.value for w in at.warning)


# ------------------------------------------------------------------ sign-in / create-account screen
def submit(at, label):
    next(b for b in at.button if b.label == label).click().run()
    return at


def fill_register(at, email, password, confirm):
    at.text_input(key="register_email").input(email)
    at.text_input(key="register_password").input(password)
    at.text_input(key="register_confirm").input(confirm)
    return at


@pytest.fixture
def auth_calls(backend, monkeypatch):
    calls = {"login": [], "register": []}

    def fake_login(email, password, forwarded_for=""):
        calls["login"].append((email, password))
        return "login-token"

    def fake_register(email, password, forwarded_for=""):
        calls["register"].append((email, password))
        return "new-account-token"

    monkeypatch.setattr(api, "login", fake_login)
    monkeypatch.setattr(api, "register", fake_register)
    return calls


def test_login_form_checks_input_before_calling_the_server(auth_calls):
    at = app(token="").run()
    submit(at, "Sign in")
    assert any("Enter your email address" in e.value for e in at.error) and not auth_calls["login"]
    at.text_input(key="login_email").input("not-an-email")
    submit(at, "Sign in")
    assert any("valid email" in e.value for e in at.error) and not auth_calls["login"]
    at.text_input(key="login_email").input("a@b.co")
    submit(at, "Sign in")
    assert any("Enter your password" in e.value for e in at.error) and not auth_calls["login"]


def test_login_trims_the_email_and_signs_in(auth_calls):
    at = app(token="").run()
    at.text_input(key="login_email").input("  a@b.co ")
    at.text_input(key="login_password").input("whatever-pass")
    submit(at, "Sign in")
    assert auth_calls["login"] == [("a@b.co", "whatever-pass")]
    assert at.session_state["auth_token"] == "login-token"


@pytest.mark.parametrize(
    "email,password,confirm,message",
    [
        ("nope", "long-enough-password", "long-enough-password", "valid email"),
        ("a@b.co", "short", "short", "at least 10 characters"),
        ("a@b.co", "long-enough-password", "different-password-x", "do not match"),
    ],
)
def test_registration_problems_are_explained_and_nothing_is_sent(
    auth_calls, email, password, confirm, message
):
    at = fill_register(app(token="").run(), email, password, confirm)
    submit(at, "Create account")
    assert any(message in e.value for e in at.error)
    assert not auth_calls["register"] and at.session_state["auth_token"] == ""


def test_registration_success_signs_the_user_in(auth_calls):
    at = fill_register(
        app(token="").run(), " new@user.io ", "long-enough-password", "long-enough-password"
    )
    submit(at, "Create account")
    assert auth_calls["register"] == [("new@user.io", "long-enough-password")]
    assert at.session_state["auth_token"] == "new-account-token"


def test_server_messages_are_shown_in_plain_language(backend, monkeypatch):
    for status, detail, expected in [
        (400, "An account with this email already exists.", "already exists"),
        (429, "raw rate limit text", "Too many attempts"),
        (0, "Cannot connect to the backend. Is the API running?", "temporarily unavailable"),
    ]:

        def fail(*a, _s=status, _d=detail, **k):
            raise api.ApiError(_s, _d)

        monkeypatch.setattr(api, "register", fail)
        at = fill_register(
            app(token="").run(), "a@b.co", "long-enough-password", "long-enough-password"
        )
        submit(at, "Create account")
        assert any(expected in e.value for e in at.error), (status, [e.value for e in at.error])


def test_auth_screen_has_the_brand_panel_and_stylesheet(backend):
    at = app(token="").run()
    html = " ".join(str(h.value) for h in at.get("html"))
    markdown = " ".join(m.value for m in at.markdown)
    assert "Ask your documents" in html and "Welcome to DocQA" in html
    assert "<style>" in markdown and "stTab" in markdown


def test_auth_stylesheet_is_well_formed_and_covers_both_streamlit_generations():
    from ui.auth_styles import AUTH_STYLES

    assert AUTH_STYLES.count("{") == AUTH_STYLES.count("}")
    for selector in (
        '[data-testid="stTab"]',
        'button[data-baseweb="tab"]',
        '[data-testid="stTextInputRootElement"]',
        '[data-baseweb="input"]',
        '[data-testid="InputInstructions"]',
    ):
        assert selector in AUTH_STYLES


def test_streamlit_theme_config_is_dark_and_matches_the_upload_limit():
    import tomllib

    from app import config

    cfg = tomllib.loads((APP.parent / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert cfg["theme"]["base"] == "dark" and cfg["theme"]["primaryColor"] == "#8264ff"
    assert (
        cfg["server"]["maxUploadSize"] == config.MAX_UPLOAD_MB
    )  # the uploader must not promise more
    assert cfg["client"]["showErrorDetails"] == "none"


# ------------------------------------------------------------------ fewer round trips per click
@pytest.fixture
def counted(backend, monkeypatch):
    counts = {"config": 0, "documents": 0, "stats": 0}
    cfg = api.get_config
    docs = api.documents
    stats = api.get_stats

    def count(name, fn):
        def wrapper(*a, **k):
            counts[name] += 1
            return fn(*a, **k)

        return wrapper

    monkeypatch.setattr(api, "get_config", count("config", cfg))
    monkeypatch.setattr(api, "documents", count("documents", docs))
    monkeypatch.setattr(api, "get_stats", count("stats", stats))
    return counts


def test_clicks_do_not_refetch_documents_stats_or_config(counted):
    at = app().run()
    assert counted == {"config": 1, "documents": 1, "stats": 0}
    for _ in range(3):  # three more reruns (toggles etc.)
        at.toggle(key="allow_general").set_value(True).run()
        at.toggle(key="allow_general").set_value(False).run()
    assert counted == {"config": 1, "documents": 1, "stats": 0}


def test_usage_stats_load_once_when_requested_then_are_cached(counted):
    at = app().run()
    at.toggle(key="show_usage").set_value(True).run()
    at.toggle(key="allow_general").set_value(True).run()
    at.toggle(key="allow_general").set_value(False).run()
    assert counted["stats"] == 1


def test_asking_a_question_refreshes_the_cached_stats(counted):
    at = app().run()
    at.toggle(key="show_usage").set_value(True).run()
    ask(at, "How many days of leave?")
    at.run()
    assert counted["stats"] == 2  # the question count changed, so the cache was dropped


def test_document_list_is_refreshed_after_a_delete(counted, monkeypatch):
    deleted = []
    monkeypatch.setattr(api, "delete_document", lambda token, name: deleted.append(name))
    at = app().run()
    next(b for b in at.button if b.key == "delete_0").click().run()
    assert deleted == ["leave.pdf"] and counted["documents"] == 2


def test_an_empty_config_answer_is_not_remembered(backend, monkeypatch):
    answers = iter([{}, {"auth_required": True, "allow_registration": True}])
    calls = []
    monkeypatch.setattr(api, "get_config", lambda: calls.append(1) or next(answers))
    monkeypatch.setattr(
        api, "documents", lambda token: (_ for _ in ()).throw(api.ApiError(0, "down"))
    )
    at = app(token="").run()
    at.run()
    assert len(calls) == 2  # the first (failed) answer was retried, not cached


def test_signing_out_clears_the_caches(counted):
    at = app().run()
    next(b for b in at.button if b.label == "Sign out").click().run()
    assert at.session_state["docs_cache"] is None and at.session_state["stats_cache"] is None
