"""A one-off analysis written as a report: did redemption really fall over the October holiday?

  python -m bizagent.special

The weekly redemption rate (redeemed this week / paid this week) drops sharply in the holiday weeks
and overshoots right after. This script builds a quiet world that runs through the holiday (no event
planted), runs the queries, fills the report's sentences from the query results, puts the report
through the same number check as the weekly reviews, and writes reports/专项分析_节假日前后的核销率.md
with one chart. The wording is fixed here; every number in it is read from a query result.
"""
from __future__ import annotations

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from . import charts, config as C, db, report, world  # noqa: E402
from .evidence import Evidence, check_blocks, feedback, row, save_json  # noqa: E402

SEED, LAST_WEEK = 2026, 45
OUT = C.ROOT / "reports"
FLOW_SQL = """SELECT week_id AS 周,
       ROUND(MAX(value) FILTER (WHERE metric = 'gmv'), 2)         AS 支付GMV_元,
       ROUND(MAX(value) FILTER (WHERE metric = 'redeem_gmv'), 2)  AS 核销GMV_元,
       ROUND(MAX(value) FILTER (WHERE metric = 'redeem_rate'), 2) AS 当周核销率_pct,
       ROUND(MAX(value) FILTER (WHERE metric = 'refund_amt'), 2)  AS 退款金额_元,
       ROUND(MAX(value) FILTER (WHERE metric = 'refund_rate'), 2) AS 当周退款率_pct
FROM metric_weekly
WHERE dim = 'total' AND week_id >= '2026-W36'
GROUP BY week_id
ORDER BY week_id"""
COHORT_SQL = """SELECT pay_week AS 支付周, orders AS 订单数, gmv AS 支付GMV_元,
       redeem_rate_7d AS 七日核销率_pct, redeem_rate_30d AS 三十日核销率_pct,
       refund_rate_final AS 最终退款率_pct, median_days_to_redeem AS 核销间隔中位数_天
FROM ({cohort})
WHERE pay_week >= '2026-W36'
ORDER BY pay_week"""
DEFINE = "7 日核销率：这一周支付的订单里，支付后 7 天内核销的金额占比；30 日核销率同理，看支付后 30 天内。最终退款率：这批订单最后退掉的金额占比。"
REFUND_SQL = """SELECT d.week_id AS 退款周, f.refund_type AS 退款类型,
       ROUND(SUM(f.refund_amount), 2) AS 退款金额_元,
       ROUND(SUM(f.refund_amount) FILTER (WHERE p.is_holiday_week), 2) AS 其中节假日周支付的订单_元
FROM fact_refund f
JOIN dim_date d ON d.date = f.refund_date
JOIN fact_order o ON o.order_id = f.order_id
JOIN dim_date pd ON pd.date = o.pay_date
JOIN dim_week p ON p.week_id = pd.week_id
WHERE d.week_id IN ('2026-W38', '2026-W44', '2026-W45')
GROUP BY d.week_id, f.refund_type
ORDER BY d.week_id, f.refund_type"""


def build():
    path = C.DATA / "special.duckdb"
    if not path.exists():
        db.build(world.generate(SEED, LAST_WEEK, []), path)
    return db.connect(path)


def chart(flow, cohort, path):
    weeks = list(flow["周"])
    x = range(len(weeks))
    by_week = dict(zip(cohort["支付周"], cohort["七日核销率_pct"]))
    seven = [by_week.get(w) for w in weeks]
    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=180)
    for i, w in enumerate(weeks):
        if w in ("2026-W39", "2026-W40", "2026-W41"):
            ax.axvspan(i - 0.5, i + 0.5, color=charts.RULE, alpha=0.45, lw=0)
    ax.plot(x, flow["当周核销率_pct"], color=charts.SERIES_2, lw=2, marker="o", ms=4, label="当周口径：当周核销GMV ÷ 当周支付GMV")
    ax.plot(x, seven, color=charts.SERIES_1, lw=2, marker="o", ms=4, label="按批次：这一周支付的订单，7 天内核销的比例")
    ax.set_xticks(list(x)); ax.set_xticklabels([w[-3:] for w in weeks])
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.set_ylim(55, 100)
    ax.grid(axis="y", color=charts.RULE, lw=0.6); ax.set_axisbelow(True)
    ax.legend(loc="upper left", frameon=False, fontsize=9, labelcolor=charts.INK2)
    charts._title(ax, "两种口径的核销率：节假日前后", "灰底是含法定节假日的周（中秋、国庆）。最后一周的 7 日核销率要再过 7 天才有。合成数据。")
    fig.tight_layout()
    fig.savefig(path); plt.close(fig)


def main() -> int:
    con = build()
    folder = OUT / "special-evidence"
    ev = Evidence(folder)
    period = "数据范围：2026-W36 至 2026-W45，其中 2026-W39 含中秋，2026-W40、2026-W41 含国庆。"
    cohort_sql = (C.ROOT / "sql" / "metrics" / "05_cohort_redeem.sql").read_text(encoding="utf-8").strip().rstrip(";")
    flow = db.query(con, FLOW_SQL)
    ev.add("当周口径的支付、核销、退款（按周）", flow, [period, "当周核销率 = 当周核销GMV ÷ 当周支付GMV；当周退款率 = 当周退款金额 ÷ 当周支付GMV。"], sql=FLOW_SQL)
    cohort = db.query(con, COHORT_SQL.format(cohort=cohort_sql))
    ev.add("按支付周分批的核销率和退款率", cohort, [period, DEFINE], sql=COHORT_SQL.format(cohort=cohort_sql))
    refund = db.query(con, REFUND_SQL)
    ev.add("节前和节后的退款，按退款类型，以及其中来自节假日周订单的部分", refund, [period], sql=REFUND_SQL)
    con.close()

    f = lambda w: flow[flow["周"] == w].iloc[0]
    c = lambda w: cohort[cohort["支付周"] == w].iloc[0]
    fq = lambda w: {"source": "q01", "quote": row(flow, int(flow.index[flow["周"] == w][0]))}
    define = {"source": "q02", "quote": DEFINE}
    cq = lambda w: {"source": "q02", "quote": row(cohort, int(cohort.index[cohort["支付周"] == w][0]))}
    r = lambda w, t: refund[(refund["退款周"] == w) & (refund["退款类型"] == t)].iloc[0]
    rq = lambda w, t: {"source": "q03", "quote": row(refund, int(refund.index[(refund["退款周"] == w) & (refund["退款类型"] == t)][0]))}
    n = lambda v: f"{float(v):,.2f}".rstrip("0").rstrip(".") if abs(float(v)) >= 1000 else db.fmt(float(v))
    blocks = [
        {"kind": "summary", "evidence": [],
         "text": "问题：监控里的核销率在中秋和国庆那几周大幅下降，节后又冲高，退款率在节后也触发了异动。这是核销和退款真的出了问题，还是口径造成的？"},
        {"kind": "summary", "evidence": [fq("2026-W38"), fq("2026-W39"), fq("2026-W41"), cq("2026-W38"), cq("2026-W39"), cq("2026-W41"), define],
         "text": f"结论：是口径造成的。当周口径的核销率从 2026-W38 的 {n(f('2026-W38')['当周核销率_pct'])}% 掉到 2026-W39 的 {n(f('2026-W39')['当周核销率_pct'])}%，"
                 f"又在 2026-W41 升到 {n(f('2026-W41')['当周核销率_pct'])}%；同样这几周支付的订单，7 日核销率分别是 {n(c('2026-W38')['七日核销率_pct'])}%、"
                 f"{n(c('2026-W39')['七日核销率_pct'])}%、{n(c('2026-W41')['七日核销率_pct'])}%，几乎没有变。"},
        {"kind": "finding", "evidence": [fq("2026-W38"), fq("2026-W40")],
         "text": f"节假日周支付放量，分母变大。支付GMV 从 2026-W38 的 {n(f('2026-W38')['支付GMV_元'])} 元升到 2026-W40 的 {n(f('2026-W40')['支付GMV_元'])} 元；"
                 f"同期核销GMV 从 {n(f('2026-W38')['核销GMV_元'])} 元升到 {n(f('2026-W40')['核销GMV_元'])} 元，涨得慢，因为节日里买的券有一部分要过几天才用。"
                 f"当周核销率于是从 {n(f('2026-W38')['当周核销率_pct'])}% 落到 {n(f('2026-W40')['当周核销率_pct'])}%。"},
        {"kind": "finding", "evidence": [fq("2026-W40"), fq("2026-W41")],
         "text": f"节后支付回落，节日里买的券集中到店，分子反而更大。2026-W41 的支付GMV 回到 {n(f('2026-W41')['支付GMV_元'])} 元，核销GMV 却升到 {n(f('2026-W41')['核销GMV_元'])} 元，"
                 f"当周核销率升到 {n(f('2026-W41')['当周核销率_pct'])}%，高于 2026-W40 的 {n(f('2026-W40')['当周核销率_pct'])}%。"},
        {"kind": "finding", "evidence": [cq("2026-W38"), cq("2026-W39"), cq("2026-W40"), cq("2026-W41"), define],
         "text": f"固定一批订单往后看，核销没有变化。2026-W38、2026-W39、2026-W40、2026-W41 支付的订单，7 日核销率是 {n(c('2026-W38')['七日核销率_pct'])}%、{n(c('2026-W39')['七日核销率_pct'])}%、"
                 f"{n(c('2026-W40')['七日核销率_pct'])}%、{n(c('2026-W41')['七日核销率_pct'])}%；30 日核销率在已经走完观察期的 2026-W38、2026-W39、2026-W40 是 "
                 f"{n(c('2026-W38')['三十日核销率_pct'])}%、{n(c('2026-W39')['三十日核销率_pct'])}%、{n(c('2026-W40')['三十日核销率_pct'])}%。"},
        {"kind": "finding", "evidence": [fq("2026-W38"), fq("2026-W45"), rq("2026-W38", "过期退"), rq("2026-W45", "过期退")],
         "text": f"节后的退款率偏高，是节日订单到期带来的。当周退款率从 2026-W38 的 {n(f('2026-W38')['当周退款率_pct'])}% 升到 2026-W45 的 {n(f('2026-W45')['当周退款率_pct'])}%。"
                 f"过期退的金额从 2026-W38 的 {n(r('2026-W38', '过期退')['退款金额_元'])} 元升到 2026-W45 的 {n(r('2026-W45', '过期退')['退款金额_元'])} 元；"
                 f"2026-W45 的过期退里，节假日周支付的订单占 {n(r('2026-W45', '过期退')['其中节假日周支付的订单_元'])} 元。"},
        {"kind": "finding", "evidence": [cq("2026-W38"), cq("2026-W39"), cq("2026-W40")],
         "text": f"按批次看，最终退款率同样稳定：2026-W38、2026-W39、2026-W40 支付的订单，最终退款率是 {n(c('2026-W38')['最终退款率_pct'])}%、{n(c('2026-W39')['最终退款率_pct'])}%、{n(c('2026-W40')['最终退款率_pct'])}%。"},
        {"kind": "cause", "evidence": [fq("2026-W39"), cq("2026-W39")],
         "text": "原因在口径：当周核销率、当周退款率的分子和分母不是同一批订单。分母是当周支付的订单，分子里有大量前几周支付的订单。支付量一波动，这两个率就跟着反向摆动，和核销、退款本身的好坏无关。"},
        {"kind": "advice", "evidence": [define],
         "text": "周监控里，把按批次的 7 日核销率设为判断核销是否健康的主指标；当周核销率只用来对账，不单独报异动。"},
        {"kind": "advice", "evidence": [],
         "text": "节假日周和节后的几周，当周核销率、当周退款率的异动先标成“节假日余波”，再看按批次的口径有没有同步变化；同步变了才升级处理。"},
        {"kind": "advice", "evidence": [],
         "text": "给商家看的周报同样处理：节后核销冲高不写成“核销改善”，节后退款偏高不写成“退款恶化”，避免商家据此误调券的有效期或库存。"},
        {"kind": "note", "evidence": [],
         "text": "局限：按批次的口径要等观察期走完才有数，最新的那一周还没有按批次的核销率；它回答的是“这批券用掉多少”，不能替代当周的现金流口径。数据是合成的，没有埋任何事件。"},
    ]
    result = {"week": "2026-W36 至 2026-W45", "has_anomaly": True, "causes": [], "blocks": blocks, "evidence": ev.items}
    check = check_blocks(blocks, folder, True)
    save_json(OUT / "special.json", {"result": result, "check": check})
    if not check["released"]:
        print(feedback(check, blocks))
        return 1
    chart(flow, cohort, OUT / "special_redeem_rate.png")
    text = report.render(result, check, folder, title="专项分析：节假日前后的核销率")
    text = text.replace("\n## 一、摘要", "\n![两种口径的核销率](special_redeem_rate.png)\n\n## 一、摘要", 1)
    (OUT / "专项分析_节假日前后的核销率.md").write_text(text, encoding="utf-8", newline="\n")
    print(f"released: {check['released']}; {check['checked']} blocks checked, {check['supported']} supported")
    print("written:", OUT / "专项分析_节假日前后的核销率.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
