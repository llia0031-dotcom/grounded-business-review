"""Code node 1 of the workflow: monitor the week and build the material the model writes from.

Paste this file into a Code node (Python). Input variable: `data` (the JSON text fetched by the
HTTP node; it names its own analysis week). Output variables: `week`, `pack` (the query results as
text, numbered q01, q02, ...), `evidence` (JSON text: {"q01": "...", ...}) and `alerts` (how many alerts).

Only the Python standard library is used. The rules are the same as the local version:
the monitor is sql/monitor/weekly_alerts.sql, the split is bizagent/decompose.py.
"""
import json
from statistics import median

DIM_LABEL = {"city": "城市", "category": "品类", "channel": "渠道", "merchant_type": "商家类型"}
Z_TOTAL, Z_SLICE, SLICE_X, SLICE_SHARE, BASE_N = 3.5, 6.0, 3.0, 0.05, 8
PACK_METRICS, PACK_FUNNELS = 3, 4
FUNNEL = ["visits", "conversion_rate", "orders", "gmv", "aov", "subsidy_rate", "online_stores", "active_stores",
          "redeem_gmv", "redeem_rate", "refund_amt", "refund_rate"]


def fmt(v):
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    s = "%.2f" % v
    return s.rstrip("0").rstrip(".") if "." in s else s


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(fmt(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def main(data: str, week: str = "") -> dict:
    d = json.loads(data)
    week = week or d["week"]
    weeks, hol = d["weeks"], set(d["holiday_weeks"])
    metrics = {m["name"]: m for m in d["metrics"]}
    order = [m["name"] for m in d["metrics"]]
    if week not in weeks:
        return {"week": week, "pack": "数据里没有这一周：" + week, "evidence": "{}", "alerts": 0}
    base = [w for w in reversed(weeks[: weeks.index(week)]) if w not in hol][:BASE_N]
    prev = weeks[weeks.index(week) - 1] if weeks.index(week) > 0 else None
    if len(base) < 6:
        return {"week": week, "pack": "这一周之前可用的基线周不足，无法判定。", "evidence": "{}", "alerts": 0}
    cell = {}  # (metric, dim, slice) -> {week: (num, den)}
    for name, dim, s, w, num, den in d["rows"]:
        cell.setdefault((name, dim, s), {})[w] = (num, den)

    def value(m, nd):
        if nd is None:
            return None
        num, den = nd
        if m["kind"] != "ratio":
            return num
        return None if not den else (100.0 if m["unit"] == "%" else 1.0) * num / den

    def weight(m, nd):
        return 0.0 if nd is None else (nd[1] if m["kind"] == "ratio" else nd[0]) or 0.0

    scored = []
    for (name, dim, s), by_week in cell.items():
        m = metrics[name]
        cur, last = value(m, by_week.get(week)), value(m, by_week.get(prev))
        vals = [value(m, by_week.get(w)) for w in base]
        vals = [v for v in vals if v is not None]
        if cur is None or len(vals) < 6:
            continue
        med = median(vals)
        mad = median([abs(v - med) for v in vals])
        rel = m["unit"] != "%"
        min_change = m["min_change"] * (1 if dim == "total" else SLICE_X)
        mad = max(mad, abs(med) * m["min_change"] / 1000.0 if rel else m["min_change"] / 10.0)
        change = (100.0 * (cur - med) / abs(med) if med else 0.0) if rel else cur - med
        wow = None if last is None else ((100.0 * (cur - last) / abs(last) if last else 0.0) if rel else cur - last)
        z = 0.6745 * (cur - med) / mad if mad else 0.0
        share = 1.0
        if dim != "total":
            tot = cell[(name, "total", "全部")]
            shares = [weight(m, by_week.get(w)) / weight(m, tot.get(w)) for w in base if weight(m, tot.get(w))]
            share = sum(shares) / len(shares) if shares else 0.0
        watched = m["monitor"] if dim == "total" else m["monitor_slices"]
        alert = bool(watched and (dim == "total" or share >= SLICE_SHARE)
                     and abs(z) >= (Z_TOTAL if dim == "total" else Z_SLICE) and abs(change) >= min_change)
        scored.append({"metric": name, "dim": dim, "slice": s, "cur": cur, "last": last, "median": med, "change": change,
                       "wow": wow, "z": z, "alert": alert, "unit": "%" if rel else "pp"})

    evidence, parts = {}, []

    def add(title, notes, head=None, rows=None):
        qid = "q%02d" % (len(evidence) + 1)
        text = "\n".join(["# " + qid + " " + title] + notes + ([""] + [table(head, rows)] if head else []))
        evidence[qid] = text
        parts.append(text)

    period = "分析周：%s。对比：基线，即本周之前最近 %d 个非节假日周（%s 至 %s）。" % (week, len(base), base[-1], base[0])
    if week in hol:
        period = period.replace("。对比", "，这一周含法定节假日。对比", 1)
    sign = lambda v, u: "" if v is None else ("%+.2f%s" % (v, u))
    head = ["指标", "单位", "本周", "上周", "基线中位数", "较上周", "较基线", "修正Z", "是否异动"]
    tot = sorted([r for r in scored if r["dim"] == "total"], key=lambda r: order.index(r["metric"]))
    add("本周指标总览（全部指标，总量）",
        [period, "较上周、较基线：数量和金额类指标是相对变化（%），百分比类指标是绝对变化（pp，即个百分点）。",
         "是否异动的规则：修正 Z 分数的绝对值不低于 3.5，并且较基线的变化不低于该指标的最小幅度。"],
        head, [[metrics[r["metric"]]["label"], metrics[r["metric"]]["unit"], r["cur"], r["last"], r["median"],
                sign(r["wow"], r["unit"]), sign(r["change"], r["unit"]), r["z"], "是" if r["alert"] else "否"] for r in tot])
    sl = sorted([r for r in scored if r["dim"] != "total" and r["alert"]], key=lambda r: -abs(r["z"]))
    note = [period, "分组监控只查九个核心指标、基线期平均占比不低于 5% 的分组；规则：修正 Z 分数的绝对值不低于 6，并且较基线的变化不低于该指标最小幅度的 3 倍。"]
    if sl:
        add("本周触发异动的分组", note, head[:2] + ["维度", "分组"] + head[2:],
            [[metrics[r["metric"]]["label"], metrics[r["metric"]]["unit"], DIM_LABEL[r["dim"]], r["slice"], r["cur"], r["last"], r["median"],
              sign(r["wow"], r["unit"]), sign(r["change"], r["unit"]), r["z"], "是" if r["alert"] else "否"] for r in sl])
    else:
        add("本周触发异动的分组", note + ["本周没有分组触发异动。"])

    def two(name, dim, s):
        by_week = cell.get((name, dim, s), {})
        n0 = sum((by_week.get(w) or (0, 0))[0] or 0.0 for w in base) / len(base)
        d0 = sum((by_week.get(w) or (0, 0))[1] or 0.0 for w in base) / len(base)
        n1, d1 = by_week.get(week) or (0.0, 0.0)
        return n0, d0, n1 or 0.0, d1 or 0.0

    def breakdown(name, dim):
        m = metrics[name]
        slices = sorted({k[2] for k in cell if k[0] == name and k[1] == dim})
        if not slices:
            return
        k = 100.0 if m["unit"] == "%" else 1.0
        N0, D0, N1, D1 = two(name, "total", "全部")
        rows = []
        if m["kind"] == "ratio":
            R0, R1 = k * N0 / D0, k * N1 / D1
            for s in slices:
                n0, d0, n1, d1 = two(name, dim, s)
                r0 = k * n0 / d0 if d0 else None
                r1 = k * n1 / d1 if d1 else None
                a, b = (r1 if r0 is None else r0), (r0 if r1 is None else r1)
                if a is None:
                    continue
                w0, w1 = d0 / D0, d1 / D1
                rate, mix = (w0 + w1) / 2 * (b - a), (w1 - w0) * ((a + b) / 2 - (R0 + R1) / 2)
                rows.append([s, r0, r1, b - a, rate, mix, rate + mix, 100 * (rate + mix) / (R1 - R0) if R1 != R0 else None, 100 * w0, 100 * w1])
            rows.sort(key=lambda r: -abs(r[6]))
            head = [DIM_LABEL[dim], "基线" + m["label"], "本周" + m["label"], "变化", "率效应", "结构效应", "合计贡献", "贡献度_pct", "基线占比_pct", "本周占比_pct"]
            line = "%s：基线 %.2f，本周 %.2f，变化 %.2f" % (m["label"], R0, R1, R1 - R0)
            extra = ["率效应：这个分组自己的比率变了带来的影响。结构效应：这个分组在分母里的占比变了带来的影响。两者相加是合计贡献。"]
        else:
            for s in slices:
                n0, _, n1, _ = two(name, dim, s)
                rows.append([s, n0, n1, n1 - n0, 100 * (n1 - n0) / n0 if n0 else None,
                             100 * (n1 - n0) / (N1 - N0) if N1 != N0 else None, 100 * n0 / N0 if N0 else None, 100 * n1 / N1 if N1 else None])
            rows.sort(key=lambda r: -abs(r[3]))
            head = [DIM_LABEL[dim], "基线", "本周", "变化", "变化率_pct", "贡献度_pct", "基线占比_pct", "本周占比_pct"]
            line = "%s：基线（%d 个基线周的平均） %.2f，本周 %.2f，变化 %.2f" % (m["label"], len(base), N0, N1, N1 - N0)
            extra = []
        add("拆解：%s 按%s" % (m["label"], DIM_LABEL[dim]), [period, line] + extra, head, rows[:12])

    alerts = sorted([r for r in scored if r["alert"]], key=lambda r: -abs(r["z"]))
    picked = []
    for r in alerts:
        if r["metric"] not in picked and metrics[r["metric"]]["kind"] != "distinct":
            picked.append(r["metric"])
    for name in picked[:PACK_METRICS]:
        for dim in DIM_LABEL:
            breakdown(name, dim)
    seen = []
    for r in sl:
        if (r["dim"], r["slice"]) not in seen:
            seen.append((r["dim"], r["slice"]))
    for dim, s in seen[:PACK_FUNNELS]:
        rows = []
        for name in FUNNEL:
            if (name, dim, s) not in cell:
                continue
            m = metrics[name]
            n0, d0, n1, d1 = two(name, dim, s)
            k = 100.0 if m["unit"] == "%" else 1.0
            v0 = (k * n0 / d0 if d0 else None) if m["kind"] == "ratio" else n0
            v1 = (k * n1 / d1 if d1 else None) if m["kind"] == "ratio" else n1
            if v0 is None or v1 is None:
                continue
            rows.append([m["label"], m["unit"], v0, v1, v1 - v0, 100 * (v1 - v0) / abs(v0) if v0 else None])
        add("分组体检（范围：%s=%s）" % (DIM_LABEL[dim], s), [period, "变化：本周减基线，单位同该指标（百分比类指标是 pp）。相对变化_pct：变化 ÷ 基线的值。"],
            ["指标", "单位", "基线", "本周", "变化", "相对变化_pct"], rows)
    return {"week": week, "pack": "\n\n".join(parts), "evidence": json.dumps(evidence, ensure_ascii=False), "alerts": len(alerts)}
