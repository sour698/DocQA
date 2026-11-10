"""LLM-as-judge for answer correctness.

The keyword check in ``metrics.is_correct`` is a cheap proxy: it misses correct answers phrased
differently and accepts answers that merely mention the keywords. A judge model reads the question,
the reference answer and the system's answer and decides if they agree. Still an automated
estimate - spot-check a sample by hand (see README) and prefer a different model family than the
one under test for the judge, to avoid self-preference bias.
"""

import json
import re

from app import llm

JUDGE_SYSTEM = (
    "You are a strict grader. Given a QUESTION, a REFERENCE answer and a CANDIDATE answer, decide "
    "whether the candidate states the same facts as the reference (wording may differ; extra "
    "correct detail is fine; a missing or contradicting key fact is a fail). "
    'Reply with JSON only: {"correct": true|false, "reason": "<short>"}'
)


def reference_for(q: dict) -> str:
    return q.get("gold_answer") or ", ".join(q.get("keywords", []))


def parse_verdict(raw: str) -> bool | None:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        value = json.loads(m.group(0)).get("correct")
    except ValueError:
        return None
    return value if isinstance(value, bool) else None


def judge(question: str, reference: str, answer: str) -> bool | None:
    """True/False verdict, or None when the judge call fails or is unparsable."""
    prompt = f"QUESTION: {question}\nREFERENCE: {reference}\nCANDIDATE: {answer}"
    try:
        return parse_verdict(llm.chat(JUDGE_SYSTEM, prompt))
    except llm.LLMError:
        return None
