"""Evidence files and the number check.

Every query a run makes is saved as a small Markdown file (q01.md, q02.md, ...) holding the result
table. A report is a list of blocks; a block that states a number must quote, word for word, the
row(s) of an evidence file the number comes from. The check itself is evidence-check's script,
unchanged (vendor/evidence-check): the quote must be in the cited file and every number in the block
must be in its quotes. A report with a block that fails is not released.

Two things are done before a block is handed to the script, both because of how the script reads
Chinese text (see docs in the README): "百分点" is rewritten as "pp" (the script reads the 百 as 100),
and a block with no evidence passes only if it states no number at all.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pandas as pd

from . import config as C
from . import db

_spec = importlib.util.spec_from_file_location("check_evidence", C.CHECK_SCRIPT)
ce = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ce)

BLOCK_KINDS = ["summary", "finding", "cause", "advice", "note"]
KIND_LABEL = {"summary": "摘要", "finding": "发现", "cause": "原因", "advice": "建议", "note": "说明"}


class Evidence:
    """The evidence files of one run."""

    def __init__(self, folder: Path, keep: list[dict] | None = None):
        """A fresh folder, or (keep = the items of an interrupted run) the folder as that run left it."""
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.items: list[dict] = list(keep or [])
        kept = {f"{i['id']}.md" for i in self.items}
        for old in self.dir.glob("q*.md"):
            if old.name not in kept:
                old.unlink()

    def add(self, title: str, df: pd.DataFrame | None, notes: list[str] | None = None, sql: str | None = None,
            max_rows: int = db.MAX_ROWS) -> str:
        qid = f"q{len(self.items) + 1:02d}"
        shown = [f"# {qid} {title}", *(notes or [])]
        if df is not None:
            shown += ["", db.to_markdown(df, max_rows)]
        stored = shown + (["", "SQL：", "```sql", sql.strip(), "```"] if sql else [])
        nl = chr(10)
        (self.dir / f"{qid}.md").write_text(nl.join(stored) + nl, encoding="utf-8", newline=nl)
        self.items.append({"id": qid, "title": title, "rows": 0 if df is None else int(len(df))})
        return nl.join(shown)  # what a model sees: the file without the SQL

    @property
    def last_id(self) -> str:
        return self.items[-1]["id"]

    def text(self, qid: str) -> str:
        return (self.dir / f"{qid}.md").read_text(encoding="utf-8")


def row(df: pd.DataFrame, i: int = 0) -> str:
    """Row i of a table exactly as it is written in an evidence file."""
    return db.to_markdown(df.iloc[[i]]).splitlines()[2]


def _normalise(text: str) -> str:
    return text.replace("个百分点", " pp").replace("百分点", " pp")


def has_number(text: str) -> bool:
    return bool(ce.numbers_in(_normalise(text)))


def check_blocks(blocks: list[dict], folder: Path, has_anomaly: bool | None = None) -> dict:
    """Check every block against the evidence files in `folder`.

    Per block, evidence-check decides: the quotes are in the cited files and every number in the
    text is in the quotes. On top of that, three rules of this project keep a report from passing
    by saying nothing that can be checked:
      * a `finding` must state at least one number (and so must cite the row it comes from);
      * a `cause` must cite at least one row;
      * a report that says there is an anomaly must have at least one `finding`.
    Returns {"released", "blocks": [{"index", "status", ...}], "report_problems", counts}.
    status: supported | no_number (nothing to check) | a failure status.
    """
    sources = ce.load_sources([str(folder)])
    results = []
    for i, b in enumerate(blocks):
        text = _normalise(str(b.get("text", "")))
        evid = [e for e in (b.get("evidence") or []) if isinstance(e, dict)]
        numbers = ce.numbers_in(text)
        if b.get("kind") == "finding" and not numbers:
            results.append({"index": i, "status": "finding_without_number", "missing_numbers": [], "problems": ["finding_without_number"]})
            continue
        if b.get("kind") == "cause" and not evid:
            results.append({"index": i, "status": "cause_without_evidence", "missing_numbers": [], "problems": ["cause_without_evidence"]})
            continue
        if not evid and not numbers:
            results.append({"index": i, "status": "no_number", "missing_numbers": [], "problems": []})
            continue
        r = ce.check_claim({"id": str(i), "text": text, "evidence": evid}, sources)
        results.append({"index": i, "status": r["status"], "missing_numbers": r["missing_numbers"], "problems": r["problems"],
                        "rounded": r["rounded"],
                        "quotes_not_found": [e["quote"] for e in r["evidence"] if not e["found"]]})
    report_problems = []
    if not blocks:
        report_problems.append("empty_report")
    elif has_anomaly and not any(b.get("kind") == "finding" for b in blocks):
        report_problems.append("no_finding")
    bad = [r for r in results if r["status"] not in ("supported", "no_number")]
    return {"released": not bad and not report_problems, "blocks": results, "report_problems": report_problems,
            "checked": sum(r["status"] != "no_number" for r in results),
            "supported": sum(r["status"] == "supported" for r in results), "failed": len(bad)}


FAIL_TEXT = {
    "unknown_source": "引用的查询编号不存在",
    "quote_not_found": "引用的那一行在查询结果里找不到（要整行原样复制，不要改动、不要把几行拼成一行）",
    "quote_too_short": "引用太短，要引用查询结果里的整行",
    "no_evidence": "这段话里有数字，但没有引用任何查询结果",
    "numbers_not_in_quote": "这些数字不在这段话引用的行里",
    "finding_without_number": "“发现”段必须写出具体数字，并引用数字所在的那一行",
    "cause_without_evidence": "“原因”段必须引用至少一行查询结果",
}
REPORT_FAIL_TEXT = {"no_finding": "报告说本周有异动，但没有“发现”段（kind=finding）", "empty_report": "报告是空的"}


_CN_NUMBER = re.compile(r"[前上这同第每近头]?[一二两三四五六七八九十百]{1,3}[一-鿿]?")


def chinese_number_phrases(text: str) -> list[str]:
    """Phrases such as 两者, 一周内, 前三 that the check reads as numbers; used to make feedback specific."""
    out = []
    for m in _CN_NUMBER.finditer(_normalise(text)):
        s = m.group(0)
        if s not in out and ce.numbers_in(s):
            out.append(s)
    return out


def feedback(check: dict, blocks: list[dict]) -> str:
    """What to tell the writer about the blocks that failed."""
    lines = [f"- 整份报告：{REPORT_FAIL_TEXT.get(p, p)}" for p in check.get("report_problems", [])]
    for r in check["blocks"]:
        if r["status"] in ("supported", "no_number"):
            continue
        text = str(blocks[r["index"]].get("text", ""))
        msg = FAIL_TEXT.get(r["status"], r["status"])
        if r["missing_numbers"]:
            msg += "：" + "、".join(r["missing_numbers"])
        if r["status"] in ("numbers_not_in_quote", "no_evidence"):
            words = chinese_number_phrases(text)
            if words:
                msg += "。这段话里的中文数字也算数字：" + "、".join(f"“{w}”" for w in words[:6]) + "，请换成不带数字的说法"
        if r.get("quotes_not_found"):
            msg += "；找不到的引用：" + "；".join(q[:60] for q in r["quotes_not_found"])
        lines.append(f"- 第 {r['index'] + 1} 段（{text[:30]}…）：{msg}")
    return "\n".join(lines)


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")


_SEG_ORDER = list(C.DIMENSIONS)


def segment_text(segment: dict) -> str:
    if not segment:
        return "全部"
    return "、".join(f"{C.DIMENSIONS[d]}={segment[d]}" for d in _SEG_ORDER if d in segment)


def clean_segment(segment) -> dict:
    """Keep known dimensions with string values; anything else is dropped."""
    if not isinstance(segment, dict):
        return {}
    return {d: str(v).strip() for d, v in segment.items() if d in C.DIMENSIONS and v not in (None, "")}


WEEK_RE = re.compile(r"^2026-W\d\d$")
