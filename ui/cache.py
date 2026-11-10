"""Per-session caches so a click does not re-fetch data that has not changed.

Without them every Streamlit rerun (a toggle, a button, a typed question...) called the API for the
document list and the usage statistics again; with a database that is not on the same machine each
of those calls costs about a second.
"""

import time

import streamlit as st

from ui import api

DOCS_TTL = 30.0  # seconds; uploads/deletes in THIS session refresh it immediately
STATS_TTL = 60.0


def load_documents(token: str) -> list[str]:
    """The account's document names (raises ``api.ApiError`` exactly like ``api.documents``)."""
    cache = st.session_state.get("docs_cache")
    if cache and cache["token"] == token and time.monotonic() - cache["at"] < DOCS_TTL:
        return cache["docs"]
    docs = api.documents(token)
    st.session_state.docs_cache = {"token": token, "at": time.monotonic(), "docs": docs}
    return docs


def invalidate_documents() -> None:
    st.session_state.docs_cache = None


def load_stats(token: str) -> dict:
    cache = st.session_state.get("stats_cache")
    if cache and cache["token"] == token and time.monotonic() - cache["at"] < STATS_TTL:
        return cache["stats"]
    stats = api.get_stats(token)
    st.session_state.stats_cache = {"token": token, "at": time.monotonic(), "stats": stats}
    return stats


def invalidate_stats() -> None:
    st.session_state.stats_cache = None


def invalidate_all() -> None:
    invalidate_documents()
    invalidate_stats()
