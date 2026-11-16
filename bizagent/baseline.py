"""The no-model baseline: monitor -> drill down -> name the lever -> fill a report template.

Every step is a fixed rule over the same tools the agent has.
  1. Scopes: the slices that raised an alert, strongest first (the whole business when only totals did).
  2. Lever: inside a scope, walk the funnel from the top and stop at the first stage that moved past
     its threshold: stores offline -> visits -> conversion -> refunds -> redemptions.
  3. Drill: run Adtributor on that stage's metric over the other dimensions; if it returns one single
     slice, narrow the scope to it (one level only, as in a one-step recursive Adtributor).
  4. Explain away: an alert that is no longer past its threshold once the segments already accepted
     are taken out of it is a consequence of them, not a new cause.
  5. A week that contains a public holiday is reported as a holiday week and nothing else.
"""
from __future__ import annotations

import pandas as pd

from . import config as C
from . import decompose as D, metrics as M, monitor
from .evidence import Evidence, row, segment_text
from .tools import Toolbox

STORE_DIMS = {"city", "category", "merchant_type", "merchant"}
CELL_DIMS = {"city", "category", "channel"}
# relative change (%) a funnel stage must show in the segment to be named as the lever
LEVER_MIN = {"supply": 6.0, "traffic": 15.0, "conversion": 15.0, "refund": 36.0, "redemption": 18.0}
CANON = {"supply": "online_stores", "traffic": "visits", "conversion": "orders", "refund": "refund_amt", "redemption": "redeem_gmv"}
ADVICE = {
    "traffic": "先核对该分组的流量来源：内容和直播场次有没有减少、投放和推荐位有没有调整；确认后和对应的运营团队对齐恢复计划。",
    "conversion": "先看该分组的补贴和活动有没有到期或调整，再看商品价格和库存；转化掉得集中的商品优先排查。",
    "supply": "联系该商家确认门店下线的原因和恢复时间；下线期间，把流量引到同城同品类的其他在线门店。",
    "redemption": "确认该分组的门店有没有预约排满、临时停业或核销设备问题；未核销的订单要提醒用户有效期，防止转成退款。",
    "refund": "按退款类型和门店看退款集中在哪里；如果集中在少数门店，核实是否停业或服务出了问题，并跟进用户补偿。",
    "holiday": "节假日带来的变化属于预期内；备好节后回落的对比口径，把节前支付、节后核销的订单单独跟踪。",
}


def _change(con, metric: str, week: str, seg: dict) -> float | None:
    """Relative change (%) of a metric inside a segment against the baseline, or None if it cannot be cut that way."""
    m = M.metric(metric)
    if any(d not in M.dims_of(m) for d in seg):
        return None
    a = D.breakdown(con, metric, week, None, seg).attrs
    return 100.0 * a["total_change"] / abs(a["total_base"]) if a["total_base"] else None


def lever_of(con, week: str, seg: dict) -> tuple[str | None, str]:
    """(lever, direction) for a segment, by the funnel-order rule."""
    c = {name: _change(con, name, week, seg) for name in ("online_stores", "visits", "conversion_rate", "gmv", "refund_amt", "redeem_gmv")}
    hit = lambda v, t: v is not None and abs(v) >= t
    if c["online_stores"] is not None and c["online_stores"] <= -LEVER_MIN["supply"]:
        return "supply", "down"
    if hit(c["visits"], LEVER_MIN["traffic"]):
        return "traffic", "down" if c["visits"] < 0 else "up"
    if hit(c["conversion_rate"], LEVER_MIN["conversion"]):
        return "conversion", "down" if c["conversion_rate"] < 0 else "up"
    if c["refund_amt"] is not None and c["refund_amt"] >= LEVER_MIN["refund"] and not hit(c["gmv"], 15.0):
        return "refund", "up"
    if hit(c["redeem_gmv"], LEVER_MIN["redemption"]) and not hit(c["gmv"], 15.0):
        return "redemption", "down" if c["redeem_gmv"] < 0 else "up"
    return None, "down" if (c["gmv"] or 0) < 0 else "up"


def _part(con, ms: dict, week: str, base: list[str], filters: dict) -> tuple[float, float]:
    """(baseline weekly average, this week) of one measure inside a segment."""
    rows = dict(con.execute(f"SELECT week_id, v FROM ({M.measure_sql(ms, [], filters, base + [week])})").fetchall())
    return sum(float(rows.get(w) or 0.0) for w in base) / len(base), float(rows.get(week) or 0.0)


def still_unusual(con, week: str, alert, accepted: list[dict]) -> bool:
    """Is this alert still past its threshold once the rows of the accepted segments are taken out?"""
    m = M.metric(alert["metric"])
    num, den = M.parts(m)
    if not num.get("additive", True):
        return not accepted          # a distinct count cannot be split; it follows the other alerts
    scope = {} if alert["dim"] == "total" else {alert["dim"]: alert["slice"]}
    base = D.baseline_weeks(con, week)
    n0, n1 = _part(con, num, week, base, scope)
    d0, d1 = _part(con, den, week, base, scope) if den else (1.0, 1.0)
    for seg in accepted:
        if any(d in scope and scope[d] != v for d, v in seg.items()):
            continue                 # the accepted segment lies outside this slice
        both = {**scope, **seg}
        if all(d in num["dims"] for d in seg):
            a0, a1 = _part(con, num, week, base, both); n0 -= a0; n1 -= a1
        if den and all(d in den["dims"] for d in seg):
            a0, a1 = _part(con, den, week, base, both); d0 -= a0; d1 -= a1
    if n0 <= 0 or d0 <= 0 or d1 <= 0:
        return False                 # nothing is left of the slice outside the accepted segments
    k = M.scale(m)
    v0, v1 = k * n0 / d0, k * n1 / d1
    change = 100.0 * (v1 - v0) / abs(v0) if M.change_kind(m) == "rel" else v1 - v0
    return abs(change) >= float(m["min_change"]) * (1 if alert["dim"] == "total" else 3)


def localise(con, week: str, alerts: pd.DataFrame) -> list[dict]:
    """Segments that explain the alerts, strongest alert first.

    An alert that is no longer past its threshold once the already accepted segments are removed
    is a consequence of them and starts no new cause.
    """
    order = alerts.reindex(alerts["robust_z"].abs().sort_values(ascending=False).index)
    sl = order[order["dim"] != "total"]
    queue = sl if not sl.empty else order.head(1)
    accepted: list[dict] = []
    for _, alert in queue.iterrows():
        if not still_unusual(con, week, alert, [a["segment"] for a in accepted]):
            continue
        seg = {} if alert["dim"] == "total" else {alert["dim"]: alert["slice"]}
        lever, direction = lever_of(con, week, seg)
        drill = CANON[lever] if lever else alert["metric"]
        cands = D.adtributor(con, drill, week, filters=seg)
        if cands and len(cands[0]["slices"]) == 1:      # one slice explains the scope by Adtributor's rule
            narrower = {**seg, cands[0]["dim"]: cands[0]["slices"][0]}
            lever2, direction2 = lever_of(con, week, narrower)
            if lever2 is not None:
                seg, lever, direction = narrower, lever2, direction2
        if not any(seg == a["segment"] for a in accepted):
            accepted.append({"segment": seg, "metric": drill, "scope": {} if alert["dim"] == "total" else {alert["dim"]: alert["slice"]},
                             "lever": lever, "direction": direction})
    return accepted


def analyse(con, week: str, folder) -> dict:
    """Run the whole baseline for one week. Returns the same structure the model arms return."""
    ev = Evidence(folder)
    tb = Toolbox(con, week, ev)
    tb.overview()
    scan = tb.scan
    alerts = scan[scan["is_alert"]]
    total = scan[scan["dim"] == "total"]
    blocks: list[dict] = []
    causes: list[dict] = []
    period = ev.text("q01").splitlines()[1]
    q = lambda qid, line: {"source": qid, "quote": line}
    head_row = lambda name: next(l for l in ev.text("q01").splitlines() if l.startswith(f"| {M.metric(name)['label']} |"))

    n_total = int((alerts["dim"] == "total").sum())
    n_slice = int((alerts["dim"] != "total").sum())
    gmv, orders = total[total["metric"] == "gmv"].iloc[0], total[total["metric"] == "orders"].iloc[0]
    blocks.append({"kind": "summary",
                   "text": f"{week}：支付GMV {gmv['this_week']:.2f} 元，较上周 {gmv['change_vs_last_week']:+.2f}%，较基线 {gmv['change_vs_base']:+.2f}%；"
                           f"支付订单数 {orders['this_week']:.0f} 单，较上周 {orders['change_vs_last_week']:+.2f}%，较基线 {orders['change_vs_base']:+.2f}%。",
                   "evidence": [q("q01", period), q("q01", head_row("gmv")), q("q01", head_row("orders"))]})
    if alerts.empty:
        blocks.append({"kind": "summary", "text": "本周没有指标触发异动，各项指标都在日常波动范围内。", "evidence": []})
        return {"week": week, "has_anomaly": False, "causes": [], "blocks": blocks, "evidence": ev.items}
    names = "、".join(alerts.loc[alerts["dim"] == "total", "label"].tolist()) or "无"
    blocks.append({"kind": "summary", "text": f"总量层面触发异动的指标：{names}。另有分组层面的异动，见下文。" if n_slice else f"总量层面触发异动的指标：{names}。",
                   "evidence": []})
    # findings: the three strongest alerts, each with its row
    strongest = alerts.reindex(alerts["robust_z"].abs().sort_values(ascending=False).index)
    strongest = (strongest[strongest["dim"] != "total"] if n_slice else strongest).head(3)
    q2 = ev.text("q02").splitlines()
    for a in strongest.itertuples():
        if a.dim == "total":
            src, line, where = "q01", head_row(a.metric), "全部业务"
        else:
            src, where = "q02", f"{C.DIMENSIONS[a.dim]}={a.slice}"
            line = next(l for l in q2 if l.startswith(f"| {a.label} | {a.unit} | {C.DIMENSIONS[a.dim]} | {a.slice} |"))
        cells = [c.strip() for c in line.split("|")]
        off = 0 if a.dim == "total" else 2
        blocks.append({"kind": "finding",
                       "text": f"{where} 的{a.label}本周是 {cells[3 + off]}，基线中位数是 {cells[5 + off]}，较基线 {cells[7 + off]}。",
                       "evidence": [q(src, line)]})

    if tb.info["is_holiday_week"]:
        causes.append({"segment": {}, "lever": "holiday", "direction": "up"})
        blocks.append({"kind": "cause", "text": f"本周含法定节假日（{tb.info['holiday_names']}），指标的变化按节假日效应看待，不单独归因到分组。",
                       "evidence": [q("q01", period)]})
        blocks.append({"kind": "advice", "text": ADVICE["holiday"], "evidence": []})
        return {"week": week, "has_anomaly": True, "causes": causes, "blocks": blocks, "evidence": ev.items}

    for f in localise(con, week, alerts):
        seg, metric, lever, direction = f["segment"], f["metric"], f["lever"], f["direction"]
        m = M.metric(metric)
        # evidence: the breakdown that shows this segment, and its funnel
        last_dim = [d for d in seg if d not in f["scope"]] or list(seg)
        parent = {d: v for d, v in seg.items() if d != last_dim[-1]} if seg else {}
        if seg:
            tb.breakdown(metric, last_dim[-1], parent or None)
            bid = ev.last_id
            line = next((l for l in ev.text(bid).splitlines() if l.startswith(f"| {seg[last_dim[-1]]} |")), None)
        else:
            bid, line = None, None
        tb.funnel(seg or None)
        fid = ev.last_id
        ftxt = ev.text(fid).splitlines()
        frow = lambda label: next((l for l in ftxt if l.startswith(f"| {label} |")), None)
        cell = lambda l, i: l.split("|")[i].strip()
        where = segment_text(seg)
        if lever is None:
            text = f"{where} 的{m['label']}出现异动，但流量、转化、供给、核销、退款各环节都没有超过判定门槛，原因待查。"
            evid = [q(bid, line)] if line else [q(fid, ftxt[0])]
        else:
            label = {"supply": "在线门店数", "traffic": "访问次数", "conversion": "访问支付转化率", "refund": "退款金额", "redemption": "核销GMV"}[lever]
            fr = frow(label)
            text = (f"原因（{C.LEVERS[lever]}）：{where} 的{label}从基线的 {cell(fr, 3)} 变为本周的 {cell(fr, 4)}，"
                    f"相对变化 {cell(fr, 6)}%。")
            evid = [q(fid, fr)]
            if line and m["type"] != "distinct":
                text += f"按{C.DIMENSIONS[last_dim[-1]]}拆{m['label']}，这一组的贡献度是 {cell(line, 6 if m['type'] != 'ratio' else 8)}%。"
                evid.append(q(bid, line))
        causes.append({"segment": seg, "lever": lever, "direction": direction})
        blocks.append({"kind": "cause", "text": text, "evidence": evid})
        if lever:
            blocks.append({"kind": "advice", "text": f"针对{where}：{ADVICE[lever]}", "evidence": [q(fid, ftxt[0])]})
    return {"week": week, "has_anomaly": True, "causes": causes, "blocks": blocks, "evidence": ev.items}
