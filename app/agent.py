"""LangGraph agent on top of the RAG pipeline.

rewrite follow-up -> route -> SEARCH (RAG) | SUMMARIZE | GENERAL | CHAT | CLARIFY | NODOCS
Every result carries `from_documents` so the UI can label answers that are NOT grounded in the PDFs.
"""

import logging
from collections.abc import Iterator
from typing import TypedDict

from langgraph.graph import END, StateGraph

from . import llm, rag
from .utils import pick_sources

log = logging.getLogger(__name__)

GENERAL_SYSTEM = (
    "You are a helpful assistant. Answer from general knowledge, clearly and concisely. "
    "If you are unsure, say so. Never claim the answer comes from the user's documents."
)
ROUTES = {"SEARCH", "SUMMARIZE", "GENERAL", "CHAT", "CLARIFY"}


class AgentState(TypedDict, total=False):
    question: str
    history: list
    documents: list
    allow_general: bool
    user_id: str
    standalone: str
    route: str
    result: dict


def _fmt_history(history: list, n: int = 6) -> str:
    return "\n".join(f"{m['role']}: {m['content']}" for m in (history or [])[-n:])


def _plain(text: str, from_documents: bool = False) -> dict:
    return {"answer": text, "citations": [], "refused": False, "from_documents": from_documents}


def rewrite(state: AgentState) -> dict:
    """Turn a follow-up like 'and for contractors?' into a standalone question. If the rewrite
    call fails the original question is used, so a hiccup never kills the whole request."""
    if not state.get("history"):
        return {"standalone": state["question"]}
    prompt = (
        "Rewrite the user's last message as a single standalone question, using the "
        "conversation for context. Output only the question.\n\n"
        f"Conversation:\n{_fmt_history(state['history'])}\n\n"
        f"Last message: {state['question']}"
    )
    try:
        return {"standalone": llm.chat("You rewrite questions.", prompt) or state["question"]}
    except llm.LLMError as exc:
        log.warning("Question rewrite failed, using the original: %s", exc)
        return {"standalone": state["question"]}


def route(state: AgentState) -> dict:
    docs_list = state.get("documents") or []
    question = (state.get("standalone") or state["question"]).strip()
    q = question.lower()

    # Deterministic intent rules take priority over the classifier for document-wide
    # requests. This prevents common requests such as "summarize the document" from
    # being incorrectly sent to GENERAL.
    summary_terms = ("summarize", "summarise", "summary", "overview", "key points")
    if docs_list and any(term in q for term in summary_terms):
        return {"route": "SUMMARIZE"}
    cross_doc_terms = (
        "all documents", "all three", "across all", "across the policies",
        "between the policies", "compare", "comparison", "conflicting",
        "conflict", "contradict", "differences between", "which policies",
    )
    if docs_list and any(term in q for term in cross_doc_terms):
        return {"route": "SEARCH"}

    docs = ", ".join(docs_list) or "none uploaded yet"
    prompt = (
        f"Available documents: {docs}\n"
        f"User message: {state['standalone']}\n\n"
        "Reply with exactly one word:\n"
        "SEARCH    - asks for specific facts that may be in the documents\n"
        "SUMMARIZE - asks for a summary, overview or key points of a whole document\n"
        "GENERAL   - general-knowledge question clearly unrelated to the documents\n"
        "CHAT      - greeting, thanks, or a question about what you can do\n"
        "CLARIFY   - too vague to search (e.g. 'what about it?')\n"
        "IMPORTANT: If documents are uploaded, prefer SEARCH for questions that could be answered "
        "from them. Use GENERAL only when the question is clearly unrelated to every document."
    )
    try:
        raw = llm.chat("You are a routing classifier.", prompt).strip().upper()
    except llm.LLMError as exc:
        log.warning("Routing failed, defaulting to SEARCH: %s", exc)
        raw = "SEARCH"
    word = raw.split()[0].strip(".:,") if raw else "SEARCH"
    word = word if word in ROUTES else "SEARCH"
    if word == "GENERAL" and not state.get("allow_general"):
        word = "SEARCH"  # documents-only mode: let retrieval decide (it will refuse if unsupported)
    if not docs_list and word in {"SEARCH", "SUMMARIZE"}:
        word = "GENERAL" if state.get("allow_general") else "NODOCS"
    return {"route": word}


def search(state: AgentState) -> dict:
    out = rag.answer(state["standalone"], "hybrid_rerank", state.get("user_id", "default"))
    out.pop("retrieved", None)
    if out["refused"] and state.get("allow_general"):
        # Not in the documents: answer from general knowledge, but the UI will label it clearly.
        return {"result": _plain(llm.chat(GENERAL_SYSTEM, state["standalone"]))}
    out["from_documents"] = not out["refused"]
    return {"result": out}


def summarize(state: AgentState) -> dict:
    sources = pick_sources(state["standalone"], state.get("documents") or [])
    out = rag.summarize(state["standalone"], sources, user_id=state.get("user_id", "default"))
    return {"result": {**out, "refused": False, "from_documents": True}}


def general(state: AgentState) -> dict:
    return {"result": _plain(llm.chat(GENERAL_SYSTEM, state["standalone"]))}


def chat(state: AgentState) -> dict:
    text = llm.chat(
        "You are a friendly assistant for a document Q&A tool. Keep replies to 1-2 sentences. "
        "Do not invent facts about documents.",
        state["standalone"],
    )
    return {"result": _plain(text)}


def clarify(state: AgentState) -> dict:
    text = llm.chat(
        "Ask ONE short clarifying question so the user's request becomes searchable.",
        state["standalone"],
    )
    return {"result": _plain(text)}


def nodocs(state: AgentState) -> dict:
    return {
        "result": _plain(
            "There are no documents indexed yet. Upload a document in the sidebar, "
            "or switch on general-knowledge answers to ask me anything else."
        )
    }


NODES = {
    "search": search,
    "summarize": summarize,
    "general": general,
    "chat": chat,
    "clarify": clarify,
    "nodocs": nodocs,
}


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("rewrite", rewrite)
    g.add_node("route", route)
    for name, fn in NODES.items():
        g.add_node(name, fn)
    g.set_entry_point("rewrite")
    g.add_edge("rewrite", "route")
    g.add_conditional_edges("route", lambda s: s["route"].lower(), {t: t for t in NODES})
    for t in NODES:
        g.add_edge(t, END)
    return g.compile()


_graph = None


def _initial(question, history, documents, allow_general, user_id) -> AgentState:
    return {
        "question": question,
        "history": history or [],
        "documents": documents or [],
        "allow_general": allow_general,
        "user_id": user_id,
    }


def _wrap(result: dict, state: AgentState) -> dict:
    result = dict(result)
    result.setdefault("from_documents", False)
    return {**result, "route": state["route"], "standalone_question": state["standalone"]}


def run(
    question: str,
    history: list | None = None,
    documents: list | None = None,
    allow_general: bool = False,
    user_id: str = "default",
) -> dict:
    global _graph
    if _graph is None:
        _graph = build_graph()
    final = _graph.invoke(_initial(question, history, documents, allow_general, user_id))
    return _wrap(final["result"], final)


def run_stream(
    question: str,
    history: list | None = None,
    documents: list | None = None,
    allow_general: bool = False,
    user_id: str = "default",
) -> Iterator[dict]:
    """Same decisions as ``run`` but streams the answer text for document searches.

    Yields {"type": "token", "text": ...} events, then one {"type": "final", "data": {...}}.
    Routes that are not a document search produce just the final event.
    """
    state = _initial(question, history, documents, allow_general, user_id)
    state.update(rewrite(state))
    state.update(route(state))

    if state["route"] != "SEARCH":
        state.update(NODES[state["route"].lower()](state))
        yield {"type": "final", "data": _wrap(state["result"], state)}
        return

    final = None
    for event in rag.answer_stream(state["standalone"], "hybrid_rerank", user_id):
        if event["type"] == "token":
            yield event
        else:
            final = event["data"]
    if final["refused"] and allow_general:
        parts = []
        for piece in llm.chat_stream(GENERAL_SYSTEM, state["standalone"]):
            parts.append(piece)
            yield {"type": "token", "text": piece}
        result = _plain("".join(parts).strip())
    else:
        result = {**final, "from_documents": not final["refused"]}
    yield {"type": "final", "data": _wrap(result, state)}
