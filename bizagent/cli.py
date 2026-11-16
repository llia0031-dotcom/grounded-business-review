"""Command line.

  python -m bizagent.cli make-cases dev|test       write eval/cases_<set>.yaml (builds each case's database)
  python -m bizagent.cli build dev|test            (re)build the databases of a case file
  python -m bizagent.cli baseline dev|test         run the no-model script on every case and score it
  python -m bizagent.cli run agent|oneshot dev|test --out NAME [--cases D01,D02] [--repeat 2] [--batch] [--budget 0.2]
                                                   run a model arm (costs money; BIZ_ENV_FILE points at the key file)
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import numpy as np

from . import config as C
from . import baseline, db, report, scenarios, score
from .evidence import check_blocks, save_json

EVAL = C.ROOT / "eval"
SETS = {
    # set: (id prefix, first world seed, seed of the draw, families)
    "dev": ("D", 1000, 11, [
        "traffic_channel", "traffic_city", "traffic_up_category", "conversion_city", "conversion_category",
        "supply_merchant", "redemption_category", "redemption_merchant_type", "refund_category", "refund_merchant",
        "traffic_city_channel", "conversion_city_category", "traffic_channel+refund_category", "none", "holiday", "after_holiday",
    ]),
    "test": ("T", 5000, 23, [
        "traffic_channel", "traffic_channel", "traffic_city", "traffic_category",
        "conversion_city", "conversion_city", "conversion_category",
        "supply_merchant", "supply_merchant", "supply_merchant",
        "redemption_city", "redemption_category", "redemption_merchant_type",
        "refund_category", "refund_city", "refund_merchant", "refund_merchant",
        "traffic_city_channel", "traffic_category_channel", "conversion_city_category", "conversion_city_category",
        "redemption_city_category", "refund_city_category",
        "traffic_up_category", "traffic_up_category", "traffic_up_category",
        "traffic_channel+refund_category", "conversion_city+redemption_category",
        "traffic_up_category+conversion_city", "supply_merchant+traffic_channel",
        "none", "none", "none", "none", "holiday", "after_holiday",
    ]),
}


def cases_file(name: str):
    return EVAL / f"cases_{name}.yaml"


def cmd_make_cases(args):
    prefix, first_seed, rng_seed, families = SETS[args.set]
    t = time.time()
    cases = scenarios.make_set(prefix, families, first_seed, rng_seed)
    scenarios.save(cases, cases_file(args.set))
    print(f"{len(cases)} cases -> {cases_file(args.set)} ({time.time() - t:.0f} s)")


def cmd_build(args):
    for case in scenarios.load(cases_file(args.set)):
        scenarios.build(case, force=args.force)
        print("built", case["id"])


def run_baseline(case: dict, out_dir) -> dict:
    con = db.connect(scenarios.build(case))
    try:
        folder = out_dir / case["id"]
        t = time.perf_counter()
        result = baseline.analyse(con, case["week"], folder / "evidence")
        check = check_blocks(result["blocks"], folder / "evidence", result["has_anomaly"])
        sc = score.score(con, case, result)
        row = {**sc, "arm": "script", "released": check["released"], "blocks_checked": check["checked"],
               "blocks_failed": check["failed"], "seconds": round(time.perf_counter() - t, 2), "cost_usd": 0.0}
        save_json(folder / "result.json", {"result": result, "check": check, "score": row})
        (folder / "report.md").write_text(report.render(result, check, folder / "evidence"), encoding="utf-8", newline="\n")
        return row
    finally:
        con.close()


def cmd_baseline(args):
    out = EVAL / "results" / f"script-{args.set}"
    rows = [run_baseline(c, out) for c in scenarios.load(cases_file(args.set))]
    save_json(out / "scores.json", rows)
    for r in rows:
        mark = "OK " if r["located_all"] else "-- "
        print(mark, r["case"], r["family"], f"located {r['located']}/{r['truth_events']} coarser {r['coarser']} extra {r['extra']}",
              "levers ok" if r["levers_right"] else "levers WRONG" if r["located_all"] else "", "" if r["released"] else "NOT RELEASED")
    n = len(rows)
    print(f"located all: {sum(r['located_all'] for r in rows)}/{n}; levers right: {sum(r['levers_right'] for r in rows)}/{n}; "
          f"released: {sum(r['released'] for r in rows)}/{n}")


def run_model_case(case: dict, arm: str, out_dir, rep: int) -> dict:
    from . import arms
    con = db.connect(scenarios.build(case))
    try:
        folder = out_dir / f"{case['id']}-r{rep}"
        t = time.perf_counter()
        result = arms.ARMS[arm](con, case["week"], folder / "evidence")
        check, first = result.pop("check"), result.pop("first_check")
        sc = score.score(con, case, result)
        row = {**sc, "arm": arm, "rep": rep,
               "released": bool(check and check["released"]), "released_first_try": bool(first and first["released"]),
               "blocks_checked": check["checked"] if check else 0, "blocks_failed": check["failed"] if check else 0,
               "first_blocks_checked": first["checked"] if first else 0, "first_blocks_failed": first["failed"] if first else 0,
               "revisions": result["revisions"], "error": result["error"], "tool_calls": sum(1 for x in result["trace"] if "tool" in x),
               "seconds": round(time.perf_counter() - t, 2), **{k: result["usage"][k] for k in ("calls", "input_tokens", "cache_write_tokens", "cache_read_tokens", "output_tokens", "cost_usd", "pricing")}}
        save_json(folder / "result.json", {"result": result, "check": check, "first_check": first, "score": row})
        if check:
            (folder / "report.md").write_text(report.render(result, check, folder / "evidence"), encoding="utf-8", newline=chr(10))
        return row
    finally:
        con.close()


def cmd_run(args):
    """Run a model arm. Costs money: prints the spend and stops starting new cases at --budget."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from . import llm
    cases = scenarios.load(cases_file(args.set))
    if args.cases:
        want = set(args.cases.split(","))
        cases = [c for c in cases if c["id"] in want]
    for c in cases:
        scenarios.build(c)
    out = EVAL / "results" / args.out
    if out.exists() and not args.resume:
        sys.exit(f"{out} already exists. Use --resume to carry on with it, or pick another --out name.")
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(c, rep) for rep in range(1, args.repeat + 1) for c in cases]
    rows, lock, spent = [], threading.Lock(), [0.0]
    say = lambda *a: print(*a, flush=True)
    pool = None
    if args.batch:
        pool = llm.BatchPool(journal=out / "journal.jsonl", log=say)
        llm.POOL = pool
        pool.start()

    saved_of = lambda job: out / f"{job[0]['id']}-r{job[1]}" / "result.json"
    if pool:  # count every worker before any starts, so the first batch waits for all of them
        for job in jobs:
            if not saved_of(job).exists():
                pool.enter()

    def work(job):
        case, rep = job
        saved = saved_of(job)
        if saved.exists():  # finished in an earlier, interrupted run
            row = json.loads(saved.read_text(encoding="utf-8"))["score"]
            with lock:
                spent[0] += row["cost_usd"]
            return row
        try:
            with lock:
                if spent[0] >= args.budget:
                    return {"case": case["id"], "rep": rep, "arm": args.arm, "error": "budget reached, not run"}
            row = run_model_case(case, args.arm, out, rep)
            with lock:
                spent[0] += row["cost_usd"]
            say("done", case["id"], f"r{rep}", f"${row['cost_usd']:.4f}", "spent so far", f"${spent[0]:.4f}")
            return row
        except Exception as e:
            return {"case": case["id"], "rep": rep, "arm": args.arm, "error": f"{type(e).__name__}: {e}"}
        finally:
            if pool:
                pool.leave()

    t = time.time()
    with ThreadPoolExecutor(max_workers=len(jobs) if args.batch else args.workers) as ex:
        rows = list(ex.map(work, jobs))
    if pool:
        pool.close()
        llm.POOL = None
    save_json(out / "scores.json", rows)
    save_json(out / "run.json", {"arm": args.arm, "set": args.set, "repeat": args.repeat, "batch": bool(args.batch),
                                 "model": llm.MODEL, "effort": llm.EFFORT, "minutes_this_process": round((time.time() - t) / 60, 1),
                                 "cost_usd": round(sum(r.get("cost_usd", 0) for r in rows), 4), "batches": pool.batches if pool else [],
                                 "answers_reused_after_interruption": pool.reused if pool else 0})
    for r in rows:
        if "located_all" not in r:
            print("!! ", r["case"], r.get("error"))
            continue
        print("OK " if r["located_all"] else "-- ", r["case"], f"r{r['rep']}", r["family"],
              f"located {r['located']}/{r['truth_events']} coarser {r['coarser']} extra {r['extra']}",
              "levers ok" if r["levers_right"] else "", "released" if r["released"] else "NOT RELEASED",
              f"(first try {'ok' if r['released_first_try'] else 'failed'})", f"calls {r['calls']} ${r['cost_usd']:.4f}", r["error"] or "")
    done = [r for r in rows if "located_all" in r]
    n = len(done)
    print(f"located all: {sum(r['located_all'] for r in done)}/{n}; levers right: {sum(r['levers_right'] for r in done)}/{n}; "
          f"released: {sum(r['released'] for r in done)}/{n} (first try {sum(r['released_first_try'] for r in done)}/{n}); "
          f"cost ${sum(r['cost_usd'] for r in done):.4f}; {round((time.time() - t) / 60, 1)} min")


DEMO = {"id": "demo", "family": "traffic_channel+refund_category", "seed": 2026, "week": "2026-W37",
        "events": [{"lever": "traffic", "segment": {"channel": "直播"}, "size": -0.42},
                   {"lever": "refund", "segment": {"category": "美发美甲"}, "size": 0.2}],
        "truth": {"causes": [{"segment": {"channel": "直播"}, "lever": "traffic", "direction": "down"},
                             {"segment": {"category": "美发美甲"}, "lever": "refund", "direction": "up"}]}}
OUTPUT = C.ROOT / "output"


def demo_db():
    p = C.DATA / "demo.duckdb"
    if not p.exists():
        from . import world
        db.build(world.generate(DEMO["seed"], int(DEMO["week"][-2:]), DEMO["events"]), p)
    return p


def cmd_demo(args):
    """Build the demo database and the no-model weekly report on it."""
    con = db.connect(demo_db())
    try:
        folder = OUTPUT / "demo-script"
        result = baseline.analyse(con, DEMO["week"], folder / "evidence")
        check = check_blocks(result["blocks"], folder / "evidence", result["has_anomaly"])
        (folder / "report.md").write_text(report.render(result, check, folder / "evidence"), encoding="utf-8", newline=chr(10))
        print("database:", demo_db())
        print("report:", folder / "report.md", "| released:", check["released"], "| causes:", json.dumps(result["causes"], ensure_ascii=False))
    finally:
        con.close()


def cmd_review(args):
    """Write one weekly review. --arm script costs nothing; agent and oneshot call the model."""
    from pathlib import Path

    from . import monitor
    con = db.connect(Path(args.db) if args.db else demo_db())
    try:
        week = args.week or monitor.latest_week(con)
        folder = Path(args.out) if args.out else OUTPUT / f"review-{args.arm}-{week}"
        if args.arm == "script":
            result = baseline.analyse(con, week, folder / "evidence")
            check = check_blocks(result["blocks"], folder / "evidence", result["has_anomaly"])
        else:
            from . import arms
            result = arms.ARMS[args.arm](con, week, folder / "evidence")
            check = result.pop("check")
            print("model calls:", result["usage"]["calls"], "| cost USD:", result["usage"]["cost_usd"], "| revisions:", result["revisions"])
        save_json(folder / "result.json", {"result": result, "check": check})
        (folder / "report.md").write_text(report.render(result, check, folder / "evidence"), encoding="utf-8", newline=chr(10))
        print("report:", folder / "report.md")
        print("released:", bool(check and check["released"]), "| causes:", json.dumps(result["causes"], ensure_ascii=False))
    finally:
        con.close()


def cmd_check(args):
    """Check a report (JSON with a `blocks` list) against a folder of evidence files. Exit code 1 if not released."""
    from pathlib import Path

    from .evidence import feedback
    data = json.loads(Path(args.report).read_text(encoding="utf-8"))
    data = data.get("result", data)
    check = check_blocks(data["blocks"], Path(args.evidence), bool(data.get("has_anomaly")))
    print(f"released: {check['released']}; {check['checked']} blocks checked, {check['supported']} supported, {check['failed']} failed")
    if not check["released"]:
        print(feedback(check, data["blocks"]))
        sys.exit(1)


def cmd_excel(args):
    """Write the Excel weekly review for the demo database; --verify compares a recalculated file with SQL."""
    from . import excel
    con = db.connect(demo_db())
    try:
        path = OUTPUT / f"经营周报_{args.week}.xlsx"
        if args.verify:
            res = excel.verify(con, args.week, path)
            print(f"compared {res['cells']} cells: {len(res['mismatches'])} mismatches, {len(res['errors'])} formula errors")
            for line in res["mismatches"][:20] + res["errors"][:20]:
                print("  ", line)
            # Excel adds the folder it saved from and the account name when it recalculates; take them out
            print("removed:", "; ".join(excel.scrub(path)) or "nothing")
            left = excel.leaks(path)
            for line in left:
                print("  still in file:", line)
            sys.exit(1 if res["mismatches"] or res["errors"] or left else 0)
        print("written:", excel.make_weekly(con, args.week, path))
    finally:
        con.close()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bizagent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("make-cases"); p.add_argument("set", choices=list(SETS)); p.set_defaults(fn=cmd_make_cases)
    p = sub.add_parser("build"); p.add_argument("set", choices=list(SETS)); p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_build)
    p = sub.add_parser("baseline"); p.add_argument("set", choices=list(SETS)); p.set_defaults(fn=cmd_baseline)
    p = sub.add_parser("run"); p.add_argument("arm", choices=["agent", "oneshot"]); p.add_argument("set", choices=list(SETS))
    p.add_argument("--cases", default=""); p.add_argument("--repeat", type=int, default=1); p.add_argument("--batch", action="store_true")
    p.add_argument("--workers", type=int, default=4); p.add_argument("--budget", type=float, default=0.2); p.add_argument("--out", required=True)
    p.add_argument("--resume", action="store_true", help="carry on with an interrupted run of the same --out")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("demo"); p.set_defaults(fn=cmd_demo)
    p = sub.add_parser("review"); p.add_argument("--db", default=""); p.add_argument("--week", default="")
    p.add_argument("--arm", choices=["script", "agent", "oneshot"], default="script"); p.add_argument("--out", default=""); p.set_defaults(fn=cmd_review)
    p = sub.add_parser("check"); p.add_argument("report"); p.add_argument("--evidence", required=True); p.set_defaults(fn=cmd_check)
    p = sub.add_parser("excel"); p.add_argument("--week", default=DEMO["week"]); p.add_argument("--verify", action="store_true"); p.set_defaults(fn=cmd_excel)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
