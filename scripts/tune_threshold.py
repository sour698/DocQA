"""Pick REFUSAL_THRESHOLD from data instead of guessing. Needs NO LLM calls (retrieval only, fast).

    python -m scripts.tune_threshold eval/questions.sample.json

It computes the top reranker score for every question, then:
  * tunes the threshold on a DEV half (every other question)
  * reports the error on the held-out TEST half, so you don't fool yourself
Set EVAL_USER_ID to tune on one account's documents. Stop the API first (two processes
should not open the vector database at once).
"""

import json
import os
import sys
from pathlib import Path

from app import rag
from eval.metrics import pick_threshold


def top_score(question: str) -> float:
    hits = rag.retrieve(question, "hybrid_rerank", os.getenv("EVAL_USER_ID", "default"))
    return hits[0]["score"] if hits else float("-inf")


def split_scores(items):
    pos = [s for q, s in items if q["answerable"]]
    neg = [s for q, s in items if not q["answerable"]]
    return pos, neg


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("eval/questions.json")
    questions = json.loads(path.read_text(encoding="utf-8"))
    scored = [(q, top_score(q["question"])) for q in questions]
    scored = [(q, s) for q, s in scored if s != float("-inf")]

    print("\nTop reranker score per question (sorted):")
    for q, s in sorted(scored, key=lambda x: x[1]):
        print(
            f"  {s:8.2f}  {'ANSWERABLE  ' if q['answerable'] else 'unanswerable'}  {q['question'][:70]}"
        )

    dev, test = scored[::2], scored[1::2]
    dpos, dneg = split_scores(dev)
    tpos, tneg = split_scores(test)
    best = pick_threshold(dpos, dneg)
    t = best["threshold"]
    t_fr = sum(s < t for s in tpos) / len(tpos) if tpos else 0.0
    t_leak = sum(s >= t for s in tneg) / len(tneg) if tneg else 0.0

    print(f"\nRecommended REFUSAL_THRESHOLD = {t}   (tuned on {len(dev)} dev questions)")
    print(
        f"  dev : false refusal {best['false_refusal']:.1%}, unanswerable accepted {best['leak']:.1%}"
    )
    print(
        f"  test: false refusal {t_fr:.1%}, unanswerable accepted {t_leak:.1%}   <- honest numbers"
    )
    print("\nPut it in .env as REFUSAL_THRESHOLD=" + str(t) + " and re-run only hybrid:")
    print("  MODES=hybrid_rerank python -m eval.run_eval eval/questions.sample.json")
    print(
        "\nNote: near-miss questions can score high (the topic IS in the documents); the prompt, "
        "not the threshold, has to catch those. Overlap between the two groups is normal."
    )


if __name__ == "__main__":
    main()
