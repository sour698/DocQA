"""Turn 👎 feedback into raw material for your evaluation set.

    python -m scripts.export_feedback --out eval/feedback_candidates.json

Each exported item is a question real users were unhappy with. REVIEW them by hand: decide
whether the right behaviour was an answer (add `source`, `page`, `keywords`, set `answerable`:
true) or a refusal (`answerable`: false), then copy them into your question set.
"""

import argparse
import json
from pathlib import Path

from app import db


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("eval/feedback_candidates.json"))
    ap.add_argument("--user-id", help="only this account (default: all)")
    a = ap.parse_args()
    db.wait_ready()
    sql = "SELECT question, answer, comment FROM feedback WHERE rating < 0"
    with db.connection() as conn:
        rows = conn.execute(
            sql + (" AND user_id = %s" if a.user_id else "") + " ORDER BY ts",
            (a.user_id,) if a.user_id else (),
        ).fetchall()
    seen, items = set(), []
    for r in rows:
        question, answer, comment = r["question"], r["answer"], r["comment"]
        if question.casefold() in seen:
            continue
        seen.add(question.casefold())
        items.append(
            {
                "question": question,
                "answerable": None,
                "observed_answer": answer,
                "user_comment": comment,
                "source": None,
                "page": None,
                "keywords": [],
            }
        )
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(items)} candidate questions to {a.out}")


if __name__ == "__main__":
    main()
