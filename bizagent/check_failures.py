"""Sort every paragraph that failed the number check by why it failed, for the first draft and the final review.

  python -m bizagent.check_failures test-oneshot test-agent [--out eval/results/check-failures-test.json]

Reads eval/results/<run>/<case>/result.json (the checks) and state.json (the first draft's text).
Categories:
  number words only        every number the check could not find comes from a Chinese word with a numeral
                           (两个, 前一天, 归零 ...); 零 is added to the evidence module's list here
  a number not in the row  at least one other number is missing from the cited rows
  no row cited             a paragraph with numbers or a cause that cites nothing (number words only or other)
  finding without number   a finding paragraph with no number
  quote not found          a cited row is not in the query result
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

from . import config as C
from .evidence import ce, chinese_number_phrases

RESULTS = C.ROOT / "eval" / "results"
_ZERO = re.compile(r"[一-鿿]?零[一-鿿]?")


def _num(x):
    try:
        return float(str(x).replace(",", ""))
    except ValueError:
        return x


def _word_numbers(text: str) -> tuple[list[str], set]:
    words = chinese_number_phrases(text) + [w for w in _ZERO.findall(text)]
    nums = {_num(n) for w in words for n in ce.numbers_in(w)}
    if any("零" in w for w in words):
        nums.add(0.0)
    return words, nums


def classify(status: str, text: str, missing: list) -> str:
    if status in ("numbers_not_in_quote", "no_evidence"):
        words, word_nums = _word_numbers(text)
        if status == "no_evidence":
            nums = {_num(n) for n in ce.numbers_in(text.replace("百分点", " pp"))}
            return "no row cited: number words only" if nums and nums <= word_nums else "no row cited: other"
        miss = {_num(n) for n in missing}
        return "number words only" if miss and miss <= word_nums else "a number not in the cited row"
    return {"finding_without_number": "finding without a number", "quote_not_found": "quote not found",
            "cause_without_evidence": "cause without a cited row"}.get(status, status)


def run(name: str) -> dict:
    out = {"run": name, "drafts": {}}
    for label in ("first", "final"):
        counts, rows, reviews, held = Counter(), [], 0, 0
        for f in sorted((RESULTS / name).glob("*/result.json")):
            d = json.loads(f.read_text(encoding="utf-8"))
            state = json.loads((f.parent / "state.json").read_text(encoding="utf-8")) if (f.parent / "state.json").exists() else {}
            check = d.get("first_check") if label == "first" else d.get("check")
            answer = state.get("first_answer") if label == "first" else d["result"]
            if not check or not answer:
                continue
            reviews += 1
            held += not check["released"]
            blocks = answer.get("blocks") or []
            for b in check["blocks"]:
                if b["status"] in ("supported", "no_number"):
                    continue
                text = str(blocks[b["index"]].get("text", "")) if b["index"] < len(blocks) else ""
                cat = classify(b["status"], text, b.get("missing_numbers") or [])
                counts[cat] += 1
                rows.append({"case": f.parent.name, "paragraph": b["index"] + 1, "status": b["status"], "category": cat,
                             "missing_numbers": b.get("missing_numbers") or [], "number_words": _word_numbers(text)[0][:6]})
        out["drafts"][label] = {"reviews": reviews, "held_back": held,
                                "paragraphs_failed": sum(counts.values()), "by_category": dict(counts), "paragraphs": rows}
    return out


def main(argv: list[str]) -> int:
    path = RESULTS / "check-failures-test.json"
    if "--out" in argv:
        i = argv.index("--out")
        path = Path(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    data = {"note": "Paragraphs that failed the number check, sorted by why. Built by python -m bizagent.check_failures.",
            "runs": [run(n) for n in argv]}
    for r in data["runs"]:
        for label, d in r["drafts"].items():
            print(r["run"], label, "reviews", d["reviews"], "held back", d["held_back"], "failed paragraphs", d["paragraphs_failed"], d["by_category"])
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print("written:", path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
