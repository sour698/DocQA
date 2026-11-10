"""Re-score a finished run WITHOUT calling the LLM again.

    python -m eval.rescore eval/questions.sample.json
Reads eval/results.json, re-applies the (normalised) keyword check, rewrites
results.json / results.md / failures.md and prints the new table.
"""

import json
import sys
from pathlib import Path

from eval.metrics import failures_md, is_correct, summarize, table

HERE = Path(__file__).parent


def main():
    qpath = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "questions.json"
    keywords = {
        q["question"]: q.get("keywords", []) for q in json.loads(qpath.read_text(encoding="utf-8"))
    }
    report = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
    for data in report.values():
        for r in data["rows"]:
            if r["answerable"]:
                r["correct"] = is_correct(
                    r["answer"], r["refused"], keywords.get(r["question"], [])
                )
        data["summary"] = summarize(data["rows"])
    (HERE / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (HERE / "results.md").write_text(table(report) + "\n", encoding="utf-8")
    (HERE / "failures.md").write_text(failures_md(report), encoding="utf-8")
    print(table(report))
    print("\nWrong answers listed in eval/failures.md")


if __name__ == "__main__":
    main()
