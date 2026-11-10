"""Evaluate RETRIEVAL on its own. Needs NO LLM calls (zero tokens, runs in about a minute).

    python -m eval.retrieval_eval eval/questions.sample.json

Compares four retrievers on every answerable question:
  dense (naive) | BM25 only | hybrid (RRF) | hybrid + cross-encoder rerank
Metrics: hit@1 / hit@3 / hit@5 (right page among the top-k) and MRR.
Set EVAL_USER_ID to evaluate one account's documents. Stop the API first (two processes
should not open the vector database at once).
"""

import json
import os
import sys
from pathlib import Path

from app.config import CANDIDATES
from app.store import get_store
from eval.metrics import first_hit_rank, retrieval_summary

K = 5
HERE = Path(__file__).parent


def retrievers(store, q: str) -> dict[str, list[str]]:
    return {
        "dense (naive)": store.dense_search(q, K),
        "bm25 only": store.bm25_search(q, K),
        "hybrid (RRF)": store.hybrid_search(q, K),
        "hybrid + rerank": [
            r["id"] for r in store.rerank(q, store.hybrid_search(q, CANDIDATES), K)
        ],
    }


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "questions.json"
    questions = [q for q in json.loads(path.read_text(encoding="utf-8")) if q["answerable"]]
    store = get_store(os.getenv("EVAL_USER_ID", "default"))
    ranks: dict[str, list] = {}
    for q in questions:
        for name, ids in retrievers(store, q["question"]).items():
            chunks = store.get(ids)
            ranks.setdefault(name, []).append(first_hit_rank(chunks, q["source"], q.get("page")))

    summaries = {name: retrieval_summary(r) for name, r in ranks.items()}
    cols = list(next(iter(summaries.values())).keys())
    lines = ["| retriever | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    for name, s in summaries.items():
        lines.append(f"| {name} | " + " | ".join(str(s[c]) for c in cols) + " |")
    table = "\n".join(lines)
    (HERE / "retrieval_results.md").write_text(table + "\n", encoding="utf-8")
    print(f"\nRetrieval on {len(questions)} answerable questions:\n")
    print(table)

    print("\nQuestions where 'dense (naive)' did NOT put the right page first:")
    for q, r in zip(questions, ranks["dense (naive)"], strict=True):
        if r != 1:
            others = {n: ranks[n][questions.index(q)] for n in ranks}
            print(f"  rank={r}  {q['question'][:70]}   (all: {others})")
    print("\nSaved: eval/retrieval_results.md")


if __name__ == "__main__":
    main()
