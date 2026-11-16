"""False alerts of the weekly monitor on worlds with no event: seeds 101-106, weeks 34-38 (30 world-weeks).

  python -m bizagent.monitor_check [--out eval/results/monitor-false-alerts.json]

These seeds are used by neither the development nor the test cases. With no event planted, every alert is false.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import config as C
from . import db, monitor, world

SEEDS, WEEKS = range(101, 107), range(34, 39)


def main(argv: list[str]) -> int:
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else C.ROOT / "eval" / "results" / "monitor-false-alerts.json"
    rows = []
    for seed in SEEDS:
        path = C.DATA / "monitor-check" / f"seed{seed}.duckdb"
        if not path.exists():
            db.build(world.generate(seed=seed, last_week=max(WEEKS)), path)
        con = db.connect(path)
        try:
            for w in WEEKS:
                scan = monitor.scan(con, C.week_id(w))
                for r in scan[scan["is_alert"]].itertuples():
                    rows.append({"seed": seed, "week": C.week_id(w), "metric": r.label, "dim": r.dim, "slice": r.slice,
                                 "robust_z": float(r.robust_z), "change_vs_base": float(r.change_vs_base)})
        finally:
            con.close()
    total = [r for r in rows if r["dim"] == "total"]
    data = {"note": "Weekly monitor on worlds with no planted event; every alert listed here is a false alert.",
            "seeds": list(SEEDS), "weeks": [C.week_id(w) for w in WEEKS], "world_weeks": len(SEEDS) * len(WEEKS),
            "false_alerts_total": len(total), "false_alerts_segment": len(rows) - len(total), "alerts": rows}
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(f"{data['world_weeks']} world-weeks: {len(total)} total-level, {len(rows) - len(total)} segment-level false alerts -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
