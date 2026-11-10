"""Small client for any OpenAI-compatible chat API (Groq, Gemini, OpenAI, ...)."""

import json
import re
import time
from collections.abc import Iterator

import requests

from .config import LLM_API_KEY, LLM_BASE_URL, LLM_DEADLINE, LLM_MODEL

MAX_RETRIES = 8
MAX_WAIT_SECONDS = 90  # if the API asks us to wait longer than this, fail with a clear message


class LLMError(RuntimeError):
    """Raised with a human-readable message when the LLM call fails."""


def _retry_after_seconds(text: str) -> float | None:
    """Parse hints like 'Please try again in 3.2475s' / '250ms' / '1m5.2s' from a 429 body."""
    m = re.search(r"try again in ([0-9hms.]+)", text)
    if not m:
        return None
    units = {"h": 3600, "m": 60, "s": 1, "ms": 0.001}
    parts = re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", m.group(1))
    return sum(float(v) * units[u] for v, u in parts) if parts else None


def _body(system: str, user: str, temperature: float, stream: bool) -> dict:
    body = {
        "model": LLM_MODEL,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if stream:
        body["stream"] = True
    if LLM_MODEL.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = "low"  # far fewer hidden tokens -> faster, fits free-tier limits
    return body


def _error_for(resp) -> LLMError:
    hint = ""
    if resp.status_code in (400, 404):
        hint = (
            f" The model '{LLM_MODEL}' may be retired or misspelled - "
            "check LLM_MODEL in .env against your provider's model list."
        )
    elif resp.status_code == 401:
        hint = " Check LLM_API_KEY in .env."
    elif resp.status_code == 429:
        hint = (
            " Rate/daily limit still exceeded after retries - wait a while, or use a "
            "different model in LLM_MODEL."
        )
    return LLMError(f"LLM API error {resp.status_code}: {resp.text[:300]}.{hint}")


def _post(body: dict, stream: bool = False):
    """POST with 429 back-off. Total time is capped by LLM_DEADLINE, so one slow provider cannot
    pin a worker thread for minutes."""
    if not LLM_API_KEY:
        raise LLMError("LLM_API_KEY is not set. Copy .env.example to .env and add your key.")
    started = time.monotonic()
    resp = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.post(
                f"{LLM_BASE_URL.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {LLM_API_KEY}"},
                json=body,
                timeout=60,
                stream=stream,
            )
        except requests.RequestException as e:
            raise LLMError(f"Could not reach the LLM service at {LLM_BASE_URL}: {e}") from e

        if resp.status_code != 429:
            break
        wait = _retry_after_seconds(resp.text)
        if wait is None:
            wait = min(2**attempt, 20)
        out_of_time = time.monotonic() - started + wait > LLM_DEADLINE
        if wait > MAX_WAIT_SECONDS or attempt == MAX_RETRIES or out_of_time:
            break  # daily limit / retries / deadline exhausted: report it below
        time.sleep(wait + 0.5)

    if resp.status_code != 200:
        raise _error_for(resp)
    return resp


def chat(system: str, user: str, temperature: float = 0.0) -> str:
    resp = _post(_body(system, user, temperature, stream=False))
    return resp.json()["choices"][0]["message"]["content"].strip()


def chat_stream(system: str, user: str, temperature: float = 0.0) -> Iterator[str]:
    """Yield the answer piece by piece (server-sent events from an OpenAI-compatible API)."""
    resp = _post(_body(system, user, temperature, stream=True), stream=True)
    # requests only decodes iter_lines as text when an encoding is known. SSE is UTF-8.
    resp.encoding = "utf-8"
    try:
        for raw in resp.iter_lines(decode_unicode=True):
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            if not raw or not raw.startswith("data:"):
                continue
            data = raw[5:].strip()
            if data == "[DONE]":
                break
            try:
                delta = json.loads(data)["choices"][0].get("delta", {})
            except (ValueError, KeyError, IndexError):
                continue
            piece = delta.get("content")
            if piece:
                yield piece
    finally:
        resp.close()
