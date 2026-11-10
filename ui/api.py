
"""HTTP client for the DocQA API.

No Streamlit imports, so it is easy to test and reuse.
Handles connection errors, timeouts, unexpected HTML responses,
invalid JSON responses, and API errors safely.
"""

import json
import os
import time
from collections.abc import Iterator
from urllib.parse import quote

import requests


def _clean_url(url: str) -> str:
    """Tidy a pasted address: whitespace, quotes, trailing slash, missing scheme."""
    url = (url or "").strip().strip("\"'").strip().rstrip("/")
    if url and not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url


# The API address. Streamlit Community Cloud: put API_URL in the app's Secrets (see
# .streamlit/secrets.toml.example); anywhere else: set the API_URL environment variable.
API = _clean_url(os.getenv("API_URL", "http://localhost:8000"))


def configure(url: str) -> None:
    """Set the API address at runtime (the Streamlit app calls this with env var / secrets)."""
    global API
    API = _clean_url(url)


def api_url() -> str:
    return API


class ApiError(Exception):
    """An API call failed. Status 0 means connection/timeout failure."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _headers(token: str = "", forwarded_for: str = "") -> dict:
    """Build request headers."""

    headers = {}

    if token:
        headers["Authorization"] = f"Bearer {token}"

    if forwarded_for:
        headers["X-Forwarded-For"] = forwarded_for

    return headers


def _detail(resp) -> str:
    """Extract a safe, readable error message.

    Never expose raw HTML returned by a proxy or hosting platform.
    """

    content_type = resp.headers.get("content-type", "").lower()

    if "text/html" in content_type:
        return (
            "The backend temporarily returned an invalid response. "
            "Please try again."
        )

    try:
        data = resp.json()

    except ValueError:
        return (
            "The backend returned an unexpected response. "
            "Please try again."
        )

    if isinstance(data, dict):
        detail = data.get("detail") or data.get("message")

        if isinstance(detail, list):
            messages = []

            for item in detail:
                if isinstance(item, dict):
                    messages.append(
                        str(item.get("msg", "Invalid request"))
                    )
                else:
                    messages.append(str(item))

            return "; ".join(messages)[:500]

        if isinstance(detail, str):
            if "<" in detail and ">" in detail:
                return "The backend returned an invalid error message."

            return detail[:500]

    return "The request could not be completed."


def _call(
    method: str,
    path: str,
    token: str = "",
    timeout: float = 30,
    forwarded_for: str = "",
    **kw,
):
    """Make an HTTP request and handle errors consistently."""

    try:
        resp = requests.request(
            method,
            f"{API}{path}",
            headers=_headers(token, forwarded_for),
            timeout=timeout,
            **kw,
        )

    except requests.exceptions.Timeout:
        raise ApiError(
            0,
            "The request timed out. Please try again.",
        ) from None

    except requests.exceptions.RequestException:
        raise ApiError(
            0,
            "Cannot connect to the backend. Please try again.",
        ) from None

    if resp.status_code >= 400:
        raise ApiError(resp.status_code, _detail(resp))

    return resp


def _json_response(resp) -> dict:
    """Parse a JSON response and reject unexpected HTML or invalid data."""

    content_type = resp.headers.get("content-type", "").lower()

    if "text/html" in content_type:
        raise ApiError(
            resp.status_code,
            "The backend returned an unexpected HTML response. "
            "Please try again.",
        )

    try:
        data = resp.json()

    except ValueError:
        raise ApiError(
            resp.status_code,
            "The backend returned an invalid response. "
            "Please try again.",
        ) from None

    if not isinstance(data, dict):
        raise ApiError(
            resp.status_code,
            "The backend returned an unexpected response format.",
        )

    return data


def _get_token(resp) -> str:
    """Extract and validate an authentication token."""

    data = _json_response(resp)
    token = data.get("token")

    if not isinstance(token, str) or not token.strip():
        raise ApiError(
            resp.status_code,
            "The server did not return a valid authentication token.",
        )

    return token


def get_config() -> dict:
    """Public application configuration. RAISES ``ApiError`` when the backend cannot be reached or
    is not a DocQA API (an address that serves a web page, another service...). It must not return
    an empty dict in that case: the UI would take "no auth required" from it and skip sign-in."""

    data = _json_response(_call("GET", "/config", timeout=20))
    if "auth_required" not in data:
        raise ApiError(
            502,
            "The address in API_URL answered, but it is not the DocQA API. "
            "It must be the URL of the backend service (not of the Streamlit app).",
        )
    return data


def wait_for_backend(max_wait: float = 75.0, interval: float = 3.0, sleep=time.sleep) -> bool:
    """Block until ``/health`` answers with JSON, for at most ``max_wait`` seconds.

    Free hosting plans put the API to sleep when idle and need up to a minute to wake it; a plain
    request made during that time fails with a timeout or an HTML "starting" page. Returns False
    when the backend never became ready.
    """
    deadline = time.monotonic() + max_wait
    while True:
        try:
            resp = requests.get(f"{API}/health", timeout=10)
            if resp.status_code == 200 and "json" in resp.headers.get("content-type", "").lower():
                return True
        except requests.exceptions.RequestException:
            pass
        if time.monotonic() + interval > deadline:
            return False
        sleep(interval)


def login(
    email: str,
    password: str,
    forwarded_for: str = "",
) -> str:
    """Authenticate an existing user."""

    resp = _call(
        "POST",
        "/auth/login",
        json={
            "email": email,
            "password": password,
        },
        timeout=30,
        forwarded_for=forwarded_for,
    )

    return _get_token(resp)


def register(
    email: str,
    password: str,
    forwarded_for: str = "",
) -> str:
    """Register a new user."""

    resp = _call(
        "POST",
        "/auth/register",
        json={
            "email": email,
            "password": password,
        },
        timeout=30,
        forwarded_for=forwarded_for,
    )

    return _get_token(resp)


def change_password(
    token: str,
    old: str,
    new: str,
) -> str:
    """Change the current user's password."""

    resp = _call(
        "POST",
        "/auth/change-password",
        token,
        json={
            "old_password": old,
            "new_password": new,
        },
        timeout=30,
    )

    return _get_token(resp)


def documents(token: str) -> list[str]:
    """Retrieve documents belonging to the authenticated user."""

    payload = _json_response(
        _call("GET", "/documents", token)
    )

    docs = payload.get("documents", [])

    if isinstance(docs, list):
        return docs

    return []


def upload(
    token: str,
    name: str,
    content: bytes,
) -> dict:
    """Upload a document."""

    resp = _call(
        "POST",
        "/upload",
        token,
        timeout=180,
        files={
            "file": (
                name,
                content,
                "application/octet-stream",
            )
        },
    )

    return _json_response(resp)


def ingest_url(token: str, url: str) -> dict:
    """Ingest a document from a URL."""

    resp = _call(
        "POST",
        "/ingest/url",
        token,
        timeout=60,
        json={"url": url},
    )

    return _json_response(resp)


def delete_document(token: str, name: str) -> None:
    """Delete a document."""

    _call(
        "DELETE",
        f"/documents/{quote(name, safe='')}",
        token,
        timeout=60,
    )


def fetch_file(token: str, name: str) -> bytes:
    """Download the original document."""

    return _call(
        "GET",
        f"/documents/{quote(name, safe='')}/file",
        token,
        timeout=60,
    ).content


def page_image(
    token: str,
    name: str,
    page: int,
    quote_text: str = "",
) -> bytes:
    """Retrieve a rendered PDF page image."""

    return _call(
        "GET",
        f"/documents/{quote(name, safe='')}/page/{int(page)}",
        token,
        timeout=60,
        params={
            "quote": quote_text[:600],
        },
    ).content


def send_feedback(
    token: str,
    question: str,
    answer: str,
    rating: int,
    comment: str = "",
) -> None:
    """Submit answer feedback."""

    _call(
        "POST",
        "/feedback",
        token,
        json={
            "question": question,
            "answer": answer,
            "rating": rating,
            "comment": comment,
        },
    )


def get_stats(token: str) -> dict:
    """Retrieve account statistics."""

    resp = _call("GET", "/stats", token)
    return _json_response(resp)


def stream_events(
    path: str,
    token: str,
    payload: dict,
    forwarded_for: str = "",
) -> Iterator[dict]:
    """POST to a streaming endpoint and yield JSON events."""

    try:
        with requests.post(
            f"{API}{path}",
            json=payload,
            stream=True,
            timeout=(10, 180),
            headers=_headers(token, forwarded_for),
        ) as resp:

            if resp.status_code >= 400:
                raise ApiError(
                    resp.status_code,
                    _detail(resp),
                )

            content_type = resp.headers.get(
                "content-type", ""
            ).lower()

            if "text/html" in content_type:
                raise ApiError(
                    resp.status_code,
                    "The backend returned an unexpected HTML response.",
                )

            resp.encoding = "utf-8"

            for line in resp.iter_lines(
                decode_unicode=True
            ):
                if isinstance(line, bytes):
                    line = line.decode(
                        "utf-8",
                        errors="replace",
                    )

                if not line:
                    continue

                try:
                    event = json.loads(line)

                except ValueError:
                    raise ApiError(
                        resp.status_code,
                        "The backend returned invalid streaming data.",
                    ) from None

                if isinstance(event, dict):
                    yield event

    except requests.exceptions.Timeout:
        raise ApiError(
            0,
            "The request timed out. Please try again.",
        ) from None

    except requests.exceptions.RequestException:
        raise ApiError(
            0,
            "Cannot connect to the backend. Please try again.",
        ) from None
