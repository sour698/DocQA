"""Create a 100-150 item, evidence-grounded QA set from real documents in a folder.

Answerable items are LLM-drafted but only kept when their verbatim evidence quote appears on the
exact cited page. Unanswerable items cannot be generated honestly, so they come from you:

    python -m eval.build_real_corpus ./my_real_pdfs --count 120 \
        --unanswerable eval/unanswerable.mine.json

(see eval/unanswerable.template.json for the format; aim for 20-30, checked by hand).
"""

import argparse
import json
import re
from pathlib import Path

from app.ingest import load_document
from app.llm import chat
from app.utils import ALLOWED_EXTENSIONS

SYSTEM = """You are creating a rigorous document QA benchmark. Use ONLY the supplied page excerpts.
Return a JSON array of question objects with keys: question, answer, evidence, source, page, answerable.
Every answerable item must have one concise correct answer and a verbatim evidence quote copied from its page.
Do not infer facts, use outside knowledge, duplicate questions, or invent page numbers. Ask specific questions with unambiguous answers.
Return only valid JSON. Do not create unanswerable questions; these are written separately by human reviewers."""


def validate_item(q, batch, seen_questions):
    """Return a benchmark row for a generated item, or None if it fails any check."""
    if not isinstance(q, dict):
        return None
    src, page = q.get("source"), q.get("page")
    evidence = str(q.get("evidence", "")).strip()
    answer = str(q.get("answer", "")).strip()
    question = str(q.get("question", "")).strip()
    match = next((p for p in batch if p["source"] == src and p["page"] == page), None)
    if not match or not all([evidence, answer, question]):
        return None
    if " ".join(evidence.lower().split()) not in " ".join(match["text"].lower().split()):
        return None  # the quote must really be on that page
    if question.casefold() in seen_questions:
        return None
    # Keep numbers of any length ("24"): they are the most discriminating keywords.
    words = [
        w.lower()
        for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9.%/-]*", answer)
        if len(w) > 2 or any(ch.isdigit() for ch in w)
    ]
    return {
        "question": question,
        "answerable": True,
        "source": src,
        "page": page,
        "keywords": words[:8] or [answer.lower()],
        "gold_answer": answer,
        "evidence": evidence,
    }


def load_unanswerable(path: Path) -> list[dict]:
    items = json.loads(path.read_text(encoding="utf-8"))
    out = [
        {"question": str(i["question"]).strip(), "answerable": False}
        for i in items
        if str(i.get("question", "")).strip()
    ]
    if not out:
        raise SystemExit(f"No questions found in {path}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("doc_dir", type=Path)
    ap.add_argument("--count", type=int, default=120, help="answerable questions (100-150)")
    ap.add_argument("--output", type=Path, default=Path("eval/questions.real.json"))
    ap.add_argument(
        "--unanswerable", type=Path, help="JSON file of hand-written unanswerable questions"
    )
    args = ap.parse_args()
    if not 100 <= args.count <= 150:
        raise SystemExit("--count must be between 100 and 150")
    files = sorted(p for p in args.doc_dir.iterdir() if p.suffix.lower() in ALLOWED_EXTENSIONS)
    if not files:
        raise SystemExit(f"No supported documents found in {args.doc_dir}")
    pages = []
    for f in files:
        for page, text in load_document(f):
            if len(text) > 120:
                pages.append({"source": f.name, "page": page, "text": text[:9000]})
    if not pages:
        raise SystemExit("No readable text. Install OCR dependencies for scanned files.")
    items, seen = [], set()
    per_batch = max(1, min(8, args.count))
    for i in range(0, len(pages), 4):
        if len(items) >= args.count:
            break
        batch = pages[i : i + 4]
        prompt = (
            f"Generate up to {min(per_batch, args.count - len(items))} distinct questions from "
            "these pages.\n" + json.dumps(batch, ensure_ascii=False)
        )
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", chat(SYSTEM, prompt).strip(), flags=re.I)
        try:
            generated = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for q in generated if isinstance(generated, list) else []:
            row = validate_item(q, batch, seen)
            if row:
                items.append(row)
                seen.add(row["question"].casefold())
                if len(items) >= args.count:
                    break
    if len(items) < args.count:
        raise SystemExit(
            f"Only {len(items)} evidence-validated questions were generated. Add more real "
            "documents or rerun; no fabricated items were written."
        )
    unanswerable = load_unanswerable(args.unanswerable) if args.unanswerable else []
    if len(unanswerable) < 20:
        print(
            f"WARNING: {len(unanswerable)} unanswerable questions. Write at least 20 by hand so the "
            "hallucination rate means something."
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(items + unanswerable, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(
        f"Wrote {len(items)} answerable + {len(unanswerable)} unanswerable questions to {args.output}"
    )


if __name__ == "__main__":
    main()
