"""Score an analysis against a case's answer. Rules only, no model.

A named segment counts as the planted one when the two cover the same orders: of the orders in the
planted segment during the baseline weeks, at least 80% are in the named segment (coverage), and of
the orders in the named segment at least 80% are in the planted one (purity). So "火锅连锁A" and
"品类=火锅、商家=火锅连锁A" are the same answer, and "品类=火锅" for a chain with a quarter of
the category is only a coarser one. The 0.8 / 0.8 rule follows MetricLens's equivalent-answer rule.
"""
from __future__ import annotations

from . import config as C
from . import decompose as D
from .evidence import clean_segment

SAME = 0.80


def _pred(seg: dict) -> str:
    conds = [f"{d} = '{str(v).replace(chr(39), chr(39) * 2)}'" for d, v in seg.items()]
    return " AND ".join(conds) if conds else "TRUE"


def overlap(con, week: str, named: dict, planted: dict) -> tuple[float, float]:
    """(coverage, purity) of a named segment against a planted one, by orders in the baseline weeks."""
    weeks = ", ".join(repr(w) for w in D.baseline_weeks(con, week))
    n_both, n_named, n_planted = con.execute(
        f"""SELECT COUNT(*) FILTER (WHERE ({_pred(named)}) AND ({_pred(planted)})),
                   COUNT(*) FILTER (WHERE {_pred(named)}), COUNT(*) FILTER (WHERE {_pred(planted)})
            FROM order_wide WHERE pay_week IN ({weeks})""").fetchone()
    return (n_both / n_planted if n_planted else 0.0), (n_both / n_named if n_named else 0.0)


def relation(con, week: str, named: dict, planted: dict) -> str:
    """same | coarser (contains the planted segment but is much wider) | other."""
    cov, pur = overlap(con, week, named, planted)
    if cov >= SAME and pur >= SAME:
        return "same"
    return "coarser" if cov >= SAME else "other"


def after_holiday(con, week: str) -> bool:
    """True when `week` has no public holiday itself but one of the two weeks before it has."""
    rows = con.execute("SELECT week_id, is_holiday_week FROM dim_week WHERE week_id <= ? ORDER BY week_id DESC LIMIT 3", [week]).fetchall()
    return bool(rows) and not rows[0][1] and any(r[1] for r in rows[1:])


def score(con, case: dict, result: dict) -> dict:
    """Compare result['causes'] with case['truth']['causes']."""
    week = case["week"]
    truth = case["truth"]["causes"]
    named = [{"segment": clean_segment(c.get("segment")), "lever": c.get("lever")} for c in (result.get("causes") or []) if isinstance(c, dict)]
    used: set[int] = set()
    events = []
    for t in truth:
        best = None
        for i, p in enumerate(named):
            if i in used:
                continue
            if t["lever"] == "holiday":
                rel = "same" if p["lever"] == "holiday" else "other"
            elif p["lever"] == "holiday":
                rel = "other"
            else:
                rel = relation(con, week, p["segment"], t["segment"])
            if rel == "same" or (rel == "coarser" and best is None):
                best = (i, rel)
                if rel == "same":
                    break
        if best and best[1] == "same":
            used.add(best[0])
        events.append({"lever": t["lever"], "segment": t["segment"],
                       "found": best[1] if best else "missed",
                       "lever_named": named[best[0]]["lever"] if best else None,
                       "lever_right": bool(best and named[best[0]]["lever"] == t["lever"])})
    coarse_used = set()
    for e, t in zip(events, truth):  # a coarser answer is not also counted as an extra one
        if e["found"] == "coarser":
            for i, p in enumerate(named):
                if i not in used and i not in coarse_used and p["lever"] != "holiday" and relation(con, week, p["segment"], t["segment"]) == "coarser":
                    coarse_used.add(i)
                    break
    # In the two weeks after a public holiday, vouchers paid over the holiday are still being redeemed
    # or refunded. Naming the holiday there is a fair reading, so it is neither required nor counted as extra.
    aftermath = [i for i, p in enumerate(named) if i not in used and p["lever"] == "holiday"] if after_holiday(con, week) else []
    extra = len(named) - len(used) - len(coarse_used) - len(aftermath)
    located = sum(e["found"] == "same" for e in events)
    all_located = located == len(truth) and extra == 0 and not coarse_used
    return {
        "case": case["id"], "family": case["family"], "week": week,
        "truth_events": len(truth), "named": len(named), "located": located,
        "coarser": sum(e["found"] == "coarser" for e in events), "missed": sum(e["found"] == "missed" for e in events), "extra": extra,
        "holiday_aftermath_named": len(aftermath),
        "detection_right": bool(result.get("has_anomaly")) == bool(truth) or (not truth and bool(aftermath)),
        "located_all": all_located,
        "levers_right": all_located and all(e["lever_right"] for e in events),
        "events": events,
    }
