"""The weekly review as an Excel workbook.

The workbook holds the weekly data as plain numbers and derives everything else with formulas:
pick a week in one cell and the overview (this week, last week, baseline median, MAD, robust Z,
alert flag), the contribution tables and the rate / mix split all recalculate. The alert rule is the
same one as sql/monitor/weekly_alerts.sql, written in worksheet functions.

Only functions that exist since Excel 2007 are used (INDEX, MATCH, LARGE, MEDIAN, SUMIFS, IF).
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as L
from openpyxl.worksheet.datavalidation import DataValidation

from . import config as C
from . import decompose as D, metrics as M

FONT = "Microsoft YaHei"
HEAD_FILL = PatternFill("solid", fgColor="1F2937")
SOFT_FILL = PatternFill("solid", fgColor="F3F4F6")
INPUT_FILL = PatternFill("solid", fgColor="FFFF00")
ALERT_FILL = PatternFill(start_color="FDE2E1", end_color="FDE2E1", fill_type="solid")
THIN = Side(style="thin", color="D1D5DB")
BOX = Border(top=THIN, bottom=THIN, left=THIN, right=THIN)
MONEY, COUNT, PCT, PP, ZFMT = "#,##0.00", "#,##0", "0.00%", '+0.00"pp";-0.00"pp"', "0.00"

# sheet 周数据: column of every stored measure, then of every derived metric (a formula)
RAW = [("visits", "访问次数"), ("orders", "支付订单数"), ("gmv", "支付GMV"), ("subsidy", "平台补贴"), ("new_orders", "新客数"),
       ("redeem_gmv", "核销GMV"), ("commission", "佣金收入"), ("refund_amt", "退款金额"), ("online_stores", "在线门店数"),
       ("active_stores", "动销门店数"), ("new_stores", "新上线门店数"), ("buyers", "支付用户数")]
DERIVED = [("conversion_rate", "orders", "visits"), ("aov", "gmv", "orders"), ("redeem_rate", "redeem_gmv", "gmv"),
           ("active_store_rate", "active_stores", "online_stores"), ("gmv_per_active_store", "gmv", "active_stores"),
           ("take_rate", "commission", "redeem_gmv"), ("subsidy_rate", "subsidy", "gmv"), ("refund_rate", "refund_amt", "gmv"),
           ("new_buyer_share", "new_orders", "orders")]
FIRST_DATA_COL = 5   # A 周, B 周起始, C 周结束, D 含节假日, then the measures
SLICE_MEASURES = ["visits", "orders", "gmv", "redeem_gmv", "refund_amt"]


def _style_header(ws, row: int, first: int, last: int):
    for c in range(first, last + 1):
        cell = ws.cell(row=row, column=c)
        cell.font = Font(name=FONT, bold=True, color="FFFFFF", size=10)
        cell.fill = HEAD_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BOX


def _font_all(ws):
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is not None and not cell.font.bold:
                cell.font = Font(name=FONT, size=10, color=cell.font.color)


def _weekly(con) -> tuple[list[str], dict]:
    weeks = [r[0] for r in con.execute("SELECT week_id FROM dim_week WHERE in_report ORDER BY 1").fetchall()]
    info = {r[0]: r[1:] for r in con.execute("SELECT week_id, week_start, week_end, is_holiday_week FROM dim_week WHERE in_report").fetchall()}
    data = {w: {} for w in weeks}
    ms = M.catalog()["measures"]
    for name, _ in RAW:
        for w, v in con.execute(f"SELECT week_id, v FROM ({M.measure_sql(ms[name], [])})").fetchall():
            data[w][name] = float(v) if v is not None else 0.0
    return weeks, {"info": info, "data": data}


def make_weekly(con, week: str, path: Path) -> Path:
    weeks, wk = _weekly(con)
    n = len(weeks)
    last_row = n + 1
    col = {name: FIRST_DATA_COL + i for i, (name, _) in enumerate(RAW)}
    for i, (name, _num, _den) in enumerate(DERIVED):
        col[name] = FIRST_DATA_COL + len(RAW) + i
    col["net_revenue"] = FIRST_DATA_COL + len(RAW) + len(DERIVED)
    helper_col = col["net_revenue"] + 1

    wb = Workbook()
    # ------------------------------------------------------------------ 说明
    note = wb.active
    note.title = "说明"
    lines = [
        ("到店团购经营周报", True),
        ("数据是脚本生成的合成数据，不是任何平台的真实经营数据；指标口径是为这份数据拟的。", False),
        ("", False),
        ("怎么用", True),
        ("1. 在“总览”表的黄色格子里选分析周，整本表都会跟着重算。", False),
        ("2. “总览”：每个指标的本周、上周、基线中位数、修正 Z 分数和是否异动，全部是公式。", False),
        ("3. “拆解”：支付GMV 按渠道、城市、品类的贡献度，以及核销率按品类拆成率效应和结构效应。", False),
        ("4. “周数据”“分组数据”是底表：前面几列是从数据库取出的数，后面的比率列是公式。", False),
        ("", False),
        ("规则", True),
        ("基线：分析周之前最近 8 个不含法定节假日的周。", False),
        ("修正 Z = 0.6745 ×（本周 − 基线中位数）÷ MAD；MAD 是基线各周与中位数之差的绝对值的中位数。", False),
        ("异动：修正 Z 的绝对值不低于 3.5，并且较基线的变化不低于该指标的最小幅度。", False),
        ("贡献度 = 这一组的变化 ÷ 总变化。率效应和结构效应相加等于核销率的总变化。", False),
        ("", False),
        ("颜色", True),
        ("黄色底：可以改的输入格。红色底：触发异动。", False),
    ]
    for i, (text, bold) in enumerate(lines, 1):
        c = note.cell(row=i, column=1, value=text)
        c.font = Font(name=FONT, bold=bold, size=14 if i == 1 else 10)
    note.column_dimensions["A"].width = 110

    # ------------------------------------------------------------------ 周数据
    ws = wb.create_sheet("周数据")
    heads = ["周", "周起始", "周结束", "含节假日"] + [label for _, label in RAW] + \
            [M.metric(name)["label"] for name, _, _ in DERIVED] + ["净收入", "可作基线的行号"]
    ws.append(heads)
    _style_header(ws, 1, 1, len(heads))
    for i, w in enumerate(weeks, 2):
        start, end, hol = wk["info"][w]
        ws.cell(row=i, column=1, value=w)
        ws.cell(row=i, column=2, value=start).number_format = "yyyy-mm-dd"
        ws.cell(row=i, column=3, value=end).number_format = "yyyy-mm-dd"
        ws.cell(row=i, column=4, value="是" if hol else "否")
        for name, _ in RAW:
            c = ws.cell(row=i, column=col[name], value=round(wk["data"][w].get(name, 0.0), 2))
            c.number_format = MONEY if name in ("gmv", "subsidy", "redeem_gmv", "commission", "refund_amt") else COUNT
        for name, num, den in DERIVED:
            c = ws.cell(row=i, column=col[name], value=f"=IF({L(col[den])}{i}=0,0,{L(col[num])}{i}/{L(col[den])}{i})")
            c.number_format = PCT if M.metric(name)["unit"] == "%" else MONEY
        ws.cell(row=i, column=col["net_revenue"], value=f"={L(col['commission'])}{i}-{L(col['subsidy'])}{i}").number_format = MONEY
        # the row number when this week may serve as a baseline week for the selected week, else 0
        ws.cell(row=i, column=helper_col, value=f'=IF(AND(ROW()<总览!$B$4,D{i}="否"),ROW(),0)')
    ws.freeze_panes = "B2"
    for c in range(1, len(heads) + 1):
        ws.column_dimensions[L(c)].width = 13
    _font_all(ws)

    # ------------------------------------------------------------------ 分组数据
    sd = wb.create_sheet("分组数据")
    sd.append(["周", "维度", "分组"] + [dict(RAW)[m] for m in SLICE_MEASURES])
    _style_header(sd, 1, 1, 3 + len(SLICE_MEASURES))
    ms = M.catalog()["measures"]
    slices: dict[tuple, dict] = {}
    members: dict[str, list[str]] = {}
    for dim in ("channel", "city", "category", "merchant_type"):
        for name in SLICE_MEASURES:
            if dim not in ms[name]["dims"]:
                continue
            for w, s, v in con.execute(f"SELECT week_id, {dim}, v FROM ({M.measure_sql(ms[name], [dim])})").fetchall():
                slices.setdefault((w, dim, s), {})[name] = float(v or 0.0)
        order = con.execute(f"SELECT {dim} FROM cube_trade GROUP BY 1 ORDER BY SUM(gmv) DESC").fetchall()
        members[dim] = [r[0] for r in order]
    r = 2
    for w in weeks:
        for dim in ("channel", "city", "category", "merchant_type"):
            for s in members[dim]:
                vals = slices.get((w, dim, s), {})
                sd.cell(row=r, column=1, value=w); sd.cell(row=r, column=2, value=C.DIMENSIONS[dim]); sd.cell(row=r, column=3, value=s)
                for j, name in enumerate(SLICE_MEASURES):
                    if name in vals:
                        sd.cell(row=r, column=4 + j, value=round(vals[name], 2)).number_format = COUNT if name in ("visits", "orders") else MONEY
                r += 1
    sd_last = r - 1
    sd.freeze_panes = "A2"
    for c, wdt in zip("ABCDEFGH", (11, 10, 16, 13, 13, 15, 15, 15)):
        sd.column_dimensions[c].width = wdt
    _font_all(sd)
    S = lambda name: f"分组数据!${L(4 + SLICE_MEASURES.index(name))}$2:${L(4 + SLICE_MEASURES.index(name))}${sd_last}"
    S_WEEK, S_DIM, S_SLICE = f"分组数据!$A$2:$A${sd_last}", f"分组数据!$B$2:$B${sd_last}", f"分组数据!$C$2:$C${sd_last}"

    # ------------------------------------------------------------------ 总览
    ov = wb.create_sheet("总览", 1)
    ov["A1"] = "到店团购经营周报：指标总览"
    ov["A1"].font = Font(name=FONT, bold=True, size=14)
    ov["A2"], ov["A3"], ov["A4"] = "分析周（可改）", "上周", "分析周在“周数据”里的行号"
    ov["B2"] = week
    ov["B2"].fill = INPUT_FILL
    ov["B2"].font = Font(name=FONT, bold=True, color="0000FF", size=11)
    ov["B4"] = f"=MATCH($B$2,周数据!$A$1:$A${last_row},0)"
    ov["B3"] = "=INDEX(周数据!$A:$A,$B$4-1)"
    ov["A5"] = "基线周（最近 8 个非节假日周）的行号"
    hc = L(helper_col)
    for k in range(8):
        ov.cell(row=5, column=4 + k, value=f"=LARGE(周数据!${hc}$2:${hc}${last_row},{k + 1})")
    ov["A6"] = "基线周"
    for k in range(8):
        ov.cell(row=6, column=4 + k, value=f"=INDEX(周数据!$A:$A,{L(4 + k)}$5)")
    # the first week that has eight baseline weeks before it is row 11 (nine earlier weeks, one of them a holiday week)
    first_ok = next(i for i, w in enumerate(weeks, 2) if sum(1 for x in weeks[: i - 2] if not wk["info"][x][2]) >= 8)
    dv = DataValidation(type="list", formula1=f"=周数据!$A${first_ok}:$A${last_row}", allow_blank=False)
    dv.error, dv.errorTitle = "请从下拉列表里选一周（前面要有 8 个非节假日周）。", "不能用这一周"
    ov.add_data_validation(dv)
    dv.add("B2")

    head_row = 8
    heads = ["分组", "指标", "单位", "本周", "上周", "较上周", "基线中位数", "较基线", "MAD", "修正Z", "最小幅度", "是否异动"]
    for c, h in enumerate(heads, 1):
        ov.cell(row=head_row, column=c, value=h)
    _style_header(ov, head_row, 1, len(heads))
    base_cells = [f"${L(4 + k)}$5" for k in range(8)]
    r = head_row + 1
    for m in M.catalog()["metrics"]:
        name, unit = m["name"], m["unit"]
        key = name if name in col else m.get("measure", name)
        cl = L(col[key])
        is_pct = unit == "%"
        fmt = PCT if is_pct else (MONEY if unit == "元" else COUNT)
        ov.cell(row=r, column=1, value=m["group"]); ov.cell(row=r, column=2, value=m["label"]); ov.cell(row=r, column=3, value=unit)
        ov.cell(row=r, column=4, value=f"=INDEX(周数据!${cl}:${cl},$B$4)").number_format = fmt
        ov.cell(row=r, column=5, value=f"=INDEX(周数据!${cl}:${cl},$B$4-1)").number_format = fmt
        base_vals = [f"INDEX(周数据!${cl}:${cl},{b})" for b in base_cells]
        ov.cell(row=r, column=7, value=f"=MEDIAN({','.join(base_vals)})").number_format = fmt
        ov.cell(row=r, column=9, value="=MEDIAN(" + ",".join(f"ABS({v}-$G{r})" for v in base_vals) + ")").number_format = PCT if is_pct else MONEY
        ov.cell(row=r, column=11, value=float(m["min_change"]))
        if is_pct:   # percentage-type ratios move in percentage points
            ov.cell(row=r, column=6, value=f"=(D{r}-E{r})*100").number_format = PP
            ov.cell(row=r, column=8, value=f"=(D{r}-G{r})*100").number_format = PP
            floor = f"K{r}/1000"
            moved = f"ABS(H{r})>=K{r}"
            ov.cell(row=r, column=11).number_format = '0.00"pp"'
        else:        # everything else moves in percent
            ov.cell(row=r, column=6, value=f"=IF(E{r}=0,0,(D{r}-E{r})/ABS(E{r}))").number_format = "+0.00%;-0.00%"
            ov.cell(row=r, column=8, value=f"=IF(G{r}=0,0,(D{r}-G{r})/ABS(G{r}))").number_format = "+0.00%;-0.00%"
            floor = f"ABS(G{r})*K{r}/1000"
            moved = f"ABS(H{r})*100>=K{r}"
            ov.cell(row=r, column=11).number_format = '0.00"%"'
        ov.cell(row=r, column=10, value=f"=0.6745*(D{r}-G{r})/MAX(I{r},{floor})").number_format = ZFMT
        if m.get("monitor", True):
            ov.cell(row=r, column=12, value=f'=IF(AND(ABS(J{r})>=3.5,{moved}),"是","否")')
        else:
            ov.cell(row=r, column=12, value="不判定")
        for c in range(1, len(heads) + 1):
            ov.cell(row=r, column=c).border = BOX
        r += 1
    ov_last = r - 1
    ov.conditional_formatting.add(f"A{head_row + 1}:L{ov_last}", FormulaRule(formula=[f'$L{head_row + 1}="是"'], fill=ALERT_FILL))
    for c, wdt in zip("ABCDEFGHIJKL", (36, 16, 7, 15, 15, 12, 15, 12, 12, 12, 12, 10)):
        ov.column_dimensions[c].width = wdt
    ov.freeze_panes = f"A{head_row + 1}"
    _font_all(ov)
    ov["A1"].font = Font(name=FONT, bold=True, size=14)
    ov["B2"].font = Font(name=FONT, bold=True, color="0000FF", size=11)

    # trend chart: paid GMV and redeemed GMV by week
    ch = LineChart()
    ch.title, ch.height, ch.width = "支付GMV 和核销GMV（按周，元）", 8.5, 24
    ch.add_data(Reference(ws, min_col=col["gmv"], min_row=1, max_row=last_row), titles_from_data=True)
    ch.add_data(Reference(ws, min_col=col["redeem_gmv"], min_row=1, max_row=last_row), titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=1, min_row=2, max_row=last_row))
    for s in ch.series:
        s.smooth = False
    ch.y_axis.number_format = "#,##0"
    ch.y_axis.title, ch.x_axis.title = "元", "周"
    ch.roundedCorners = False
    ch.legend.overlay = False
    ch.title.overlay = False
    ch.x_axis.title.overlay = False
    ch.y_axis.title.overlay = False
    ch.x_axis.delete = False
    ch.y_axis.delete = False
    ov.add_chart(ch, f"A{ov_last + 3}")

    # ------------------------------------------------------------------ 拆解
    de = wb.create_sheet("拆解", 2)
    de["A1"] = "拆解：本周和上周比"
    de["A1"].font = Font(name=FONT, bold=True, size=14)
    de["A2"], de["B2"], de["C2"], de["D2"] = "分析周", "=总览!$B$2", "上周", "=总览!$B$3"
    row = 4
    first_block = None
    for dim in ("channel", "city", "category"):
        label = C.DIMENSIONS[dim]
        de.cell(row=row, column=1, value=f"支付GMV 按{label}").font = Font(name=FONT, bold=True, size=11)
        row += 1
        heads = [label, "本周（元）", "上周（元）", "变化（元）", "变化率", "贡献度", "本周占比", "上周占比"]
        for c, h in enumerate(heads, 1):
            de.cell(row=row, column=c, value=h)
        _style_header(de, row, 1, len(heads))
        top, bottom = row + 1, row + len(members[dim])
        for i, s in enumerate(members[dim]):
            rr = top + i
            de.cell(row=rr, column=1, value=s)
            de.cell(row=rr, column=2, value=f'=SUMIFS({S("gmv")},{S_WEEK},$B$2,{S_DIM},"{label}",{S_SLICE},$A{rr})').number_format = MONEY
            de.cell(row=rr, column=3, value=f'=SUMIFS({S("gmv")},{S_WEEK},$D$2,{S_DIM},"{label}",{S_SLICE},$A{rr})').number_format = MONEY
            de.cell(row=rr, column=4, value=f"=B{rr}-C{rr}").number_format = MONEY
            de.cell(row=rr, column=5, value=f"=IF(C{rr}=0,0,D{rr}/C{rr})").number_format = "+0.00%;-0.00%"
            de.cell(row=rr, column=6, value=f"=IF(SUM($D${top}:$D${bottom})=0,0,D{rr}/SUM($D${top}:$D${bottom}))").number_format = "0.00%"
            de.cell(row=rr, column=7, value=f"=B{rr}/SUM($B${top}:$B${bottom})").number_format = "0.00%"
            de.cell(row=rr, column=8, value=f"=C{rr}/SUM($C${top}:$C${bottom})").number_format = "0.00%"
        tot = bottom + 1
        de.cell(row=tot, column=1, value="合计").font = Font(name=FONT, bold=True, size=10)
        for c in (2, 3, 4):
            de.cell(row=tot, column=c, value=f"=SUM({L(c)}{top}:{L(c)}{bottom})").number_format = MONEY
        de.cell(row=tot, column=5, value=f"=IF(C{tot}=0,0,D{tot}/C{tot})").number_format = "+0.00%;-0.00%"
        de.cell(row=tot, column=6, value=f"=SUM(F{top}:F{bottom})").number_format = "0.00%"
        for rr in range(top, tot + 1):
            for c in range(1, len(heads) + 1):
                de.cell(row=rr, column=c).border = BOX
        if first_block is None:
            first_block = (top, bottom)
        row = tot + 3

    # rate effect and mix effect of the redemption rate, by category
    de.cell(row=row, column=1, value="核销率 按品类：率效应和结构效应").font = Font(name=FONT, bold=True, size=11)
    row += 1
    heads = ["品类", "本周支付GMV", "上周支付GMV", "本周核销GMV", "上周核销GMV", "本周核销率", "上周核销率", "本周占比", "上周占比",
             "率效应(pp)", "结构效应(pp)", "合计贡献(pp)"]
    for c, h in enumerate(heads, 1):
        de.cell(row=row, column=c, value=h)
    _style_header(de, row, 1, len(heads))
    top, bottom = row + 1, row + len(members["category"])
    tot = bottom + 1
    for i, s in enumerate(members["category"]):
        rr = top + i
        de.cell(row=rr, column=1, value=s)
        for c, (meas, wk_cell) in zip((2, 3, 4, 5), (("gmv", "$B$2"), ("gmv", "$D$2"), ("redeem_gmv", "$B$2"), ("redeem_gmv", "$D$2"))):
            de.cell(row=rr, column=c, value=f'=SUMIFS({S(meas)},{S_WEEK},{wk_cell},{S_DIM},"品类",{S_SLICE},$A{rr})').number_format = MONEY
        de.cell(row=rr, column=6, value=f"=IF(B{rr}=0,0,D{rr}/B{rr})").number_format = PCT
        de.cell(row=rr, column=7, value=f"=IF(C{rr}=0,0,E{rr}/C{rr})").number_format = PCT
        de.cell(row=rr, column=8, value=f"=B{rr}/$B${tot}").number_format = PCT
        de.cell(row=rr, column=9, value=f"=C{rr}/$C${tot}").number_format = PCT
        de.cell(row=rr, column=10, value=f"=(H{rr}+I{rr})/2*(F{rr}-G{rr})*100").number_format = "+0.00;-0.00"
        de.cell(row=rr, column=11, value=f"=(H{rr}-I{rr})*((F{rr}+G{rr})/2-($F${tot}+$G${tot})/2)*100").number_format = "+0.00;-0.00"
        de.cell(row=rr, column=12, value=f"=J{rr}+K{rr}").number_format = "+0.00;-0.00"
    de.cell(row=tot, column=1, value="合计").font = Font(name=FONT, bold=True, size=10)
    for c in (2, 3, 4, 5):
        de.cell(row=tot, column=c, value=f"=SUM({L(c)}{top}:{L(c)}{bottom})").number_format = MONEY
    de.cell(row=tot, column=6, value=f"=D{tot}/B{tot}").number_format = PCT
    de.cell(row=tot, column=7, value=f"=E{tot}/C{tot}").number_format = PCT
    for c in (10, 11, 12):
        de.cell(row=tot, column=c, value=f"=SUM({L(c)}{top}:{L(c)}{bottom})").number_format = "+0.00;-0.00"
    de.cell(row=tot + 1, column=1, value="核对：各品类合计贡献之和 − 核销率总变化（应为 0）")
    de.cell(row=tot + 1, column=12, value=f"=ROUND(L{tot}-(F{tot}-G{tot})*100,6)").number_format = "0.000000"
    for rr in range(top, tot + 1):
        for c in range(1, len(heads) + 1):
            de.cell(row=rr, column=c).border = BOX
    for c in range(1, 13):
        de.column_dimensions[L(c)].width = 15
    _font_all(de)
    de["A1"].font = Font(name=FONT, bold=True, size=14)

    bar = BarChart()
    bar.type, bar.title, bar.height, bar.width = "col", "支付GMV 较上周的变化：按渠道（元）", 7.5, 14
    bar.add_data(Reference(de, min_col=4, min_row=first_block[0] - 1, max_row=first_block[1]), titles_from_data=True)
    bar.set_categories(Reference(de, min_col=1, min_row=first_block[0], max_row=first_block[1]))
    bar.varyColors = False
    for s in bar.series:
        s.invertIfNegative = False
        s.graphicalProperties.solidFill = "58554F"
        s.graphicalProperties.line.solidFill = "58554F"
    bar.x_axis.tickLblPos = "low"
    bar.y_axis.number_format = "#,##0"
    bar.roundedCorners = False
    bar.title.overlay = False
    bar.legend = None
    bar.x_axis.delete = False
    bar.y_axis.delete = False
    de.add_chart(bar, "N4")

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def verify(con, week: str, path: Path) -> dict:
    """Compare a recalculated workbook (formulas computed by Excel) with the SQL side.

    Returns {"cells": compared, "mismatches": [...], "errors": [...]}. Run it after Excel has
    recalculated and saved the file; openpyxl only reads the values Excel stored.
    """
    from openpyxl import load_workbook

    from . import monitor
    wb = load_workbook(path, data_only=True)
    out = {"cells": 0, "mismatches": [], "errors": []}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("#"):
                    out["errors"].append(f"{ws.title}!{c.coordinate} {c.value}")

    def same(where, got, want, tol):
        out["cells"] += 1
        if got is None or abs(float(got) - float(want)) > tol:
            out["mismatches"].append(f"{where}: Excel {got} vs SQL {want}")

    ov = wb["总览"]
    scan = monitor.scan(con, week)
    total = scan[scan["dim"] == "total"].set_index("label")
    for r in range(9, 9 + len(M.catalog()["metrics"])):
        label, unit = ov.cell(row=r, column=2).value, ov.cell(row=r, column=3).value
        t = total.loc[label]
        k = 100.0 if unit == "%" else 1.0
        same(f"总览 {label} 本周", ov.cell(row=r, column=4).value * k, t["this_week"], 0.02)
        same(f"总览 {label} 基线中位数", ov.cell(row=r, column=7).value * k, t["base_median"], 0.02)
        if ov.cell(row=r, column=12).value != "不判定":
            same(f"总览 {label} 修正Z", ov.cell(row=r, column=10).value, t["robust_z"], 0.02)
            out["cells"] += 1
            if (ov.cell(row=r, column=12).value == "是") != bool(t["is_alert"]):
                out["mismatches"].append(f"总览 {label} 是否异动: Excel {ov.cell(row=r, column=12).value} vs SQL {bool(t['is_alert'])}")
    de = wb["拆解"]
    gmv = D.breakdown(con, "gmv", week, "channel", compare="last_week").set_index("slice")
    rate = D.breakdown(con, "redeem_rate", week, "category", compare="last_week").set_index("slice")
    for row in de.iter_rows(min_row=4):
        name = row[0].value
        if name in gmv.index and isinstance(row[5].value, (int, float)) and row[8].value is None:
            same(f"拆解 GMV 渠道 {name} 贡献度", row[5].value * 100, gmv.loc[name, "contribution_pct"], 0.01)
        if name in rate.index and len(row) >= 12 and isinstance(row[9].value, (int, float)):
            same(f"拆解 核销率 {name} 率效应", row[9].value, rate.loc[name, "rate_effect"], 0.005)
            same(f"拆解 核销率 {name} 结构效应", row[10].value, rate.loc[name, "mix_effect"], 0.005)
    return out


_ABS_PATH = re.compile(r"<mc:AlternateContent\b[^>]*>(?:(?!</mc:AlternateContent>).)*?x15ac:absPath(?:(?!</mc:AlternateContent>).)*</mc:AlternateContent>", re.S)
_PEOPLE = re.compile(r"<(dc:creator|cp:lastModifiedBy)>[^<]*</\1>")
_APP_FIELDS = re.compile(r"<(Company|Manager)>[^<]*</\1>")


def scrub(path: Path) -> list[str]:
    """Remove what Excel adds when it saves the file: the folder it was saved from (x15ac:absPath in
    xl/workbook.xml), the Windows account name (cp:lastModifiedBy), and company/manager fields.
    Rewrites the file in place and returns what was removed (names only, not the values)."""
    removed, parts = [], []
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            data = z.read(info.filename)
            if info.filename == "xl/workbook.xml":
                text, n = _ABS_PATH.subn("", data.decode("utf-8"))
                if n:
                    removed.append("xl/workbook.xml: saved-from folder")
                data = text.encode("utf-8")
            elif info.filename == "docProps/core.xml":
                text = data.decode("utf-8")
                new = _PEOPLE.sub(lambda m: f"<{m.group(1)}></{m.group(1)}>", text)
                if new != text:
                    removed.append("docProps/core.xml: author fields")
                data = new.encode("utf-8")
            elif info.filename == "docProps/app.xml":
                text = data.decode("utf-8")
                new = _APP_FIELDS.sub(lambda m: f"<{m.group(1)}></{m.group(1)}>", text)
                if new != text:
                    removed.append("docProps/app.xml: company fields")
                data = new.encode("utf-8")
            parts.append((info, data))
    tmp = path.with_suffix(".tmp.xlsx")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        for info, data in parts:
            out.writestr(info, data)
    tmp.replace(path)
    return removed


def leaks(path: Path) -> list[str]:
    """What a published Office file would reveal: a saved-from folder, people's names, external links."""
    found = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if "externalLink" in name:
                found.append(f"{name}: external link")
            if not name.endswith((".xml", ".rels")):
                continue
            text = z.read(name).decode("utf-8", "replace")
            if "absPath" in text:
                found.append(f"{name}: saved-from folder")
            for tag, value in re.findall(r"<(cp:lastModifiedBy|dc:creator|Company|Manager)>([^<]*)<", text):
                if value and value != "openpyxl":
                    found.append(f"{name}: {tag} is set")
            if re.search(r'Target="(?:file:|[A-Za-z]:\\|https?://d\.docs\.live\.net)', text):
                found.append(f"{name}: link to a local or cloud-drive file")
    return found
