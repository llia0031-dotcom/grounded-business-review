"""Write the data files the hosted workflow reads, and check its code node against the local monitor.

  python -m bizagent.dify_export

A file holds, for one scenario, the weekly numerator and denominator of every metric, in total and
by city, category, channel and merchant type, for the analysis week and the twelve weeks before it.
The workflow's code node (dify/node_monitor.py) recomputes the monitor from these numbers; this
script runs that same code locally and stops if its alerts differ from sql/monitor/weekly_alerts.sql.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from . import config as C
from . import db, metrics as M, monitor, world

OUT = C.ROOT / "dify" / "data"
KEEP_WEEKS = 13
SCENARIOS = {  # name -> (seed, last week, events): one busy week, one quiet week, one holiday week
    "w37_two_causes": (2026, 37, [{"lever": "traffic", "segment": {"channel": "直播"}, "size": -0.42},
                                  {"lever": "refund", "segment": {"category": "美发美甲"}, "size": 0.2}]),
    "w36_quiet": (2027, 36, []),
    "w40_holiday": (2028, 40, []),
    "w35_city_channel": (2029, 35, [{"lever": "traffic", "segment": {"city": "上海", "channel": "短视频"}, "size": -0.7}]),
}


def export(con, week: str) -> dict:
    weeks = [r[0] for r in con.execute("SELECT week_id FROM dim_week WHERE in_report AND week_id <= ? ORDER BY 1", [week]).fetchall()][-KEEP_WEEKS:]
    hol = [r[0] for r in con.execute("SELECT week_id FROM dim_week WHERE is_holiday_week").fetchall() if r[0] in weeks]
    ms = [{"name": m["name"], "label": m["label"], "group": m["group"], "unit": m["unit"],
           "kind": m["type"], "min_change": float(m["min_change"]), "monitor": bool(m.get("monitor", True)),
           "monitor_slices": bool(m.get("monitor_slices", False))} for m in M.catalog()["metrics"]]
    rows = con.execute(f"""SELECT metric, dim, slice, week_id, ROUND(num, 4), ROUND(den, 4) FROM metric_weekly
                           WHERE week_id IN ({', '.join(repr(w) for w in weeks)}) ORDER BY 1, 2, 3, 4""").fetchall()
    return {"note": "Synthetic data generated from a random seed. Not any platform's business data.",
            "week": week, "weeks": weeks, "holiday_weeks": hol, "metrics": ms, "rows": [list(r) for r in rows]}


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, C.ROOT / "dify" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_check_node() -> Path:
    """dify/node_check.py = evidence-check's script without its command-line entry point + dify/check_tail.py."""
    src = C.CHECK_SCRIPT.read_text(encoding="utf-8")
    body = src[: src.index("def main(argv=None):")].rstrip() + "\n\n\n"
    body = body.replace("import argparse\n", "").replace("import sys\n", "")
    out = C.ROOT / "dify" / "node_check.py"
    out.write_text(body + (C.ROOT / "dify" / "check_tail.py").read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    return out


def write_prompts() -> None:
    """The prompts of the hosted workflow: the one-prompt arm's, with the answer format spelled out."""
    from . import prompts
    fmt = ("\n\n## 输出格式\n\n只输出一个 JSON 对象，不要加解释，不要用代码块包起来。结构：\n"
           '{"has_anomaly": true 或 false,\n'
           ' "causes": [{"segment": [{"dimension": "city|category|channel|merchant_type|merchant", "value": "分组名"}], '
           '"lever": "traffic|conversion|supply|redemption|refund|holiday", "direction": "up|down"}],\n'
           ' "blocks": [{"kind": "summary|finding|cause|advice|note", "text": "一两句话", '
           '"evidence": [{"source": "q01", "quote": "原样复制的一整行"}]}]}\n'
           "\n段落的类别由 kind 表示，text 里不要再写“发现一”“原因二”“1)”这类编号或序号：它们也会被当成数字核对。\n")
    d = C.ROOT / "dify"
    (d / "prompt_system.md").write_text(prompts.system_prompt(False) + fmt, encoding="utf-8", newline="\n")
    (d / "prompt_user.md").write_text("请根据下面的材料，写 {{week}} 这一周的经营分析周报。\n\n{{pack}}\n", encoding="utf-8", newline="\n")
    (d / "prompt_revise.md").write_text(prompts.RETRY.replace("{feedback}", "{{feedback}}") + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    node = _load("node_monitor")
    print("check node:", build_check_node().name)
    check = _load("node_check")
    write_prompts()
    OUT.mkdir(parents=True, exist_ok=True)
    bad = 0
    for name, (seed, last, events) in SCENARIOS.items():
        path = C.DATA / "dify" / f"{name}.duckdb"
        if not path.exists():
            db.build(world.generate(seed, last, events), path)
        con = db.connect(path)
        week = C.week_id(last)
        data = export(con, week)
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        (OUT / f"{name}.json").write_text(text, encoding="utf-8", newline="\n")
        out = node.main(text, week)
        local = monitor.alerts(con, week)
        con.close()
        same = out["alerts"] == len(local)
        bad += not same
        print(f"{name}: {week}, {len(data['rows'])} rows, {len(text) / 1024:.0f} KB, pack {len(out['pack'])} chars, "
              f"alerts node {out['alerts']} / sql {len(local)} {'OK' if same else 'DIFFERENT'}")
        if name == "w37_two_causes":  # the check node on a copied number, a changed number and an empty finding
            ev = json.loads(out["evidence"])
            line = next(l for l in ev["q02"].splitlines() if l.startswith("| 访问次数 |"))
            value = line.split("|")[5].strip()
            ok = {"has_anomaly": True, "causes": [], "blocks": [
                {"kind": "finding", "text": f"直播的访问次数本周是 {value} 次。", "evidence": [{"source": "q02", "quote": line}]}]}
            wrong = json.loads(json.dumps(ok, ensure_ascii=False).replace(f"是 {value} 次", "是 123456 次"))
            empty = {"has_anomaly": True, "causes": [], "blocks": [{"kind": "finding", "text": "直播的访问次数明显下降。", "evidence": []}]}
            got = [check.main(json.dumps(r, ensure_ascii=False), out["evidence"])["released"] for r in (ok, wrong, empty)]
            print("check node self-test (expect [1, 0, 0]):", got)
            bad += got != [1, 0, 0]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
