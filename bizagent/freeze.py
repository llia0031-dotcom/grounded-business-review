"""Fingerprints of the files a pre-registered run depends on.

  python -m bizagent.freeze            write eval/frozen.json and print the table
  python -m bizagent.freeze --check    compare the files with eval/frozen.json (exit code 1 if any changed)

A fingerprint is the first 12 hex digits of the sha256 of the file with line endings normalised to LF.
"""
from __future__ import annotations

import hashlib
import json
import sys

from . import config as C

FILES = [
    "bizagent/config.py", "bizagent/world.py", "bizagent/db.py", "bizagent/metrics.py", "bizagent/monitor.py",
    "bizagent/decompose.py", "bizagent/tools.py", "bizagent/evidence.py", "bizagent/baseline.py", "bizagent/prompts.py",
    "bizagent/arms.py", "bizagent/llm.py", "bizagent/scenarios.py", "bizagent/score.py",
    "metrics/metrics.yaml", "sql/monitor/weekly_alerts.sql",
    "sql/build/00_dim_week.sql", "sql/build/01_order_wide.sql", "sql/build/02_cube_traffic.sql",
    "sql/build/03_cube_trade.sql", "sql/build/04_cube_store.sql",
    "vendor/evidence-check/check_evidence.py", "eval/cases_test.yaml",
]
OUT = C.ROOT / "eval" / "frozen.json"


def fingerprint(rel: str) -> str:
    data = (C.ROOT / rel).read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()[:12]


def main() -> int:
    now = {f: fingerprint(f) for f in FILES}
    if "--check" in sys.argv:
        frozen = json.loads(OUT.read_text(encoding="utf-8"))
        changed = [f for f in FILES if frozen.get(f) != now[f]]
        for f in changed:
            print(f"CHANGED {f}: frozen {frozen.get(f)} now {now[f]}")
        print(f"{len(FILES) - len(changed)} of {len(FILES)} files unchanged")
        return 1 if changed else 0
    OUT.write_text(json.dumps(now, indent=1), encoding="utf-8", newline="\n")
    print("| File | sha256 |\n|---|---|")
    for f, h in now.items():
        print(f"| `{f}` | `{h}` |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
