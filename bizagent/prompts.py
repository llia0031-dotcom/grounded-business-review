"""Prompts, tool definitions and the answer schema shared by the two model arms."""
from __future__ import annotations

from . import config as C
from . import metrics as M

PROMPT_VERSION = "v4"


def _metric_table() -> str:
    cat = M.catalog()
    ms = cat["measures"]
    lines = ["| name | 指标 | 分组 | 算法 | 可拆的维度 |", "|---|---|---|---|---|"]
    for m in cat["metrics"]:
        if m["type"] == "ratio":
            how = f"{ms[m['numerator']]['label']} ÷ {ms[m['denominator']]['label']}"
        elif m["type"] == "distinct":
            how = "去重计数（各分组不能相加）"
        else:
            how = "可按分组相加"
        lines.append(f"| {m['name']} | {m['label']}（{m['unit']}） | {m['group']} | {how} | {'、'.join(M.dims_of(m))} |")
    return "\n".join(lines)


SCHEMA_NOTES = """周汇总表（week_id 形如 2026-W37）：
- cube_traffic(week_id, city, category, channel, visits)
- cube_trade(week_id, city, category, channel, merchant_type, merchant, orders, gmv, subsidy, new_orders, redeem_orders, redeem_gmv, commission, refund_orders, refund_amt)
- cube_store(week_id, city, category, merchant_type, merchant, online_stores, active_stores, new_stores)
- metric_weekly(metric, label, grp, unit, dim, slice, week_id, num, den, value, share)：每个指标每周的总量（dim='total'）和按城市、品类、渠道、商家类型的分组值
- dim_week(week_id, week_start, week_end, is_holiday_week, holiday_names, in_report)
明细表：
- order_wide(order_id, user_id, deal_id, store_id, merchant, merchant_type, city, city_tier, category, industry, commission_rate, channel, pay_date, pay_week, order_amount, platform_subsidy, is_first_order)
- fact_redeem(order_id, redeem_date, redeem_amount)、fact_refund(order_id, refund_date, refund_amount, refund_type)：refund_type 是“主动退”或“过期退”
- fact_traffic_daily(visit_date, city_id, category_id, channel, visit_cnt)
- dim_store(store_id, merchant_id, city_id, category_id, open_date)、fact_store_offline(store_id, offline_from, offline_to)
- dim_date(date, week_id, week_start, day_of_week, is_holiday, holiday_name, is_holiday_week)
入账时点：支付按支付日，核销按核销日，退款按退款日。"""


def system_prompt(with_tools: bool) -> str:
    tools = """
## 工具

- overview：本周全部指标对比上周和基线，加上触发异动的分组。每次分析先调它。
- breakdown：把一个指标的变化按一个维度拆开，可以用 filters 限定在某个分组里面再拆（下钻）。
- funnel：给一个分组做体检，一次看它的访问、转化、订单、GMV、门店、核销、退款。
- run_sql：自己写一条 SELECT 查上面的表。前三个工具查不到的才用它。

每次调用的结果都会编号（q01、q02……），报告里引用数字时要用这个编号。可以一次调多个工具。
""" if with_tools else """
## 你拿到的材料

用户消息里是一组已经查好的结果，每个都有编号（q01、q02……）：本周指标总览、触发异动的分组、主要异动指标按各维度的拆解、异动分组的体检。你不能再查别的数据，只能用这些材料。
"""
    how = """1. 先看总览和触发异动的分组。没有任何异动，就如实写“本周没有异动”，不要硬找原因。
2. 对每个异动，沿着漏斗从上游往下游找第一个发生变化的环节：门店是否下线 → 访问次数 → 访问支付转化率 → 退款金额 → 核销GMV。上游变了，下游的指标自然跟着变，那些不是另外的原因。
3. 用拆解把异动定位到分组。一个分组的变化如果几乎都来自它里面更细的一组，就定位到更细的那一组；如果分组里各处都在变，就停在这个分组。
4. 确定一个原因以后，回头看其他触发异动的分组：它的变化如果主要来自已经确定的原因（例如某个城市的下滑几乎都在出问题的那个渠道里），它就是同一个原因的表现，不是新原因。去掉已确定原因的影响后仍然明显异常的分组，才另列一个原因。
5. 本周含法定节假日时，整体上涨属于节假日效应。节假日之后的一两周，节前和节日期间支付的订单会陆续到店核销或过期退款，核销和退款金额随之偏高，这也属于节假日的余波。"""
    return f"""你是到店团购业务的经营分析师。任务：写本周的经营分析周报，说清楚本周哪些指标异动、原因是什么、建议做什么。

## 业务和数据

用户在平台上买团购券（支付），到店使用（核销），没用的可以退（退款）。平台在核销后收佣金。

维度：城市 city、品类 category、渠道 channel（短视频、直播、搜索、门店页）、商家类型 merchant_type（连锁、单店）、商家 merchant。

{SCHEMA_NOTES}

## 指标

{_metric_table()}

读指标时要注意：
- 核销率、退款率 = 当周核销（退款）金额 ÷ 当周支付GMV，分子分母不是同一批订单。某个分组的支付量突然变了，这两个率会被机械地带动，这不代表核销或退款本身出了问题。判断核销、退款有没有问题，看核销GMV、退款金额本身有没有变，同时看支付GMV 有没有变。
- 比率指标的变化分成两部分：率效应（分组自己的率变了）和结构效应（分组的占比变了）。客单价、货币化率主要随品类占比变化。
- 基线是本周之前最近 8 个非节假日周。异动由监控规则判定（总览里的“是否异动”），不要自己另定标准。
{tools}
## 怎么分析

{how}

## 原因清单（causes）

每个原因是：一个分组（segment，用一到两个维度写）、一个环节（lever）、一个方向（direction：up 或 down）。

lever 只能取这几个值：
- traffic（流量）：该分组的访问次数变了，转化率基本没变。
- conversion（转化）：访问次数基本没变，访问支付转化率变了，常伴随补贴率变化。
- supply（供给）：门店下线，在线门店数下降，该商家的订单消失。
- redemption（核销）：支付基本正常，核销GMV 本身下降。
- refund（退款）：支付基本正常，退款金额本身上升。
- holiday（节假日）：本周含法定节假日带来的整体变化，segment 留空。

规则：
- 一个原因只写一次。同一个原因会带动多个指标（流量降，订单、GMV、核销GMV 都跟着降），那是一个原因。
- 分组写到刚好：照“怎么分析”第 3 条，不要写得过粗，也不要把一个大分组拆成它下面的每个小组。
- 没有异动，或者找不到集中的分组和环节，causes 留空。证据不够的不要列为原因，可以在 note 里写“值得继续观察”。

## 报告正文（blocks）

报告由若干段组成，每段一两句话，简体中文，大白话。kind 取值：summary（摘要）、finding（发现）、cause（原因）、advice（建议）、note（说明口径或局限）。

每一段的要求：
- finding（发现）必须写出具体数字：本周是多少、对比期是多少、变了多少。只说“明显下降”不算发现。
- cause（原因）必须引用支持它的那一行查询结果。
- 本周有异动时，至少要有一段 finding。

数字规则是硬性的。程序会逐段核对，有一段对不上，整份报告就不放行：
1. 一段话里出现的每个数字，都必须出现在这段话引用的行里。
2. 引用写在 evidence 里：source 是查询编号（如 q03），quote 是从那个查询结果里原样复制的一整行（表格里的一整行，或表格上方的一整行说明）。不要改动，不要把几行拼成一行。一段话可以引用多行。
3. 数字照抄。可以四舍五入，但至少保留两位有效数字。不要自己做加减乘除，不要自己算差值、倍数、占比；需要的数必须是查询结果里现成的。
4. 计数、序号和中文数字也算数字：“两个渠道”“这三项”“两者”“第一”“8 个指标”“一周内”“前一周”“一两周”都会被核对。引用的行里没有这个数，就换个说法（“这些”“它们”“都”“首先”“本周内”“上周”“近期”）。“基线是前 8 周”“阈值是 3.5”这类说法，要引用写着这句话的那一行说明。
5. 没有引用的段落里不能有任何数字。建议里不要写数字。
6. 说到本周就写“本周”或查询结果里的周编号；百分点写 pp。

最后只输出规定格式的 JSON。"""


USER_AGENT = "请写 {week} 这一周的经营分析周报。"
USER_ONESHOT = "请根据下面的材料，写 {week} 这一周的经营分析周报。\n\n{pack}"
RETRY = ("这份报告没有通过数字核对，问题如下：\n{feedback}\n\n"
         "请只修改有问题的段落。数字不在引用行里的：补上写有这个数的那一行引用，或者把数改成引用行里的写法。"
         "计数词和序号（如“两个”“第一”）换成不带数的说法。不要靠删掉关键数字来通过核对，“发现”段必须保留具体数字。"
         "然后重新输出完整的 JSON。原因清单如果没有问题就保持不变。")

_SEGMENT = {
    "type": "array",
    "description": "分组条件，每项一个维度；留空表示全部。",
    "items": {"type": "object", "properties": {"dimension": {"type": "string", "enum": list(C.DIMENSIONS)}, "value": {"type": "string"}},
              "required": ["dimension", "value"], "additionalProperties": False},
}

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "has_anomaly": {"type": "boolean", "description": "本周有没有需要解释的异动"},
        "causes": {"type": "array", "items": {"type": "object", "properties": {
            "segment": _SEGMENT,
            "lever": {"type": "string", "enum": list(C.LEVERS)},
            "direction": {"type": "string", "enum": ["up", "down"]},
        }, "required": ["segment", "lever", "direction"], "additionalProperties": False}},
        "blocks": {"type": "array", "items": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["summary", "finding", "cause", "advice", "note"]},
            "text": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "object", "properties": {
                "source": {"type": "string"}, "quote": {"type": "string"}}, "required": ["source", "quote"], "additionalProperties": False}},
        }, "required": ["kind", "text", "evidence"], "additionalProperties": False}},
    },
    "required": ["has_anomaly", "causes", "blocks"],
    "additionalProperties": False,
}


def tools() -> list[dict]:
    names = [m["name"] for m in M.catalog()["metrics"]]
    compare = {"type": "string", "enum": ["baseline", "last_week"], "description": "和谁比：baseline（前 8 个非节假日周）或 last_week（上周）"}
    return [
        {"name": "overview", "strict": True,
         "description": "本周全部指标（总量）对比上周和基线，以及触发异动的分组。每次分析先调用它。",
         "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
        {"name": "breakdown", "strict": True,
         "description": "把一个指标本周相对对比期的变化，按一个维度拆到每个分组：加法指标给贡献度，比率指标给率效应和结构效应。"
                        "用 filters 可以先限定在某个分组里再拆，用来下钻。指标不支持的维度会报错。",
         "input_schema": {"type": "object", "properties": {
             "metric": {"type": "string", "enum": names}, "dimension": {"type": "string", "enum": list(C.DIMENSIONS)},
             "filters": _SEGMENT, "compare": compare},
             "required": ["metric", "dimension", "filters", "compare"], "additionalProperties": False}},
        {"name": "funnel", "strict": True,
         "description": "一个分组的体检表：访问次数、转化率、订单、GMV、客单价、补贴率、在线和动销门店、核销GMV、核销率、退款金额、退款率，本周对比对比期。"
                        "filters 留空就是全部业务。",
         "input_schema": {"type": "object", "properties": {"filters": _SEGMENT, "compare": compare},
                          "required": ["filters", "compare"], "additionalProperties": False}},
        {"name": "run_sql", "strict": True,
         "description": "对数据库执行一条只读的 SELECT（DuckDB 语法），最多显示 60 行。只在前三个工具查不到时使用。",
         "input_schema": {"type": "object", "properties": {
             "sql": {"type": "string"}, "purpose": {"type": "string", "description": "这条查询要看什么，一句话"}},
             "required": ["sql", "purpose"], "additionalProperties": False}},
    ]
