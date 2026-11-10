
"""DocQA cookie-based session authentication."""

import time
from datetime import datetime, timedelta

import streamlit as st

try:  # persistence is a convenience: without the component the app still works (sign in per visit)
    import extra_streamlit_components as stx
except Exception:  # noqa: BLE001
    stx = None

COOKIE_NAME = "docqa_auth_token"
MANAGER_KEY = "_docqa_cookie_manager"

COOKIE_READ_DONE = "_docqa_cookie_read_done"
COOKIE_CACHE = "_docqa_cookie_cache"
SIGNED_OUT_KEY = "_docqa_signed_out"


def get_cookie_manager():
    """Create and reuse one CookieManager per Streamlit session."""
    if stx is None:
        raise RuntimeError("extra-streamlit-components is not installed")
    if MANAGER_KEY not in st.session_state:
        st.session_state[MANAGER_KEY] = stx.CookieManager(
            key="docqa_cookie_manager"
        )
    return st.session_state[MANAGER_KEY]


def begin_cookie_cycle():
    """Start one cookie-read cycle per Streamlit script execution."""
    st.session_state[COOKIE_READ_DONE] = False
    st.session_state[COOKIE_CACHE] = None


def restore_token():
    """Restore the browser token unless the user has signed out."""
    if st.session_state.get(SIGNED_OUT_KEY, False):
        return None

    if st.session_state.get(COOKIE_READ_DONE, False):
        return st.session_state.get(COOKIE_CACHE) or None

    st.session_state[COOKIE_READ_DONE] = True

    try:
        manager = get_cookie_manager()
        cookies = manager.get_all(key="docqa_get_all") or {}

        token = cookies.get(COOKIE_NAME, "")
        st.session_state[COOKIE_CACHE] = token or ""

        return token or None

    except Exception as exc:
        print(f"Cookie restoration error: {exc}")
        st.session_state[COOKIE_CACHE] = ""
        return None


def persist_token(token: str):
    """Save the authentication token in a seven-day browser cookie."""
    if not token:
        return

    try:
        manager = get_cookie_manager()
        manager.set(
            cookie=COOKIE_NAME,
            val=token,
            expires_at=datetime.now() + timedelta(days=7),
            key="docqa_save_token",
        )

        st.session_state[SIGNED_OUT_KEY] = False
        st.session_state[COOKIE_CACHE] = token
        st.session_state[COOKIE_READ_DONE] = True
        st.session_state["auth_token"] = token
        # The cookie is written by a small browser component. If the script reruns at once (the
        # sign-in form does st.rerun()), the browser may never get to run it and the visitor
        # would be signed out on refresh. A short pause lets the component render first.
        time.sleep(0.6)

    except Exception as exc:
        print(f"Cookie save error: {exc}")


def clear_token():
    """Clear the session token and request deletion of the browser cookie."""
    # Set this first so restore_token cannot reuse the old cookie.
    st.session_state[SIGNED_OUT_KEY] = True
    st.session_state["auth_token"] = ""
    st.session_state[COOKIE_CACHE] = ""
    st.session_state[COOKIE_READ_DONE] = True

    try:
        manager = get_cookie_manager()
        manager.delete(
            cookie=COOKIE_NAME,
            key="docqa_delete_token",
        )
    except Exception as exc:
        print(f"Cookie deletion error: {exc}")
