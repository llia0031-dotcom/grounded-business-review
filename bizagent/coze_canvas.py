"""Write the Coze Studio (open-source Coze) workflow canvas from the same node scripts and prompts as the Dify version.

  python -m bizagent.coze_canvas --model-id 1 --model-name claude-haiku-5-5 [--out coze/weekly_review_canvas.json]

The canvas: start (which week) -> code: monitor and break down (downloads the week's numbers from the public
repository) -> model writes the review -> code: number check -> condition: released? -> yes: take the last
version; no: one revision -> code: check again -> take the last version -> end.

Coze Studio passes every code node's code and inputs on a command line, so each hand-over stays well under the
128 KB limit of one argument: the weekly numbers (430 KB) are downloaded inside the first code node instead of
being passed in.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import config as C

DIFY = C.ROOT / "dify"
DATA_BASE = "https://raw.githubusercontent.com/llia0031-dotcom/grounded-business-review/main/dify/data/"
WEEKS = {"37": "w37_two_causes", "36": "w36_quiet", "40": "w40_holiday", "35": "w35_city_channel"}
ICON = "https://lf3-static.bytednsdoc.com/obj/eden-cn/dvsmryvd_avi_dvsm/ljhwZthlaukjlkulzlp/icon/"

MONITOR_MAIN = '''

FILES = %s
LOCAL = "/opt/bizreview-data/"
BASE = "%s"


async def main(args: Args) -> Output:
    import os
    import re
    asked = re.findall(r"\\d+", str(args.params.get("week") or ""))
    name = FILES.get(asked[-1] if asked else "", FILES["37"])
    if os.path.exists(LOCAL + name + ".json"):  # mounted into coze-server, see coze/compose.bizreview-data.yml
        with open(LOCAL + name + ".json", encoding="utf-8") as f:
            return monitor(f.read())
    import httpx
    r = httpx.get(BASE + name + ".json", timeout=60, follow_redirects=True)
    r.raise_for_status()
    return monitor(r.text)
'''

CHECK_MAIN = '''

async def main(args: Args) -> Output:
    return check(args.params.get("report") or "", args.params.get("evidence") or "{}")
'''


def ref(block: str, name: str, kind: str = "string") -> dict:
    raw = {"string": 1, "integer": 2}[kind]
    return {"type": kind, "value": {"type": "ref", "content": {"source": "block-output", "blockID": block, "name": name}, "rawMeta": {"type": raw}}}


def lit(value, kind: str = "string") -> dict:
    raw = {"string": 1, "integer": 2, "float": 4, "boolean": 3}[kind]
    return {"type": kind, "value": {"type": "literal", "content": value, "rawMeta": {"type": raw}}}


def meta(title: str, sub: str, icon: str, desc: str) -> dict:
    return {"title": title, "subTitle": sub, "icon": ICON + icon, "description": desc}


def code_node(nid: str, x: int, y: int, title: str, desc: str, code: str, inputs: dict, outputs: dict) -> dict:
    return {"id": nid, "type": "5", "meta": {"position": {"x": x, "y": y}},
            "data": {"nodeMeta": {**meta(title, "代码", "icon-Code-v2.jpg", desc), "mainColor": "#00B2B2"},
                     "inputs": {"inputParameters": [{"name": k, "input": v} for k, v in inputs.items()], "code": code, "language": 3,
                                "settingOnError": {"processType": 1, "timeoutMs": 120000, "retryTimes": 0}},
                     "outputs": [{"type": t, "name": n} for n, t in outputs.items()]}}


def llm_node(nid: str, x: int, y: int, title: str, model_id: str, model_name: str, system: str, prompt: str, inputs: dict) -> dict:
    params = [("modelType", lit(str(model_id), "integer")), ("modleName", lit(model_name)), ("generationDiversity", lit("balance")),
              ("maxTokens", lit("20000", "integer")), ("responseFormat", lit("0", "integer")),  # no temperature: Claude Haiku 5.5 rejects it; room for its default thinking
              ("prompt", lit(prompt)), ("enableChatHistory", lit(False, "boolean")), ("chatHistoryRound", lit("3", "integer")),
              ("systemPrompt", lit(system))]
    return {"id": nid, "type": "3", "meta": {"position": {"x": x, "y": y}},
            "data": {"nodeMeta": {**meta(title, "大模型", "icon-LLM-v2.jpg", "调用大语言模型写周报"), "mainColor": "#5C62FF"},
                     "inputs": {"inputParameters": [{"name": k, "input": v} for k, v in inputs.items()],
                                "llmParam": [{"name": n, "input": v} for n, v in params],
                                "settingOnError": {"processType": 1, "retryTimes": 1, "timeoutMs": 300000}},
                     "outputs": [{"type": "string", "name": "output"}], "version": "3"}}


def build(model_id: str, model_name: str) -> dict:
    read = lambda name: (DIFY / name).read_text(encoding="utf-8")
    monitor_src = read("node_monitor.py").replace('def main(data: str, week: str = "") -> dict:', 'def monitor(data: str, week: str = "") -> dict:')
    check_src = read("node_check.py").replace("def main(report: str, evidence: str) -> dict:", "def check(report: str, evidence: str) -> dict:")
    check_src = check_src.replace("from __future__ import annotations\n", "")  # the runner puts its own lines before the code
    assert "def monitor(" in monitor_src and "def check(" in check_src and "__future__" not in check_src
    system = read("prompt_system.md").strip()
    user = "请根据下面的材料，写 {{week}} 这一周的经营分析周报。\n\n{{pack}}"
    revise = (user + "\n\n上一次写的周报（JSON）：\n{{draft}}\n\n"
              + read("prompt_revise.md").strip().replace("{{feedback}}", "{{feedback}}"))
    check_out = {"released": "integer", "feedback": "string", "markdown": "string", "checked": "integer", "failed": "integer"}
    nodes = [
        {"id": "100001", "type": "1", "meta": {"position": {"x": 0, "y": 0}},
         "data": {"nodeMeta": meta("开始", "", "icon-Start-v2.jpg", "选一周：37、36、40 或 35"),
                  "outputs": [{"type": "string", "name": "week", "required": False,
                               "description": "37 两个原因同时发生；36 平稳的一周；40 含国庆假期；35 一个城市的一个渠道"}],
                  "trigger_parameters": []}},
        code_node("130001", 400, 0, "监控和拆解", "取这一周和前 12 周的指标，判定异动，按维度拆解",
                  monitor_src + MONITOR_MAIN % (json.dumps(WEEKS, ensure_ascii=False), DATA_BASE), {"week": ref("100001", "week")},
                  {"week": "string", "pack": "string", "evidence": "string", "alerts": "integer"}),
        llm_node("140001", 800, 0, "写周报", model_id, model_name, system, user,
                 {"week": ref("130001", "week"), "pack": ref("130001", "pack")}),
        code_node("150001", 1200, 0, "数字核对", "每段引用的行要在查询结果里，段里的数字要在引用的行里",
                  check_src + CHECK_MAIN, {"report": ref("140001", "output"), "evidence": ref("130001", "evidence")}, check_out),
        {"id": "160001", "type": "8", "meta": {"position": {"x": 1600, "y": 0}},
         "data": {"nodeMeta": {**meta("放行了吗", "选择器", "icon-Condition-v2.jpg", "全部通过就放行"), "mainColor": "#00B2B2"},
                  "inputs": {"branches": [{"condition": {"logic": 2, "conditions": [{
                      "operator": 1, "left": {"input": ref("150001", "released", "integer")},
                      "right": {"input": lit(1, "integer")}}]}}]}}},
        llm_node("170001", 2000, 260, "改一次", model_id, model_name, system, revise,
                 {"week": ref("130001", "week"), "pack": ref("130001", "pack"), "draft": ref("140001", "output"),
                  "feedback": ref("150001", "feedback")}),
        code_node("180001", 2400, 260, "再核对", "改过的周报再核对一次", check_src + CHECK_MAIN,
                  {"report": ref("170001", "output"), "evidence": ref("130001", "evidence")}, check_out),
        {"id": "190001", "type": "32", "meta": {"position": {"x": 2800, "y": 0}},
         "data": {"nodeMeta": meta("取最后一版", "变量聚合", "VariableMerge-icon.jpg", "改过就取改过的那版，没改就取第一版"),
                  "inputs": {"mergeGroups": [{"name": "Group1", "variables": [ref("180001", "markdown"), ref("150001", "markdown")]}]},  # revised version first: Coze takes the first non-empty value
                  "outputs": [{"type": "string", "name": "Group1"}]}},
        {"id": "900001", "type": "2", "meta": {"position": {"x": 3200, "y": 0}},
         "data": {"nodeMeta": meta("结束", "", "icon-End-v2.jpg", "输出周报"),
                  "inputs": {"terminatePlan": "returnVariables",
                             "inputParameters": [{"name": "report", "input": ref("190001", "Group1")}]}}},
    ]
    edge = lambda a, b, port="": {"sourceNodeID": a, "targetNodeID": b, "sourcePortID": port}
    edges = [edge("100001", "130001"), edge("130001", "140001"), edge("140001", "150001"), edge("150001", "160001"),
             edge("160001", "190001", "true"), edge("160001", "170001", "false"), edge("170001", "180001"),
             edge("180001", "190001"), edge("190001", "900001")]
    return {"nodes": nodes, "edges": edges, "versions": {"loop": "v2"}}


def main(argv: list[str]) -> int:
    arg = lambda k, d=None: argv[argv.index(k) + 1] if k in argv else d
    out = Path(arg("--out", str(C.ROOT / "coze" / "weekly_review_canvas.json")))
    canvas = build(arg("--model-id", "1"), arg("--model-name", "claude-haiku-5-5"))
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(canvas, ensure_ascii=False, indent=1)
    out.write_text(text, encoding="utf-8", newline="\n")
    sizes = {n["data"]["nodeMeta"]["title"]: len(n["data"].get("inputs", {}).get("code", "")) for n in canvas["nodes"] if n["type"] == "5"}
    print(f"written: {out} ({len(text.encode('utf-8')) / 1024:.0f} KB); code sizes: {sizes}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
