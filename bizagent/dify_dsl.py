"""Write the Dify workflow file from the node scripts and prompts in dify/.

  python -m bizagent.dify_dsl                                  # dify/weekly_review.yml
  python -m bizagent.dify_dsl --force-revise --out FILE.yml    # test copy: every run takes the revision branch

The workflow: pick one of four scenario weeks -> code node 1 (monitor and split; the scenario's weekly
numbers are stored compressed inside the node, so the app needs no outside data source) -> the model
writes the review -> code node 2 (number check) -> released, or one revision and a second check ->
the last version, marked released or not.
Import the file in Dify with Studio > Create > Import App.
"""
from __future__ import annotations

import base64
import sys
import zlib
from pathlib import Path

import yaml

from . import config as C

DIFY = C.ROOT / "dify"
SCENARIOS = [("第 37 周：两个原因同时发生", "w37_two_causes"), ("第 36 周：平稳的一周", "w36_quiet"),
             ("第 40 周：含国庆假期", "w40_holiday"), ("第 35 周：一个城市的一个渠道", "w35_city_channel")]
MODEL = {"provider": "langgenius/openai/openai", "name": "gpt-5-mini", "mode": "chat", "completion_params": {}}
RETRY = {"enabled": False, "max_retries": 1, "retry_interval": 1000,
         "exponential_backoff": {"enabled": False, "multiplier": 2, "max_interval": 10000}}

EMBED = '''

# ---------------------------------------------------------------------------------------------
# The four scenario weeks (synthetic data, zlib + base64 of dify/data/<name>.json).
# ---------------------------------------------------------------------------------------------
import base64
import zlib

DATA = {
%s
}


def main(scenario: str) -> dict:
    packed = DATA.get(scenario) or next(iter(DATA.values()))
    return monitor(zlib.decompress(base64.b64decode(packed)).decode("utf-8"))
'''


class Literal(str):
    pass


yaml.add_representer(Literal, lambda dumper, data: dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|"))


def node(nid: str, x: int, y: int, height: int, data: dict) -> dict:
    return {"data": {"desc": "", "selected": False, **data}, "height": height, "id": nid, "position": {"x": x, "y": y},
            "positionAbsolute": {"x": x, "y": y}, "selected": False, "sourcePosition": "right", "targetPosition": "left",
            "type": "custom", "width": 244}


def edge(source: str, target: str, types: tuple[str, str], handle: str = "source") -> dict:
    return {"data": {"isInIteration": False, "isInLoop": False, "sourceType": types[0], "targetType": types[1]},
            "id": f"{source}-{handle}-{target}-target", "source": source, "sourceHandle": handle, "target": target,
            "targetHandle": "target", "type": "custom", "zIndex": 0}


def code(title: str, source: str, variables: dict, outputs: dict) -> dict:
    return {"title": title, "type": "code", "code": Literal(source), "code_language": "python3",
            "outputs": {k: {"children": None, "type": t} for k, t in outputs.items()},
            "variables": [{"value_selector": list(sel), "variable": name} for name, sel in variables.items()]}


def llm(title: str, prompt: list[tuple[str, str]]) -> dict:
    return {"title": title, "type": "llm", "model": MODEL,
            "prompt_template": [{"role": role, "text": Literal(text) if "\n" in text else text} for role, text in prompt],
            "vision": {"enabled": False, "configs": {"variable_selector": []}},
            "memory": {"enabled": False, "window": {"enabled": False, "size": 50}},
            "context": {"enabled": False, "variable_selector": []}, "structured_output": {"enabled": False},
            "retry_config": RETRY}


def build(force_revise: bool = False) -> dict:
    read = lambda name: (DIFY / name).read_text(encoding="utf-8")
    monitor_src = read("node_monitor.py").replace('def main(data: str, week: str = "") -> dict:',
                                                  'def monitor(data: str, week: str = "") -> dict:')
    assert "def monitor(" in monitor_src
    packed = ",\n".join(f'    "{label}": "{base64.b64encode(zlib.compress(read(f"data/{name}.json").encode("utf-8"), 9)).decode("ascii")}"'
                        for label, name in SCENARIOS)
    check_src = read("node_check.py")
    system = read("prompt_system.md").strip()
    user = "请根据下面的材料，写 {{#monitor.week#}} 这一周的经营分析周报。\n\n{{#monitor.pack#}}"
    revise = read("prompt_revise.md").strip().replace("{{feedback}}", "{{#check.feedback#}}")
    check_out = {"released": "number", "feedback": "string", "markdown": "string", "checked": "number", "failed": "number"}
    labels = [label for label, _ in SCENARIOS]
    nodes = [
        node("start", 0, 200, 90, {"title": "开始", "type": "start", "variables": [
            {"label": "选一周", "max_length": 48, "options": labels, "required": True, "type": "select", "variable": "scenario",
             "default": labels[0]}]}),
        node("monitor", 260, 200, 54, code("监控和拆解", monitor_src + EMBED % packed, {"scenario": ("start", "scenario")},
                                           {"week": "string", "pack": "string", "evidence": "string", "alerts": "number"})),
        node("write", 520, 200, 90, llm("写周报", [("system", system), ("user", user)])),
        node("check", 780, 200, 54, code("数字核对", check_src, {"report": ("write", "text"), "evidence": ("monitor", "evidence")}, check_out)),
        node("gate", 1040, 200, 126, {"title": "放行了吗", "type": "if-else", "cases": [{
            "case_id": "true", "id": "true", "logical_operator": "and", "conditions": [{
                "comparison_operator": "=", "id": "released-is-1", "value": "2" if force_revise else "1", "varType": "number",
                "variable_selector": ["check", "released"]}]}]}),
        node("revise", 1300, 380, 90, llm("改一次", [("system", system), ("user", user), ("assistant", "{{#write.text#}}"), ("user", revise)])),
        node("check2", 1560, 380, 54, code("再核对", check_src, {"report": ("revise", "text"), "evidence": ("monitor", "evidence")}, check_out)),
        node("merge", 1820, 200, 130, {"title": "取最后一版", "type": "variable-aggregator", "output_type": "string",
                                       "variables": [["check", "markdown"], ["check2", "markdown"]],
                                       "advanced_settings": {"group_enabled": False, "groups": []}}),
        node("end", 2080, 200, 90, {"title": "周报", "type": "end", "outputs": [
            {"value_selector": ["merge", "output"], "value_type": "string", "variable": "report"}]}),
    ]
    edges = [edge("start", "monitor", ("start", "code")), edge("monitor", "write", ("code", "llm")),
             edge("write", "check", ("llm", "code")), edge("check", "gate", ("code", "if-else")),
             edge("gate", "merge", ("if-else", "variable-aggregator"), "true"), edge("gate", "revise", ("if-else", "llm"), "false"),
             edge("revise", "check2", ("llm", "code")), edge("check2", "merge", ("code", "variable-aggregator")),
             edge("merge", "end", ("variable-aggregator", "end"))]
    return {
        "app": {"description": "合成的到店团购数据：每周监控、按维度拆解、写周报，数字逐个核对，对不上不放行。",
                "icon": "📈", "icon_background": "#E4FBCC", "mode": "workflow",
                "name": "有据经营分析周报" + ("（改一次分支测试）" if force_revise else ""), "use_icon_as_answer_icon": False},
        "dependencies": [], "kind": "app", "version": "0.3.1",
        "workflow": {"conversation_variables": [], "environment_variables": [],
                     "features": {"file_upload": {"enabled": False}, "opening_statement": "", "retriever_resource": {"enabled": False},
                                  "sensitive_word_avoidance": {"enabled": False}, "speech_to_text": {"enabled": False},
                                  "suggested_questions": [], "suggested_questions_after_answer": {"enabled": False},
                                  "text_to_speech": {"enabled": False}},
                     "graph": {"edges": edges, "nodes": nodes, "viewport": {"x": 0, "y": 0, "zoom": 0.6}}}}


def main(argv: list[str]) -> int:
    force = "--force-revise" in argv
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else DIFY / "weekly_review.yml"
    text = yaml.dump(build(force), allow_unicode=True, sort_keys=False, width=1000)
    out.write_text(text, encoding="utf-8", newline="\n")
    back = yaml.safe_load(text)
    kinds = [n["data"]["type"] for n in back["workflow"]["graph"]["nodes"]]
    print(f"written: {out}, {len(text.encode('utf-8')) / 1024:.0f} KB, nodes: {', '.join(kinds)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
