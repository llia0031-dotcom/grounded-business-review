# ---------------------------------------------------------------------------------------------
# Code node 2 of the workflow: the number check and the final report.
# Everything above this line is evidence-check's script (version 0.4.0, MIT), unchanged except that
# its command-line entry point is left out. Input variables: `report` (the model's JSON answer),
# `evidence` (JSON text from code node 1). Output variables: `released` (1 or 0), `feedback` (what
# to tell the model when it is 0), `markdown` (the report as text), `checked`, `failed`.
# ---------------------------------------------------------------------------------------------
KIND_TITLE = [("summary", "一、摘要"), ("finding", "二、发现"), ("cause", "三、原因"), ("advice", "四、建议"), ("note", "五、说明")]
LEVER = {"traffic": "流量", "conversion": "转化", "supply": "供给", "redemption": "核销", "refund": "退款", "holiday": "节假日"}
DIM = {"city": "城市", "category": "品类", "channel": "渠道", "merchant_type": "商家类型", "merchant": "商家"}
FAIL = {
    "unknown_source": "引用的查询编号不存在",
    "quote_not_found": "引用的那一行在查询结果里找不到（要整行原样复制，不要改动、不要把几行拼成一行）",
    "quote_too_short": "引用太短，要引用查询结果里的整行",
    "no_evidence": "这段话里有数字，但没有引用任何查询结果",
    "numbers_not_in_quote": "这些数字不在这段话引用的行里",
    "finding_without_number": "“发现”段必须写出具体数字，并引用数字所在的那一行",
    "cause_without_evidence": "“原因”段必须引用至少一行查询结果",
}


_CN_NUMBER = re.compile(r"[前上这同第每近头]?[一二两三四五六七八九十百]{1,3}[一-鿿]?")


def chinese_number_phrases(text: str) -> list:
    """Phrases such as 两者, 一周内, 发现二 that the check reads as numbers; used to make feedback specific."""
    out = []
    for m in _CN_NUMBER.finditer(text):
        s = m.group(0)
        if s not in out and numbers_in(s):
            out.append(s)
    return out


def main(report: str, evidence: str) -> dict:
    try:
        text = report.strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.index("{"):]
        data = json.loads(text[text.index("{"): text.rindex("}") + 1])
    except Exception:
        return {"released": 0, "feedback": "- 输出不是合法的 JSON，请只输出规定格式的 JSON。", "markdown": report, "checked": 0, "failed": 1}
    sources = {qid: Source(qid, None, body) for qid, body in json.loads(evidence).items()}
    blocks = [b for b in data.get("blocks", []) if isinstance(b, dict)]
    results, problems = [], []
    for i, b in enumerate(blocks):
        body = str(b.get("text", "")).replace("个百分点", " pp").replace("百分点", " pp")
        evid = [e for e in (b.get("evidence") or []) if isinstance(e, dict)]
        numbers = numbers_in(body)
        if b.get("kind") == "finding" and not numbers:
            results.append((i, "finding_without_number", [], []))
        elif b.get("kind") == "cause" and not evid:
            results.append((i, "cause_without_evidence", [], []))
        elif not evid and not numbers:
            results.append((i, "no_number", [], []))
        else:
            r = check_claim({"id": str(i), "text": body, "evidence": evid}, sources)
            results.append((i, r["status"], r["missing_numbers"], [e["quote"] for e in r["evidence"] if not e["found"]]))
    if not blocks:
        problems.append("报告是空的")
    elif data.get("has_anomaly") and not any(b.get("kind") == "finding" for b in blocks):
        problems.append("报告说本周有异动，但没有“发现”段（kind=finding）")
    bad = [r for r in results if r[1] not in ("supported", "no_number")]
    lines = ["- 整份报告：" + p for p in problems]
    for i, status, missing, lost in bad:
        msg = FAIL.get(status, status)
        if missing:
            msg += "：" + "、".join(missing)
        if status in ("numbers_not_in_quote", "no_evidence"):
            words = chinese_number_phrases(str(blocks[i].get("text", "")))
            if words:
                msg += "。这段话里的中文数字也算数字：" + "、".join("“%s”" % w for w in words[:6]) + "，请换成不带数字的说法"
        if lost:
            msg += "；找不到的引用：" + "；".join(q[:60] for q in lost)
        lines.append("- 第 %d 段（%s…）：%s" % (i + 1, str(blocks[i].get("text", ""))[:30], msg))
    released = not bad and not problems
    status = dict((r[0], r[1]) for r in results)
    md = ["# 经营分析周报", "",
          "> 数字核对：%s。%d 段含数字或引用，%d 段没通过。" % ("已放行" if released else "未放行", sum(r[1] != "no_number" for r in results), len(bad)), ""]
    causes = [c for c in data.get("causes", []) if isinstance(c, dict)]
    if causes:
        md += ["**原因清单**", ""]
        for c in causes:
            seg = "、".join("%s=%s" % (DIM.get(s.get("dimension"), s.get("dimension")), s.get("value")) for s in (c.get("segment") or []) if isinstance(s, dict))
            md.append("- %s：%s" % (seg or "全部", LEVER.get(c.get("lever"), "待查")))
        md.append("")
    for kind, title in KIND_TITLE:
        items = [(i, b) for i, b in enumerate(blocks) if b.get("kind", "finding") == kind]
        if not items:
            continue
        md += ["## " + title, ""]
        for i, b in items:
            refs = sorted(set(str(e.get("source")) for e in (b.get("evidence") or []) if isinstance(e, dict) and e.get("source")))
            mark = "" if status.get(i) in ("supported", "no_number") else " **［未通过核对］**"
            md.append("- " + str(b.get("text", "")) + ("（出处：" + "、".join(refs) + "）" if refs else "") + mark)
        md.append("")
    md.append("数据是合成的，不是任何平台的真实经营数据。")
    return {"released": 1 if released else 0, "feedback": "\n".join(lines), "markdown": "\n".join(md),
            "checked": sum(r[1] != "no_number" for r in results), "failed": len(bad)}
