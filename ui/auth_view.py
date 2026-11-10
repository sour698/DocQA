
"""Sign-in / create-account screen."""

import re

import streamlit as st

from ui import api
from ui.auth_styles import AUTH_STYLES, BRAND_PANEL
from ui.session_auth import persist_token


MIN_PASSWORD = 10

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def _forwarded_for() -> str:
    """Get the visitor IP when running behind a proxy."""

    try:
        return (
            st.context.headers.get(
                "X-Forwarded-For", ""
            )
            .split(",")[-1]
            .strip()
        )

    except Exception:  # noqa: BLE001
        return ""


def _check_email(email: str) -> str | None:
    """Validate an email address."""

    email = email.strip()

    if not email:
        return "Enter your email address."

    if not _EMAIL.fullmatch(email):
        return "That does not look like a valid email address."

    return None


def _friendly(
    error: api.ApiError,
    action: str,
) -> str:
    """Convert API errors into user-friendly messages."""

    if error.status == 0:
        return (
            f"{action}: the service is temporarily unavailable. "
            "Please try again in a moment."
        )

    if error.status == 429:
        return (
            "Too many attempts. Please wait a few minutes "
            "and try again."
        )

    if error.status in (502, 503, 504):
        return (
            "The backend is temporarily unavailable. "
            "Please try again shortly."
        )

    detail = error.detail or "An unexpected error occurred."

    # Prevent accidental HTML from appearing in the UI.
    if "<" in detail and ">" in detail:
        return (
            "The server returned an unexpected response. "
            "Please try again."
        )

    return detail


def _complete_login(token: str) -> None:
    """Store a successfully authenticated token."""

    if not token:
        st.error(
            "Authentication failed: "
            "the server returned an empty token."
        )
        return

    st.session_state["auth_token"] = token

    # Persist the authentication token using existing cookie handling.
    persist_token(token)

    # Reset user-specific UI state.
    st.session_state["history"] = []
    st.session_state["pending_question"] = None
    st.session_state["selected_doc"] = None
    st.session_state["preview_bytes"] = None
    st.session_state["preview_name"] = None
    st.session_state["preview_cleared"] = False
    st.session_state["focus"] = None
    st.session_state["asked"] = 0


def render_auth_gate(cfg: dict) -> None:
    """Render the sign-in and registration screen."""

    st.markdown(
        AUTH_STYLES,
        unsafe_allow_html=True,
    )

    allow_register = cfg.get(
        "allow_registration",
        True,
    )

    brand, form = st.columns(
        [1.15, 1],
        gap="large",
        vertical_alignment="top",
    )

    with brand:
        st.html(BRAND_PANEL)

    with form:
        subtitle = (
            " or create a new account."
            if allow_register
            else "."
        )

        st.html(
            '<div class="auth-card-title">Welcome to DocQA</div>'
            '<div class="auth-card-sub">'
            "Sign in to your workspace"
            + subtitle
            + "</div>"
        )

        tab_names = (
            ["Sign in", "Create account"]
            if allow_register
            else ["Sign in"]
        )

        tabs = st.tabs(tab_names)

        with tabs[0]:
            _login_form()

        if allow_register:
            with tabs[1]:
                _register_form()

        st.html(
            '<div class="auth-secure">'
            "🔒 Your documents stay private to your account"
            "</div>"
        )


def _login_form() -> None:
    """Render and process the login form."""

    with st.form("docqa_login"):

        email = st.text_input(
            "Email",
            key="login_email",
            placeholder="you@company.com",
            autocomplete="email",
        )

        password = st.text_input(
            "Password",
            type="password",
            key="login_password",
            placeholder="Your password",
            autocomplete="current-password",
        )

        submit = st.form_submit_button(
            "Sign in",
            use_container_width=True,
            type="primary",
        )

    if submit:

        problem = (
            _check_email(email)
            or (
                "Enter your password."
                if not password
                else None
            )
        )

        if problem:
            st.error(problem)

        else:
            try:
                with st.spinner("Signing you in..."):

                    token = api.login(
                        email.strip(),
                        password,
                        _forwarded_for(),
                    )

                    _complete_login(token)

                st.rerun()

            except api.ApiError as error:
                st.error(
                    _friendly(
                        error,
                        "Sign in failed",
                    )
                )

            except Exception:
                st.error(
                    "An unexpected sign-in error occurred. "
                    "Please try again."
                )

    st.html(
        '<div class="auth-footer">'
        "Forgot your password? Ask your "
        "<b>administrator</b> to reset it."
        "</div>"
    )


def _register_form() -> None:
    """Render and process the registration form."""

    with st.form("docqa_register"):

        email = st.text_input(
            "Email",
            key="register_email",
            placeholder="you@company.com",
            autocomplete="email",
        )

        password = st.text_input(
            "Password",
            type="password",
            key="register_password",
            placeholder=f"At least {MIN_PASSWORD} characters",
            autocomplete="new-password",
        )

        confirm_password = st.text_input(
            "Confirm password",
            type="password",
            key="register_confirm",
            placeholder="Repeat the password",
            autocomplete="new-password",
        )

        st.html(
            f'<div class="auth-hint">'
            f"Use at least {MIN_PASSWORD} characters. "
            "A short phrase of several words is easy to remember "
            "and hard to guess."
            "</div>"
        )

        submit = st.form_submit_button(
            "Create account",
            use_container_width=True,
            type="primary",
        )

    if submit:

        problem = _check_email(email)

        if (
            not problem
            and len(password) < MIN_PASSWORD
        ):
            problem = (
                f"Your password needs at least "
                f"{MIN_PASSWORD} characters."
            )

        if (
            not problem
            and password != confirm_password
        ):
            problem = "The two passwords do not match."

        if problem:
            st.error(problem)

        else:
            try:
                with st.spinner("Creating your account..."):

                    token = api.register(
                        email.strip(),
                        password,
                        _forwarded_for(),
                    )

                    _complete_login(token)

                st.rerun()

            except api.ApiError as error:
                st.error(
                    _friendly(
                        error,
                        "Account creation failed",
                    )
                )

            except Exception:
                st.error(
                    "An unexpected account creation error occurred. "
                    "Please try again."
                )
