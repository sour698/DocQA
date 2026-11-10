"""Compare naive RAG vs hybrid+rerank on your own question set.

Usage (from project root):
    python -m eval.run_eval                              # uses eval/questions.json
    python -m eval.run_eval eval/questions.sample.json   # bundled sample set
    MODES=hybrid_rerank python -m eval.run_eval ...      # re-run one mode only
    JUDGE=1 python -m eval.run_eval ...                  # also grade answers with an LLM judge
    EVAL_USER_ID=<account id> python -m eval.run_eval ...  # evaluate one account's documents

Metrics (per mode, each with a 95 % Wilson confidence interval):
  hit@k              answerable Qs where a retrieved chunk is from the right source/page
  answer_accuracy    answerable Qs where ALL keywords appear in the answer (cheap proxy)
  judge_accuracy     answerable Qs the LLM judge marks correct (only with JUDGE=1)
  false_refusal      answerable Qs the system wrongly refused
  hallucination      unanswerable Qs the system answered instead of refusing
It also prints a paired sign test between the modes: with ~100 questions a few points of
difference are often just noise.
"""

import json
import os
import sys
import time
from pathlib import Path

from app import rag
from app.config import LLM_MODEL, REFUSAL_THRESHOLD, TOP_K
from app.llm import LLMError
from eval.judge import judge, reference_for
from eval.metrics import compare_modes, failures_md, is_correct, summarize, table

HERE = Path(__file__).parent
QUESTIONS = HERE / "questions.json"
DELAY = float(os.getenv("EVAL_DELAY", "3"))  # be gentle with free-tier rate limits
USER_ID = os.getenv("EVAL_USER_ID", "default")
MIN_UNANSWERABLE = 20


def _cache_file(mode: str) -> Path:
    safe = f"{mode}_{LLM_MODEL}_k{TOP_K}_t{REFUSAL_THRESHOLD}_{USER_ID}".replace("/", "-")
    d = HERE / ".cache"
    d.mkdir(exist_ok=True)
    return d / f"{safe}.json"


def run_mode(questions, mode, use_judge=False):
    """Runs every question. Each finished answer is saved at once, so a rate-limit crash
    loses nothing: just re-run the same command and it resumes where it stopped."""
    cache_path = _cache_file(mode)
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    rows = []
    for n, q in enumerate(questions, 1):
        sig = json.dumps(q, sort_keys=True)
        row = cache.get(sig)
        tag = "cached" if row else "new"
        print(f"  [{mode}] question {n}/{len(questions)} ({tag})", flush=True)
        try:
            if row is None:
                out = rag.answer(q["question"], mode, user_id=USER_ID)
                row = {
                    "question": q["question"],
                    "answerable": q["answerable"],
                    "refused": out["refused"],
                    "answer": out["answer"],
                    "top_score": out.get("top_score"),
                }
                if q["answerable"]:
                    row["hit"] = any(
                        h["source"] == q["source"]
                        and (q.get("page") is None or h["page"] == q["page"])
                        for h in out["retrieved"]
                    )
                    row["correct"] = is_correct(out["answer"], out["refused"], q["keywords"])
                time.sleep(DELAY)
            if use_judge and q["answerable"] and "judge_correct" not in row:
                verdict = (
                    False
                    if row["refused"]
                    else judge(q["question"], reference_for(q), row["answer"])
                )
                if verdict is not None:
                    row["judge_correct"] = verdict
                time.sleep(DELAY)
        except LLMError as e:
            raise SystemExit(
                f"\nStopped: {e}\nProgress is saved ({len(cache)} answers cached for {mode}). "
                "Re-run the same command later to resume."
            ) from e
        rows.append(row)
        cache[sig] = row
        cache_path.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    return rows


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else QUESTIONS
    if not path.exists():
        raise SystemExit(
            f"{path} not found. Create eval/questions.json (see questions.example.json)."
        )
    questions = json.loads(path.read_text(encoding="utf-8"))
    n_unans = sum(not q["answerable"] for q in questions)
    if n_unans < MIN_UNANSWERABLE:
        print(
            f"WARNING: only {n_unans} unanswerable questions (aim for {MIN_UNANSWERABLE}+). The "
            "hallucination rate is meaningless on so few - see eval/unanswerable.template.json.\n"
        )
    modes = [m.strip() for m in os.getenv("MODES", "naive,hybrid_rerank").split(",")]
    use_judge = os.getenv("JUDGE", "0") == "1"
    results_path = HERE / "results.json"
    report = {}
    if results_path.exists() and set(modes) < {"naive", "hybrid_rerank"}:
        report = json.loads(results_path.read_text(encoding="utf-8"))  # keep the mode not re-run
    for mode in modes:
        print(f"Running {mode} ...")
        rows = run_mode(questions, mode, use_judge)
        report[mode] = {"summary": summarize(rows), "rows": rows}
    report = {m: report[m] for m in ("naive", "hybrid_rerank") if m in report}
    results_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    (HERE / "results.md").write_text(table(report) + "\n", encoding="utf-8")
    (HERE / "failures.md").write_text(failures_md(report), encoding="utf-8")
    print("\n" + table(report))
    if len(report) == 2:
        for key in ("correct",) + (("judge_correct",) if use_judge else ()):
            c = compare_modes(report["naive"]["rows"], report["hybrid_rerank"]["rows"], key)
            print(
                f"\nPaired comparison on '{key}': only naive right = {c['only_a_right']}, "
                f"only hybrid_rerank right = {c['only_b_right']}, sign-test p = {c['p_value']}"
            )
    print("\nSaved: eval/results.json, eval/results.md (paste into README), eval/failures.md")


if __name__ == "__main__":
    main()
