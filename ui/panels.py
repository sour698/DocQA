"""The three dashboard columns: documents (left), viewer (middle), assistant (right)."""

import html
import os

import streamlit as st

from ui import api
from ui.cache import invalidate_all, invalidate_documents, invalidate_stats, load_stats
from ui.components import doc_card, render_html, render_pair
from ui.session_auth import clear_token, persist_token

SESSION_LIMIT = int(os.getenv("SESSION_LIMIT", "10"))  # questions per visitor session (demo only)
S = st.session_state


def _forwarded_for() -> str:
    """Real visitor IP when running behind a proxy (so rate limits are per visitor)."""
    try:
        return st.context.headers.get("X-Forwarded-For", "").split(",")[-1].strip()
    except Exception:  # noqa: BLE001 - older Streamlit / no context
        return ""


def _api_error(e: api.ApiError, what: str) -> None:
    if e.status == 401:  # token expired or revoked: back to the sign-in screen
        clear_token()
        S.auth_token = ""
        st.rerun()
    st.error(f"{what}: {e.detail}")


def clear_preview() -> None:
    S.preview_bytes = None
    S.preview_name = None
    S.preview_cleared = True
    S.focus = None


def reset_selection() -> None:
    S.selected_doc = None
    S.preview_bytes = None
    S.preview_name = None
    S.preview_cleared = False
    S.focus = None


# ------------------------------------------------------------------------------ left
def render_left(docs: list[str], api_online: bool, api_error: str | None, cfg: dict, demo: bool):
    token = S.get("auth_token", "")
    
    if token:
        if st.button("Sign out", key="docqa_signout", use_container_width=True):
            clear_token()

            S.auth_token = ""
            S.history = []
            S.pending_question = None
            S.asked = 0

            invalidate_all()
            reset_selection()

            st.rerun()

        with st.expander("Account & usage"):
            _account_panel(token)


    with st.container(border=True):
        render_html(
            '<div class="panel-heading"><span class="panel-icon">▣</span>Indexed Sources</div>'
        )
        if demo:
            st.info(
                f"Public demo: ask about the sample documents below. Each visitor gets {SESSION_LIMIT} questions."
            )
        else:
            _upload_panel(token, cfg)

        render_html('<div class="section-label">ACTIVE COLLECTIONS</div>')
        if api_online:
            st.success("API Connected")
        else:
            st.error("API Offline")
            st.caption(api_error or "")
        st.caption(f"{len(docs)} indexed documents")

        if docs:
            for i, name in enumerate(docs):
                render_html(doc_card(name, S.selected_doc == name))
                if st.button("Open Document", key=f"open_{i}", use_container_width=True):
                    S.selected_doc = name
                    S.preview_bytes = S.preview_name = None
                    S.preview_cleared = False
                    S.focus = None
                    st.rerun()
                if not demo and st.button(
                    "Delete Document", key=f"delete_{i}", use_container_width=True
                ):
                    try:
                        with st.spinner("Deleting document..."):
                            api.delete_document(token, name)
                        invalidate_documents()
                        if S.selected_doc == name:
                            reset_selection()
                        st.success("Document deleted.")
                        st.rerun()
                    except api.ApiError as e:
                        _api_error(e, "Deletion failed")
        else:
            st.info("No documents indexed yet.")

        render_html('<div class="section-label">AI CONFIGURATION</div>')
        st.toggle(
            "Agent Mode",
            key="agent_mode",
            help="Understands follow-up questions, summaries and small talk.",
        )
        st.toggle(
            "Also answer general questions",
            key="allow_general",
            help="OFF: answers only from your documents (safest). ON: if the documents do not "
            "contain the answer, it answers from general knowledge and labels the answer clearly.",
            disabled=not S.agent_mode,
        )
        st.selectbox(
            "Retrieval Strategy",
            ["hybrid_rerank", "naive"],
            key="retrieval_mode",
            disabled=S.agent_mode,
            help="'naive' = plain vector search with no refusal logic (baseline for comparison only).",
        )
        if st.button("Clear Chat History", key="clear_chat", use_container_width=True):
            S.history, S.pending_question = [], None
            st.rerun()


def _upload_panel(token: str, cfg: dict) -> None:
    exts = [e.lstrip(".") for e in cfg.get("allowed_extensions", [".pdf"])]
    limit = cfg.get("max_upload_mb", 10)
    st.markdown("**☁ Upload a document**")
    uploaded = st.file_uploader(
        "Choose a file",
        type=exts,
        label_visibility="collapsed",
        key="document_upload",
        help=f"Up to {limit} MB. Types: {', '.join(exts)}",
    )
    if uploaded:
        st.caption(f"Selected: {uploaded.name}")
        if st.button("＋ Index Document", key="index_document", use_container_width=True):
            try:
                content = uploaded.getvalue()
                with st.spinner("Uploading and indexing..."):
                    result = api.upload(token, uploaded.name, content)
                invalidate_documents()
                name = result["document"]
                S.selected_doc = name
                S.preview_bytes = content if name.lower().endswith(".pdf") else None
                S.preview_name = name if S.preview_bytes else None
                S.preview_cleared = False
                S.focus = None
                st.success(f"Indexed {result['pages']} page(s) / {result['chunks']} passages.")
                st.rerun()
            except api.ApiError as e:
                _api_error(e, "Upload failed")
    if cfg.get("allow_url_ingest"):
        url = st.text_input(
            "…or add a web page / PDF by URL", key="ingest_url", placeholder="https://"
        )
        if url and st.button("＋ Fetch and index", key="index_url", use_container_width=True):
            try:
                with st.spinner("Fetching and indexing..."):
                    result = api.ingest_url(token, url.strip())
                invalidate_documents()
                S.selected_doc = result["document"]
                S.preview_bytes = S.preview_name = None
                st.success(f"Indexed {result['document']}.")
                st.rerun()
            except api.ApiError as e:
                _api_error(e, "Could not add the URL")


def _account_panel(token: str) -> None:
    with st.form("docqa_change_password", clear_on_submit=True):
        old = st.text_input("Current password", type="password")
        new = st.text_input("New password (minimum 10 characters)", type="password")
        if st.form_submit_button("Change password", use_container_width=True):
            try:
                new_token = api.change_password(token, old, new)
                S.auth_token = new_token
                persist_token(new_token)
                st.success("Password changed. Other sessions were signed out.")
            except api.ApiError as e:
                _api_error(e, "Could not change password")
    # Loaded only when asked for: a collapsed expander still RUNS its code on every rerun, and the
    # statistics are three database queries.
    if not st.toggle("Show usage statistics", key="show_usage"):
        return
    try:
        stats = load_stats(token)
    except api.ApiError:
        return
    st.markdown(f"**Last {stats['days']} days**")
    c1, c2 = st.columns(2)
    c1.metric("Questions", stats["questions"])
    rate = stats["refusal_rate"]
    c2.metric("Not answerable", f"{rate:.0%}" if rate is not None else "-")
    if stats["avg_latency_ms"] is not None:
        st.caption(
            f"Average response time: {stats['avg_latency_ms'] / 1000:.1f} s · "
            f"👍 {stats['feedback_up']} / 👎 {stats['feedback_down']}"
        )
    if stats["recent_unanswered"]:
        st.caption("Recent questions the documents could not answer:")
        for q in stats["recent_unanswered"][:5]:
            st.caption(f"• {q}")


# ---------------------------------------------------------------------------- middle
def render_middle(token: str) -> None:
    with st.container(border=True):
        render_html(
            '<div class="panel-heading"><span class="panel-icon">▤</span>Document Viewer</div>'
        )
        selected = S.selected_doc
        name = html.escape(selected) if selected else "No document selected"
        render_html(f'<div class="viewer-toolbar">📄 &nbsp; {name}</div>')
        is_pdf = bool(selected) and selected.lower().endswith(".pdf")

        if S.focus and S.focus.get("source") == selected:
            _cited_page(token)
            return
        if selected and not is_pdf:
            st.info(
                "Previews are available for PDF files. This document is indexed and searchable; "
                "citations show the matching passage."
            )
            return

        if is_pdf and S.preview_bytes is None and not S.preview_cleared:
            try:
                with st.spinner("Loading original PDF..."):
                    data = api.fetch_file(token, selected)
                if not data.startswith(b"%PDF-"):
                    raise api.ApiError(0, "The backend did not return a valid PDF.")
                S.preview_bytes, S.preview_name, S.preview_cleared = data, selected, False
            except api.ApiError as e:
                if e.status == 404:
                    st.warning("Original PDF is unavailable. Please upload the document again.")
                else:
                    _api_error(e, "PDF loading error")

        if S.preview_bytes is not None and S.preview_name == selected:
            if st.button("✕ Clear Preview", key="clear_preview", use_container_width=True):
                clear_preview()
                st.rerun()
            try:
                st.pdf(S.preview_bytes, height=680, key="document_pdf_viewer")
            except Exception as e:  # noqa: BLE001
                st.error(f"PDF viewer error: {e}")
        else:
            render_html("""
            <div class="viewer-empty"><div class="viewer-icon">📑</div>
            <div class="viewer-title">Document Preview Workspace</div>
            <div class="viewer-description">Select a document from Indexed Sources.<br>
            Click <b>Open Document</b> to preview it here.<br><br>Your document viewer is ready.</div></div>
            """)
            if selected and S.preview_cleared:
                st.caption(
                    "Preview cleared. Your indexed document is safe. Click Open Document to display it again."
                )


def _cited_page(token: str) -> None:
    f = S.focus
    st.caption(
        f"Cited passage on page {f['page']} (highlighted in yellow when the text can be located)"
    )
    try:
        key = (f["source"], f["page"], f["quote"])
        if S.get("focus_key") != key:
            S.focus_image = api.page_image(token, f["source"], f["page"], f["quote"])
            S.focus_key = key
        st.image(S.focus_image, use_container_width=True)
    except api.ApiError as e:
        _api_error(e, "Could not render that page")
    if st.button("← Back to the full document", key="close_focus", use_container_width=True):
        S.focus = None
        st.rerun()


# ----------------------------------------------------------------------------- right
def render_right(token: str, docs: list[str], demo: bool) -> None:
    with st.container(border=True):
        if docs:
            greeting = (
                f"Hello! I've indexed {len(docs)} document(s). Ask me anything about them, or pick a "
                "suggestion below. Every answer shows whether it came from your documents."
            )
        else:
            greeting = "Hello! Upload a document on the left to get started. " + (
                "General questions are switched on, so you can also just chat with me."
                if S.allow_general
                else "Tip: switch on 'Also answer general questions' to chat without documents."
            )
        render_html(f"""
        <div class="panel-heading"><span class="panel-icon">✧</span>Multimodal RAG Assistant</div>
        <div class="assistant-card">
            <div class="assistant-header"><div class="assistant-avatar">AI</div>
                <div><div class="assistant-title">DocQA Assistant</div>
                <div class="assistant-subtitle">Intelligent Document Analysis</div></div></div>
            <div class="assistant-message">{html.escape(greeting)}</div>
        </div>""")

        with st.form("docqa_question_form", clear_on_submit=True):
            typed = st.text_input(
                "Your question",
                placeholder="Ask your documents anything...",
                label_visibility="collapsed",
                key="docqa_question_text",
            )
            submitted = st.form_submit_button("Send question  ➤", use_container_width=True)
        question = typed.strip() if submitted and typed else None
        if S.pending_question:
            question, S.pending_question = S.pending_question, None

        if not S.history and not question:
            render_html('<div class="section-label">✨ SUGGESTED QUESTIONS</div>')
            for i, text in enumerate(
                [
                    "Summarize the document",
                    "What are the key findings?",
                    "Find important statistics",
                ]
            ):
                if st.button(f"✧  {text}", key=f"suggestion_{i}", use_container_width=True):
                    S.pending_question = text
                    st.rerun()

        render_html('<div class="section-label">💬 CONVERSATION — LATEST FIRST</div>')
        if question:
            _ask(token, question, demo)

        history = S.history
        pairs = [(history[i], history[i + 1]) for i in range(0, len(history) - 1, 2)]
        for idx, (u, a) in reversed(list(enumerate(pairs))):
            render_pair(u, a, idx)
        if not history and not question:
            st.caption("No messages yet. Ask something above.")


def _ask(token: str, question: str, demo: bool) -> None:
    if demo and S.asked >= SESSION_LIMIT:
        st.warning(
            f"Demo limit reached ({SESSION_LIMIT} questions per session). Thanks for trying it!"
        )
        return
    S.asked += 1
    with st.chat_message("user"):
        st.markdown(question)
    if S.agent_mode:
        path = "/agent/ask/stream"
        payload = {
            "question": question,
            "allow_general": S.allow_general,
            "history": [{"role": m["role"], "content": m["content"]} for m in S.history],
        }
    else:
        path, payload = "/ask/stream", {"question": question, "mode": S.retrieval_mode}

    final: dict = {}

    def tokens():
        for event in api.stream_events(path, token, payload, _forwarded_for()):
            if event["type"] == "token":
                yield event["text"]
            elif event["type"] == "final":
                final.update(event["data"])
            elif event["type"] == "error":
                raise api.ApiError(event.get("status", 500), event.get("detail", "Unknown error"))

    with st.chat_message("assistant"):
        try:
            with st.spinner("AI is analyzing your documents..."):
                streamed = st.write_stream(tokens())
        except api.ApiError as e:
            if e.status == 429:
                st.warning(e.detail)
            else:
                _api_error(e, "Request failed")
            return
        if not final:
            st.error("The server closed the connection before finishing. Please try again.")
            return
        answer = (
            final.get("answer")
            or (streamed if isinstance(streamed, str) else "")
            or "No answer returned."
        )
        invalidate_stats()  # the question count changed
        S.history.append({"role": "user", "content": question})
        S.history.append(
            {
                "role": "assistant",
                "content": answer,
                "question": question,
                "citations": final.get("citations", []),
                "citation_audit": final.get("citation_audit", {}),
                "confidence": final.get("confidence"),
                "top_score": final.get("top_score"),
                "meta": {
                    "from_documents": final.get("from_documents", not final.get("refused", False)),
                    "refused": final.get("refused", False),
                },
            }
        )
    st.rerun()
