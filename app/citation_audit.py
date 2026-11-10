"""Structural and optional semantic citation verification."""

import json
import re

from . import llm


def audit(answer: str, hits: list[dict], semantic: bool = False) -> dict:
    refs = [int(x) for x in re.findall(r"(?<!\w)\[(\d+)\](?!\w)", answer or "")]
    valid = sorted({r for r in refs if 1 <= r <= len(hits)})
    invalid = sorted({r for r in refs if r < 1 or r > len(hits)})
    result = {
        "citation_refs": refs,
        "valid_refs": valid,
        "invalid_refs": invalid,
        "has_citations": bool(refs),
        "structurally_valid": bool(refs) and not invalid,
    }
    if semantic and valid:
        supported = []
        for ref in valid:
            prompt = (
                "Assess whether this passage supports the answer's factual claims that cite "
                f'[{ref}]. Return JSON only: {{"supported":true/false,"reason":"short"}}.\n'
                f"ANSWER: {answer}\nPASSAGE: {hits[ref - 1]['text']}"
            )
            try:
                raw = llm.chat(
                    "You are a strict citation auditor. Do not assume support from topic similarity.",
                    prompt,
                )
                m = re.search(r"\{.*\}", raw, re.S)
                obj = json.loads(m.group(0)) if m else {}
                supported.append(
                    {
                        "ref": ref,
                        "supported": bool(obj.get("supported")),
                        "reason": str(obj.get("reason", ""))[:300],
                    }
                )
            except Exception as exc:  # audit must never break an answer
                supported.append(
                    {
                        "ref": ref,
                        "supported": None,
                        "reason": f"Audit unavailable: {str(exc)[:120]}",
                    }
                )
        result["semantic_checks"] = supported
        checked = [x for x in supported if x["supported"] is not None]
        result["semantic_support_rate"] = (
            sum(x["supported"] for x in checked) / len(checked) if checked else None
        )
    return result
