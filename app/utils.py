"""Pure helper functions (no heavy dependencies) so they are easy to unit test."""

import hashlib
import re
import unicodedata
from pathlib import Path


def tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def rrf(rankings: list[list[str]], k: int = 60) -> list[str]:
    """Reciprocal Rank Fusion: merge several ranked id lists into one."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item_id in enumerate(ranking):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=scores.get, reverse=True)


_DASHES = {ord(c): "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"}


def normalize_text(text: str) -> str:
    """Make answers comparable: LLMs (esp. gpt-oss) emit narrow no-break spaces, special
    hyphens, markdown bold and '8,000' vs '8000'. Normalise all of that before matching."""
    t = unicodedata.normalize("NFKC", text).translate(_DASHES).lower()
    t = t.replace("*", "")
    t = re.sub(r"(?<=\d),(?=\d{3})", "", t)
    return re.sub(r"\s+", " ", t).strip()


def sample_evenly(items: list, n: int) -> list:
    """Pick up to n items spread evenly from first to last (keeps document order)."""
    if n <= 0 or not items:
        return []
    if len(items) <= n:
        return list(items)
    if n == 1:
        return [items[0]]
    step = (len(items) - 1) / (n - 1)
    return [items[round(i * step)] for i in range(n)]


_STOP = {"the", "pdf", "and", "for", "doc", "docx"}


def pick_sources(question: str, documents: list[str]) -> list[str]:
    """If the question names a document ('summarize the leave policy'), return only the best
    matching one(s); otherwise (no match, or a generic question) return all documents."""

    def words(t):  # splits on _ and - too
        return re.findall(r"[a-z0-9]+", normalize_text(t))

    q_tokens = set(words(question))
    best, scored = 0, []
    for d in documents:
        tokens = {t for t in words(d.rsplit(".", 1)[0]) if len(t) >= 3 and t not in _STOP}
        score = len(tokens & q_tokens)
        scored.append((d, score))
        best = max(best, score)
    if best == 0:
        return list(documents)
    return [d for d, score in scored if score == best]


def user_key(user_id: str) -> str:
    """The one place that turns an account id into the on-disk / collection key."""
    return hashlib.sha256(str(user_id).encode()).hexdigest()[:20]


ALLOWED_EXTENSIONS = (".pdf", ".docx", ".txt", ".md", ".html", ".htm")
_BAD_NAME_CHARS = re.compile(r"[^\w.\- ()]+", re.UNICODE)


def safe_filename(name: str, allowed: tuple[str, ...] = ALLOWED_EXTENSIONS) -> str:
    """Return a harmless file name, or raise ValueError.

    Strips directories, control characters and anything that could break paths or the
    ``source::p1::c0`` chunk-id format (such as ':'), and enforces the extension allow-list.
    """
    base = Path((name or "").replace("\\", "/")).name.strip()
    base = _BAD_NAME_CHARS.sub("_", base).strip(" .")
    if not base or base in {".", ".."}:
        raise ValueError("Invalid file name.")
    if len(base) > 150:
        stem, dot, ext = base.rpartition(".")
        base = stem[: 150 - len(ext) - 1] + dot + ext
    if not base.lower().endswith(allowed):
        raise ValueError("Unsupported file type. Allowed: " + ", ".join(allowed))
    return base


def contextual_text(chunk: dict) -> str:
    """Chunk text with its document title and section heading in front.

    Used for embeddings and reranking so a passage \"knows\" which document/section it is from.
    """
    title = Path(chunk.get("source", "")).stem.replace("_", " ").replace("-", " ")
    heading = chunk.get("heading") or ""
    head = f"{title}. {heading}".strip(" .")
    return f"{head}\n{chunk['text']}" if head else chunk["text"]
