
"""Reusable Streamlit pieces: answer badges, citations, feedback buttons."""

import html
import re

import streamlit as st

from ui import api

CONFIDENCE_ICON = {"high": "🟢", "medium": "🟡", "low": "🟠"}


def clean_text(value) -> str:
    """Clean common UTF-8 mojibake and normalize text."""
    if value is None:
        return ""

    text = str(value)

    replacements = {
        "â€™": "'",
        "â€˜": "'",
        "â€œ": '"',
        "â€\u009d": '"',
        "â€“": "–",
        "â€”": "—",
        "â€¦": "...",
        "Â ": " ",
        "Â": "",
        "ã": "[",
        "ã": "]",
        "ã": "",
        "â": "",
        "ï»¿": "",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    # Remove remaining replacement/control characters.
    text = text.replace("\ufffd", "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)

    return text


def clean_answer(text: str) -> str:
    """Clean answer text and remove raw internal citation markers."""
    text = clean_text(text)

    # Remove malformed citation markers such as ã1ã.
    text = re.sub(r"ã.*?ã", "", text)

    # Remove common internal citation markers, if rendered in the answer.
    text = re.sub(r"【\s*\d+\s*†[^】]*】", "", text)
    text = re.sub(r"【\s*\d+\s*】", "", text)

    return text.strip()


def render_html(content: str) -> None:
    st.html(content)


def render_badge(meta: dict | None) -> None:
    if not meta:
        return

    if meta.get("from_documents") is True:
        render_html(
            '<span class="badge badge-doc">📄 From your documents</span>'
        )
    elif not meta.get("refused"):
        render_html(
            '<span class="badge badge-gen">'
            '🌐 General answer, not from your documents'
            '</span>'
        )


def render_confidence(message: dict) -> None:
    label = message.get("confidence")

    if label and message.get("meta", {}).get("from_documents"):
        score = message.get("top_score")
        extra = (
            f" (retrieval score {score:.1f})"
            if isinstance(score, (int, float))
            else ""
        )
        st.caption(
            f"{CONFIDENCE_ICON.get(label, '')} "
            f"Retrieval confidence: {label}{extra}"
        )


def render_citations(citations: list[dict], msg_index: int) -> None:
    if not citations:
        return

    st.markdown("**Sources**")

    for citation_idx, citation in enumerate(citations):
        source = clean_text(citation.get("source", "Document"))
        page = citation.get("page", "N/A")
        heading = clean_text(citation.get("heading", ""))

        title = f"📄 {source} — Page {page}"

        if heading:
            title += f" · {heading}"

        if citation.get("cited") is False:
            title += " (retrieved, not cited)"

        with st.expander(title):
            reference = clean_text(citation.get("ref", "N/A"))
            snippet = clean_text(citation.get("snippet", ""))

            st.caption(f"Reference: {reference}")

            if snippet:
                st.write(snippet)

            if (
                source.lower().endswith(".pdf")
                and isinstance(page, int)
            ):
                key = f"cite_{msg_index}_{citation_idx}_{reference}"

                if st.button("👁 Show this page", key=key):
                    st.session_state.focus = {
                        "source": source,
                        "page": page,
                        "quote": snippet,
                    }
                    st.session_state.selected_doc = source
                    st.rerun()


def render_audit(audit: dict) -> None:
    if not audit:
        return

    if audit.get("invalid_refs"):
        st.warning(
            f"Citation audit: invalid reference(s) "
            f"{audit['invalid_refs']}"
        )

    elif (
        audit.get("has_citations")
        and audit.get("semantic_support_rate") is None
    ):
        st.caption("Citation references are structurally valid.")

    if audit.get("semantic_support_rate") is not None:
        st.caption(
            "Semantic citation support score: "
            f"{audit['semantic_support_rate']:.0%}"
        )



def render_feedback(message: dict, msg_index: int) -> None:
    """Render clear, labelled feedback buttons below each AI answer."""

    question = message.get("question")

    if not question:
        return

    existing_feedback = message.get("feedback")

    if existing_feedback is not None:
        if existing_feedback > 0:
            st.caption("✅ Thank you! You marked this answer as helpful.")
        else:
            st.caption("📝 Thank you! Your feedback has been recorded.")
        return

    st.markdown(
        '<div class="feedback-heading">Was this answer helpful?</div>',
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns(2, gap="small")

    with col1:
        helpful = st.button(
            "👍  Helpful",
            key=f"feedback_helpful_{msg_index}",
            use_container_width=True,
        )

    with col2:
        not_helpful = st.button(
            "👎  Not helpful",
            key=f"feedback_not_helpful_{msg_index}",
            use_container_width=True,
        )

    rating = 1 if helpful else (-1 if not_helpful else None)

    if rating is not None:
        try:
            api.send_feedback(
                st.session_state.auth_token,
                question,
                message.get("content", ""),
                rating,
            )

            message["feedback"] = rating
            st.rerun()

        except api.ApiError as e:
            st.error(f"Could not send feedback: {e.detail}")


def render_pair(
    user_message: dict,
    assistant_message: dict,
    index: int,
) -> None:

    with st.chat_message("user"):
        st.markdown(clean_text(user_message.get("content", "")))

    with st.chat_message("assistant"):
        render_badge(assistant_message.get("meta"))

        answer = clean_answer(
            assistant_message.get("content", "")
        )
        st.markdown(answer)

        render_confidence(assistant_message)

        render_citations(
            assistant_message.get("citations", []),
            index,
        )

        render_audit(
            assistant_message.get("citation_audit", {})
        )

        render_feedback(assistant_message, index)


def doc_card(name: str, selected: bool) -> str:
    cls = "doc-card selected" if selected else "doc-card"

    return f"""
    <div class="{cls}">
        <div class="doc-name">📄 {html.escape(name)}</div>
        <div class="doc-description">Indexed document collection</div>
        <div style="color:#10d9a0;font-size:10px;margin-top:10px;">
            ● Indexed successfully
        </div>
    </div>
    """
