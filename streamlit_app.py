
"""DocQA dashboard (Streamlit).
The logic lives in the ui package; this file wires it up.
"""

import os

import streamlit as st

from ui import api
from ui.auth_view import render_auth_gate
from ui.cache import load_documents
from ui.components import render_html
from ui.panels import render_left, render_middle, render_right
from ui.styles import STYLES
from ui.session_auth import begin_cookie_cycle, clear_token, restore_token


# -------------------------------------------------
# PAGE CONFIGURATION
# -------------------------------------------------

st.set_page_config(
    page_title="DocQA | AI Document Intelligence",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded",
)


# -------------------------------------------------
# BACKEND ADDRESS
# -------------------------------------------------


def _resolve_api_url() -> str:
    """API_URL from the environment, else from Streamlit secrets (Community Cloud), else local."""
    url = os.getenv("API_URL", "").strip()
    if not url:
        try:
            url = str(st.secrets.get("API_URL", "")).strip()
        except Exception:  # noqa: BLE001 - no secrets file configured
            url = ""
    return url or "http://localhost:8000"


api.configure(_resolve_api_url())


# -------------------------------------------------
# SESSION DEFAULTS
# -------------------------------------------------

DEFAULTS = {
    "history": [],
    "selected_doc": None,
    "preview_bytes": None,
    "preview_name": None,
    "preview_cleared": False,
    "focus": None,
    "agent_mode": True,
    "allow_general": False,
    "retrieval_mode": "hybrid_rerank",
    "pending_question": None,
    "asked": 0,
    "auth_token": "",
    "cfg": {},
    "docs_cache": None,
    "stats_cache": None,
}

for key, value in DEFAULTS.items():
    st.session_state.setdefault(key, value)


# -------------------------------------------------
# COOKIE AUTHENTICATION
# -------------------------------------------------

# Start one cookie-read cycle for this script execution.
begin_cookie_cycle()

# Do not restore a browser cookie after an explicit sign-out.
if not st.session_state.get("auth_token"):

    try:
        saved_token = restore_token()

        if saved_token:
            st.session_state["auth_token"] = saved_token

        elif not (
            st.session_state.get("_docqa_cookie_initialized", False)
            or st.session_state.get("_docqa_signed_out", False)  # explicit sign-out / expiry
        ):
            # Give the browser cookie component time to initialize.
            st.session_state["_docqa_cookie_initialized"] = True
            st.info("Restoring your DocQA session...")
            # Safety net: if the browser component never answers, the visitor can still continue.
            st.button("Continue to sign in", key="_docqa_skip_restore")
            st.stop()

    except Exception as exc:
        st.warning(f"Cookie restoration error: {exc}")


# -------------------------------------------------
# SERVER CONFIGURATION
# -------------------------------------------------

cfg = st.session_state.get("cfg") or {}

if not cfg:
    try:
        with st.spinner(
            "Connecting to the DocQA server... (a sleeping free-tier server can take up to a minute)"
        ):
            # A sleeping server needs time to wake: wait for it ONCE, then ask for its settings.
            if not st.session_state.get("_backend_awake"):
                api.wait_for_backend()
            cfg = api.get_config()
        st.session_state["_backend_awake"] = True
        st.session_state["cfg"] = cfg

    except api.ApiError as e:
        # Never fall through to the dashboard (or skip the sign-in screen) without a backend.
        st.error(f"Could not connect to the DocQA server: {e.detail}")
        st.caption(
            f"Address in use: `{api.api_url()}`. On Streamlit Community Cloud set API_URL in "
            "the app's Secrets to your Render service URL (https://<name>.onrender.com)."
        )
        st.button("Try again", key="_docqa_retry_backend")
        st.stop()


# -------------------------------------------------
# AUTHENTICATION GATE
# -------------------------------------------------

if cfg.get("auth_required") and not st.session_state.get("auth_token"):

    render_auth_gate(cfg)
    st.stop()


# -------------------------------------------------
# GLOBAL STYLES
# -------------------------------------------------

st.markdown(STYLES, unsafe_allow_html=True)


# -------------------------------------------------
# LOAD DOCUMENTS
# -------------------------------------------------

token = st.session_state.get("auth_token", "")

docs = []
api_online = False
api_error = None

try:
    docs = load_documents(token)
    api_online = True

except api.ApiError as e:

    if e.status == 401:
        # Token expired or revoked.
        clear_token()

        st.session_state["auth_token"] = ""
        st.session_state["history"] = []
        st.session_state["pending_question"] = None

        st.rerun()

    api_error = e.detail


# -------------------------------------------------
# BACKEND STATUS
# -------------------------------------------------

demo = bool(cfg.get("demo_mode", False))

status_class = (
    "system-status"
    if api_online
    else "system-status offline"
)

status_text = (
    "● Vector Index Active"
    if api_online
    else "● Backend Offline"
)


# -------------------------------------------------
# HEADER
# -------------------------------------------------

render_html(f"""
<div class="top-header">
    <div class="brand-icon">◈</div>

    <div class="brand-info">
        <div class="brand-name">DocQA</div>
        <div class="brand-sub">
            Vector Store &amp; Vision QA Engine
        </div>
    </div>

    <div class="brand-tag">MULTIMODAL RAG</div>

    <div class="header-spacer"></div>

    <div class="{status_class}">
        {status_text}
    </div>

    <div class="ai-avatar">AI</div>
</div>
""")


# -------------------------------------------------
# MAIN DASHBOARD
# -------------------------------------------------

left, middle, right = st.columns(
    [1.05, 1.15, 1.05],
    gap="medium",
    vertical_alignment="top",
)

with left:
    render_left(
        docs,
        api_online,
        api_error,
        cfg,
        demo,
    )

with middle:
    render_middle(token)

with right:
    render_right(
        token,
        docs,
        demo,
    )


# -------------------------------------------------
# FOOTER
# -------------------------------------------------

render_html("""
<div class="footer">
    ◈ DocQA | Document Intelligence |
    Powered by Retrieval-Augmented Generation
</div>
""")
