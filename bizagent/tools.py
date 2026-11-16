"""The analysis tools. The rule-based script, the one-prompt pack and the agent all use these.

Each call writes an evidence file and returns the text a model is shown (the same file without SQL).
"""
from __future__ import annotations

import pandas as pd

from . import config as C
from . import db, decompose as D, metrics as M, monitor
from .evidence import Evidence, segment_text

FUNNEL = ["visits", "conversion_rate", "orders", "gmv", "aov", "subsidy_rate", "online_stores", "active_stores",
          "redeem_gmv", "redeem_rate", "refund_amt", "refund_rate"]


class Toolbox:
    def __init__(self, con, week: str, evidence: Evidence):
        self.con, self.week, self.ev = con, week, evidence
        self.info = monitor.week_info(con, week)
        self.base_weeks = D.baseline_weeks(con, week)
        self.scan: pd.DataFrame | None = None

    # -- helpers -----------------------------------------------------------------------------
    def _period(self, compare: str = "baseline") -> str:
        head = f"分析周：{self.week}（{self.info['week_start']} 至 {self.info['week_end']}）"
        if self.info["is_holiday_week"]:
            head += f"，这一周含法定节假日：{self.info['holiday_names']}"
        if compare == "baseline":
            return (f"{head}。对比：基线，即本周之前最近 {len(self.base_weeks)} 个非节假日周"
                    f"（{self.base_weeks[-1]} 至 {self.base_weeks[0]}）。")
        return f"{head}。对比：上周（{D.previous_week(self.con, self.week)}）。"

    # -- tools -------------------------------------------------------------------------------
    def overview(self) -> str:
        """All metrics this week against last week and the baseline, and the slices that raised an alert."""
        s = monitor.scan(self.con, self.week)
        self.scan = s
        unit = lambda r: "pp" if r.change_kind == "abs" else "%"
        def shape(d: pd.DataFrame, with_slice: bool) -> pd.DataFrame:
            out = pd.DataFrame({"指标": d["label"], "单位": d["unit"]})
            if with_slice:
                out["维度"] = d["dim"].map(C.DIMENSIONS); out["分组"] = d["slice"]
            out["本周"] = d["this_week"]; out["上周"] = d["last_week"]; out["基线中位数"] = d["base_median"]
            out["较上周"] = [f"{v:+.2f}{unit(r)}" if v == v else "" for v, r in zip(d["change_vs_last_week"], d.itertuples())]
            out["较基线"] = [f"{v:+.2f}{unit(r)}" if v == v else "" for v, r in zip(d["change_vs_base"], d.itertuples())]
            out["修正Z"] = d["robust_z"]
            out["是否异动"] = ["是" if a else "否" for a in d["is_alert"]]
            return out
        total = s[s["dim"] == "total"]
        order = {m["name"]: i for i, m in enumerate(M.catalog()["metrics"])}
        total = total.sort_values("metric", key=lambda c: c.map(order))
        notes = [self._period(), "基线中位数：这些周里该指标的中位数。较上周、较基线：数量和金额类指标是相对变化（%），"
                 "百分比类指标是绝对变化（pp，即个百分点）。",
                 "是否异动的规则：修正 Z 分数的绝对值不低于 3.5，并且较基线的变化不低于该指标的最小幅度。"]
        text = self.ev.add("本周指标总览（全部指标，总量）", shape(total, False), notes, sql=monitor.ALERT_SQL)
        sl = s[(s["dim"] != "total") & s["is_alert"]]
        notes2 = [self._period(), "分组监控只查九个核心指标、基线期平均占比不低于 5% 的分组；规则：修正 Z 分数的绝对值不低于 6，"
                  "并且较基线的变化不低于该指标最小幅度的 3 倍。"]
        if sl.empty:
            text2 = self.ev.add("本周触发异动的分组", None, notes2 + ["本周没有分组触发异动。"])
        else:
            text2 = self.ev.add("本周触发异动的分组", shape(sl, True), notes2)
        return text + "\n\n" + text2

    def breakdown(self, metric: str, dimension: str, filters: dict | None = None, compare: str = "baseline") -> str:
        """A metric's change split by one dimension, optionally inside a segment given by `filters`."""
        df = D.breakdown(self.con, metric, self.week, dimension, filters, compare)
        m = M.metric(metric)
        note = [self._period(compare), D.total_line(df)]
        if m["type"] == "ratio":
            note.append("率效应：这个分组自己的比率变了带来的影响。结构效应：这个分组在分母里的占比变了带来的影响。"
                        "两者相加是合计贡献，所有分组的合计贡献相加等于总变化。")
        elif not M.parts(m)[0].get("additive", True):
            note.append("这是去重计数，各分组相加会大于总数，所以不算贡献度。")
        title = f"拆解：{m['label']} 按{C.DIMENSIONS[dimension]}（范围：{segment_text(filters or {})}）"
        sql = M.series_sql(m, [dimension], filters, df.attrs["base_weeks"] + [self.week])
        text = self.ev.add(title, D.table(df), note, sql=sql)
        if len(df) > 12:
            text += f"\n（共 {len(df)} 个分组，只显示贡献最大的 12 个）"
        return text

    def funnel(self, filters: dict | None = None, compare: str = "baseline") -> str:
        """Health check of one segment: the funnel metrics this week against the comparison period."""
        rows = []
        for name in FUNNEL:
            m = M.metric(name)
            if any(d not in M.dims_of(m) for d in (filters or {})):
                continue
            df = D.breakdown(self.con, name, self.week, None, filters, compare)
            a = df.attrs
            pct = 100 * a["total_change"] / abs(a["total_base"]) if a["total_base"] else float("nan")
            rows.append({"指标": m["label"], "单位": m["unit"], "基线" if compare == "baseline" else "上周": round(a["total_base"], 2),
                         "本周": round(a["total_cur"], 2), "变化": round(a["total_change"], 2), "相对变化_pct": round(pct, 2)})
        title = f"分组体检（范围：{segment_text(filters or {})}）"
        note = [self._period(compare), "变化：本周减对比期，单位同该指标（百分比类指标是 pp）。相对变化_pct：变化 ÷ 对比期的值。",
                "没有列出的指标不能按这个范围拆（例如访问次数没有商家维度，门店数没有渠道维度）。"]
        return self.ev.add(title, pd.DataFrame(rows), note)

    def run_sql(self, sql: str, purpose: str = "") -> str:
        """One read-only SELECT against the database."""
        df = db.query(self.con, sql)
        return self.ev.add(f"查询：{purpose or '自定义 SQL'}", df, [self._period().split("。")[0] + "。"], sql=sql)
