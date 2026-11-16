"""Summarise scored runs into one table per arm and per group of cases.

  python -m bizagent.summary script=script-test oneshot=oneshot-test agent=agent-test [--out eval/results/summary.json]

Every figure is counted from eval/results/<run>/scores.json. Rates come with a Wilson 95% interval.
A run that failed to produce a scored answer stays in the denominator as a miss.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from . import config as C
from . import scenarios

RESULTS = C.ROOT / "eval" / "results"
GROUPS = [("one segment, one dimension", "一个原因，一个维度"), ("one segment, two dimensions", "一个原因，两个维度"),
          ("two causes", "两个原因"), ("controls", "对照（无事件、节假日、节后）")]


def group_of(family: str) -> str:
    if family in ("none", "holiday", "after_holiday"):
        return "controls"
    if "+" in family:
        return "two causes"
    two = family.endswith(("_city_channel", "_category_channel", "_city_category"))
    return "one segment, two dimensions" if two else "one segment, one dimension"


def wilson(k: int, n: int) -> list[float]:
    if n == 0:
        return [0.0, 0.0]
    z, p = 1.96, k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(100 * max(0.0, centre - half), 1), round(100 * min(1.0, centre + half), 1)]


def rate(rows: list[dict], key: str) -> dict:
    k, n = sum(bool(r.get(key)) for r in rows), len(rows)
    return {"k": k, "n": n, "pct": round(100 * k / n, 1) if n else None, "ci95": wilson(k, n)}


def summarise(rows: list[dict]) -> dict:
    done = [r for r in rows if "located_all" in r]
    out = {"runs": len(rows), "scored": len(done), "failed_to_run": len(rows) - len(done),
           "located_all": rate(rows, "located_all"), "levers_right": rate(rows, "levers_right"),
           "detection_right": rate(rows, "detection_right"), "released": rate(rows, "released"),
           "events": sum(r["truth_events"] for r in done), "events_located": sum(r["located"] for r in done),
           "events_coarser": sum(r["coarser"] for r in done), "events_missed": sum(r["missed"] for r in done),
           "extra_causes": sum(r["extra"] for r in done), "runs_with_extra": sum(r["extra"] > 0 for r in done),
           "cost_usd": round(sum(r.get("cost_usd", 0.0) for r in done), 4),
           "cost_per_run_usd": round(sum(r.get("cost_usd", 0.0) for r in done) / len(done), 5) if done else None,
           "seconds_per_run": round(sum(r.get("seconds", 0.0) for r in done) / len(done), 1) if done else None}
    if done and "released_first_try" in done[0]:
        out["released_first_try"] = rate(rows, "released_first_try")
        out["runs_revised"] = sum(r.get("revisions", 0) > 0 for r in done)
        out["model_calls_per_run"] = round(sum(r["calls"] for r in done) / len(done), 2)
        out["tool_calls_per_run"] = round(sum(r.get("tool_calls", 0) for r in done) / len(done), 2)
        out["blocks_checked_first_try"] = sum(r["first_blocks_checked"] for r in done)
        out["blocks_failed_first_try"] = sum(r["first_blocks_failed"] for r in done)
        out["blocks_checked_final"] = sum(r["blocks_checked"] for r in done)
        out["blocks_failed_final"] = sum(r["blocks_failed"] for r in done)
        out["input_tokens"] = sum(r["input_tokens"] + r["cache_write_tokens"] + r["cache_read_tokens"] for r in done)
        out["output_tokens"] = sum(r["output_tokens"] for r in done)
    return out


def paired(a_rows: list[dict], b_rows: list[dict], key: str = "located_all") -> dict:
    """Case-by-case comparison of two arms: how often only one of them is right, and an exact binomial p."""
    a = {(r["case"], r.get("rep", 1)): bool(r.get(key)) for r in a_rows}
    b = {(r["case"], r.get("rep", 1)): bool(r.get(key)) for r in b_rows}
    keys = sorted(set(a) & set(b))
    only_a, only_b = sum(a[k] and not b[k] for k in keys), sum(b[k] and not a[k] for k in keys)
    n, k = only_a + only_b, min(only_a, only_b)
    p = min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n) if n else 1.0
    return {"pairs": len(keys), "both": sum(a[k] and b[k] for k in keys), "only_first": only_a, "only_second": only_b,
            "neither": sum(not a[k] and not b[k] for k in keys), "p_exact": round(p, 4)}


def main(argv: list[str]) -> int:
    out_path = RESULTS / "summary.json"
    if "--out" in argv:
        i = argv.index("--out")
        out_path = Path(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    arms = dict(a.split("=", 1) for a in argv)
    family = {}
    for f in sorted((C.ROOT / "eval").glob("cases_*.yaml")):
        family.update({c["id"]: c["family"] for c in scenarios.load(f)})
    summary = {"runs": arms, "arms": {}, "by_group": {}, "by_case": {}, "paired": {}}
    all_rows = {}
    for name, run in arms.items():
        rows = json.loads((RESULTS / run / "scores.json").read_text(encoding="utf-8"))
        for r in rows:
            r.setdefault("family", family.get(r["case"], ""))
        all_rows[name] = rows
        summary["arms"][name] = summarise(rows)
        for g, _ in GROUPS:
            summary["by_group"].setdefault(g, {})[name] = summarise([r for r in rows if group_of(r["family"]) == g])
        for r in rows:
            summary["by_case"].setdefault(r["case"], {"family": r["family"]}).setdefault(name, []).append(
                {"located_all": bool(r.get("located_all")), "levers_right": bool(r.get("levers_right")), "extra": r.get("extra"),
                 "coarser": r.get("coarser"), "missed": r.get("missed"), "released": bool(r.get("released")), "error": r.get("error")})
        info = RESULTS / run / "run.json"
        if info.exists():
            summary["arms"][name]["run"] = json.loads(info.read_text(encoding="utf-8"))
    names = list(arms)
    for i, x in enumerate(names):
        for y in names[i + 1:]:
            summary["paired"][f"{x} vs {y}"] = {"located_all": paired(all_rows[x], all_rows[y]),
                                                "levers_right": paired(all_rows[x], all_rows[y], "levers_right")}
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8", newline=chr(10))
    pc = lambda r: f"{r['k']}/{r['n']} ({r['pct']}%, {r['ci95'][0]}–{r['ci95'][1]})"
    print("| | " + " | ".join(arms) + " |")
    print("|---|" + "---|" * len(arms))
    A = summary["arms"]
    for label, f in [("located all", lambda s: pc(s["located_all"])), ("levers right", lambda s: pc(s["levers_right"])),
                     ("events located", lambda s: f"{s['events_located']}/{s['events']}"), ("coarser", lambda s: s["events_coarser"]),
                     ("missed", lambda s: s["events_missed"]), ("extra causes", lambda s: f"{s['extra_causes']} in {s['runs_with_extra']} runs"),
                     ("released", lambda s: pc(s["released"])),
                     ("released first try", lambda s: pc(s["released_first_try"]) if "released_first_try" in s else "—"),
                     ("failed to run", lambda s: s["failed_to_run"]),
                     ("cost per run USD", lambda s: s["cost_per_run_usd"]), ("total cost USD", lambda s: s["cost_usd"])]:
        print(f"| {label} | " + " | ".join(str(f(A[n])) for n in arms) + " |")
    for g, _ in GROUPS:
        print(f"| {g}: located all | " + " | ".join(pc(summary["by_group"][g][n]["located_all"]) for n in arms) + " |")
    for pair, d in summary["paired"].items():
        v = d["located_all"]
        print(f"paired {pair} (located all): both {v['both']}, only first {v['only_first']}, only second {v['only_second']}, "
              f"neither {v['neither']}, p = {v['p_exact']}")
    print("written:", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
