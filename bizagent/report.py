"""Turn a result (blocks + evidence) into a Markdown report."""
from __future__ import annotations

from pathlib import Path

from . import config as C
from .evidence import KIND_LABEL, segment_text

SECTIONS = [("summary", "一、摘要"), ("finding", "二、发现"), ("cause", "三、原因"), ("advice", "四、建议"), ("note", "五、说明")]


def render(result: dict, check: dict | None = None, folder: Path | None = None, title: str | None = None) -> str:
    """Markdown: sections by block kind, each sentence followed by the evidence files it cites."""
    out = [f"# {title or '经营分析周报'}（{result['week']}）", ""]
    if check is not None:
        state = "已放行" if check["released"] else "未放行"
        out += [f"> 数字核对：{state}。{check['checked']} 段含数字或引用，{check['supported']} 段核对通过，{check['failed']} 段没通过。", ""]
    if result.get("causes"):
        out += ["**原因清单**", ""]
        for c in result["causes"]:
            lever = C.LEVERS.get(c.get("lever"), "待查")
            out.append(f"- {segment_text(c.get('segment') or {})}：{lever}")
        out.append("")
    status = {r["index"]: r["status"] for r in (check or {}).get("blocks", [])}
    for kind, heading in SECTIONS:
        items = [(i, b) for i, b in enumerate(result["blocks"]) if b.get("kind", "finding") == kind]
        if not items:
            continue
        out += [f"## {heading}", ""]
        for i, b in items:
            refs = sorted({e["source"] for e in (b.get("evidence") or []) if isinstance(e, dict) and e.get("source")})
            mark = "" if status.get(i, "supported") in ("supported", "no_number") else " **［未通过核对］**"
            out.append(f"- {b['text']}" + (f"（出处：{'、'.join(refs)}）" if refs else "") + mark)
        out.append("")
    if folder is not None:
        out += ["## 附：查询结果", ""]
        for f in sorted(Path(folder).glob("q*.md")):
            text = f.read_text(encoding="utf-8").rstrip()
            out += ["<details><summary>" + text.splitlines()[0].lstrip("# ") + "</summary>", "", *text.splitlines()[1:], "", "</details>", ""]
    return "\n".join(out).rstrip() + "\n"
