"""Pure scoring helpers (no heavy imports) shared by run_eval and rescore."""

import math

from app.utils import normalize_text


def is_correct(answer: str, refused: bool, keywords: list[str]) -> bool:
    if refused:
        return False
    ans = normalize_text(answer)
    return all(normalize_text(k) in ans for k in keywords)


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95 % Wilson score interval for a proportion k/n, in percent (None when n == 0).
    With ~100 questions the interval is wide - report it instead of a bare percentage."""
    if n == 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return round(100 * max(0.0, centre - half), 1), round(100 * min(1.0, centre + half), 1)


def sign_test(wins_a: int, wins_b: int) -> float:
    """Exact two-sided sign test p-value for paired outcomes (discordant pairs only)."""
    n = wins_a + wins_b
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(wins_a, wins_b) + 1)) / 2**n
    return round(min(1.0, 2 * tail), 4)


def compare_modes(rows_a: list[dict], rows_b: list[dict], key: str = "correct") -> dict:
    """Paired comparison of two runs over the same questions: is B really better than A?"""
    by_q = {r["question"]: r for r in rows_a}
    a_only = b_only = 0
    for r in rows_b:
        a = by_q.get(r["question"])
        if a is None or not r["answerable"] or key not in r or key not in a:
            continue
        a_only += bool(a[key]) and not r[key]
        b_only += bool(r[key]) and not a[key]
    return {"only_a_right": a_only, "only_b_right": b_only, "p_value": sign_test(a_only, b_only)}


def summarize(rows: list[dict]) -> dict:
    ans = [r for r in rows if r["answerable"]]
    unans = [r for r in rows if not r["answerable"]]

    def pct(n, d):
        return round(100 * n / d, 1) if d else None

    def ci(n, d):
        c = wilson_ci(n, d)
        return f"{c[0]}-{c[1]}" if c else None

    hit, cor = sum(r["hit"] for r in ans), sum(r["correct"] for r in ans)
    ref, hal = sum(r["refused"] for r in ans), sum(not r["refused"] for r in unans)
    out = {
        "n_answerable": len(ans),
        "n_unanswerable": len(unans),
        "hit@k_%": pct(hit, len(ans)),
        "hit@k_95ci": ci(hit, len(ans)),
        "answer_accuracy_%": pct(cor, len(ans)),
        "answer_accuracy_95ci": ci(cor, len(ans)),
        "false_refusal_%": pct(ref, len(ans)),
        "false_refusal_95ci": ci(ref, len(ans)),
        "hallucination_%": pct(hal, len(unans)),
        "hallucination_95ci": ci(hal, len(unans)),
    }
    judged = [r for r in ans if "judge_correct" in r]
    if judged:
        j = sum(r["judge_correct"] for r in judged)
        out["judge_accuracy_%"] = pct(j, len(judged))
        out["judge_accuracy_95ci"] = ci(j, len(judged))
    return out


def table(report: dict) -> str:
    modes = list(report)
    keys = list(report[modes[0]]["summary"].keys())
    lines = ["| metric | " + " | ".join(modes) + " |", "|---|" + "---|" * len(modes)]
    for k in keys:
        lines.append(f"| {k} | " + " | ".join(str(report[m]["summary"][k]) for m in modes) + " |")
    return "\n".join(lines)


def pick_threshold(pos_scores: list[float], neg_scores: list[float]) -> dict:
    """Choose the refusal threshold with the lowest combined error.

    pos_scores: top reranker score for ANSWERABLE questions (should be accepted)
    neg_scores: top reranker score for UNANSWERABLE questions (should be refused)
    error(t) = share of answerable refused (score < t) + share of unanswerable accepted (score >= t)
    Ties go to the lowest threshold (prefers answering).
    """
    allv = sorted(set(pos_scores) | set(neg_scores))
    if not allv:
        raise ValueError("no scores")
    cands = (
        [allv[0] - 1.0]
        + [(a + b) / 2 for a, b in zip(allv, allv[1:], strict=False)]
        + [allv[-1] + 1.0]
    )

    def rates(t):
        fr = sum(s < t for s in pos_scores) / len(pos_scores) if pos_scores else 0.0
        leak = sum(s >= t for s in neg_scores) / len(neg_scores) if neg_scores else 0.0
        return fr, leak

    best = min(cands, key=lambda t: (sum(rates(t)), t))
    fr, leak = rates(best)
    return {"threshold": round(best, 3), "false_refusal": fr, "leak": leak}


def rates_at(t: float, pos_scores: list[float], neg_scores: list[float]) -> tuple[float, float]:
    fr = sum(s < t for s in pos_scores) / len(pos_scores) if pos_scores else 0.0
    leak = sum(s >= t for s in neg_scores) / len(neg_scores) if neg_scores else 0.0
    return fr, leak


def safe_threshold(pos_scores: list[float], margin: float = 1.0) -> float:
    """Never refuse a question we know is answerable: sit just below the weakest answerable
    score (minus a safety margin). Catches the clearly off-topic questions; the LLM prompt must
    handle the rest."""
    return round(min(pos_scores) - margin, 2)


def first_hit_rank(ranked: list[dict], source: str, page: int | None) -> int | None:
    """1-based rank of the first retrieved chunk from the right source/page (None = missed)."""
    for i, c in enumerate(ranked, 1):
        if c["source"] == source and (page is None or c["page"] == page):
            return i
    return None


def retrieval_summary(ranks: list[int | None], ks=(1, 3, 5)) -> dict:
    n = len(ranks)

    def pct(x):
        return round(100 * x / n, 1) if n else None

    out = {f"hit@{k}_%": pct(sum(r is not None and r <= k for r in ranks)) for k in ks}
    out["MRR"] = round(sum(1 / r for r in ranks if r) / n, 3) if n else None
    return out


def failures_md(report: dict) -> str:
    """Per-mode list of wrong answers: raw material for your 'failure analysis'."""
    out = []
    for mode, data in report.items():
        out.append(f"## {mode}\n")
        for r in data["rows"]:
            if r["answerable"] and not r["correct"]:
                reason = (
                    "refused"
                    if r["refused"]
                    else ("retrieval miss" if not r["hit"] else "wrong/other wording")
                )
                out.append(
                    f"- **Q:** {r['question']}\n  - reason: {reason}\n  - answer: {r['answer'][:200]!r}"
                )
            elif not r["answerable"] and not r["refused"]:
                out.append(
                    f"- **HALLUCINATION on unanswerable Q:** {r['question']}\n  - answer: {r['answer'][:200]!r}"
                )
        out.append("")
    return "\n".join(out)
