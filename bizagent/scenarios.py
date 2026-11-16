"""Test cases: a world, a week, and the events planted in that week. The answer is fixed when the case is written.

A case is {id, family, seed, week, events, truth}. `truth.causes` is what a correct analysis names:
the segment each event hit and the lever it moved. Control cases have no planted event; a week with a
public holiday has the calendar as its only cause.

A drafted case is kept only if the weekly monitor raises an alert on a metric the event moves
directly (in total, or in a slice that is part of the event's segment). An event the monitor cannot
see is not an anomaly under the monitor's own rule, so it would have no correct answer.
The approach follows RiskLoc's generate_dataset.py (random segments, a severity, an answer file) and
the answer-per-scenario layout of InsightBench.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import config as C
from . import db, monitor, world

NORMAL_WEEKS = [34, 35, 36, 37, 38]      # no public holiday in the week or in its trailing effects
HOLIDAY_WEEK, AFTER_HOLIDAY_WEEK = 40, 42
PRIMARY = {  # metrics an event moves directly
    "traffic": {"visits"}, "conversion": {"conversion_rate", "orders"}, "supply": {"online_stores", "orders"},
    "redemption": {"redeem_gmv", "redeem_rate"}, "refund": {"refund_amt", "refund_rate"},
}
# family -> (lever, dimensions of the segment)
FAMILIES = {
    "traffic_channel": ("traffic", ["channel"]), "traffic_city": ("traffic", ["city"]), "traffic_category": ("traffic", ["category"]),
    "traffic_up_category": ("traffic", ["category"]),
    "conversion_city": ("conversion", ["city"]), "conversion_category": ("conversion", ["category"]),
    "supply_merchant": ("supply", ["merchant"]),
    "redemption_city": ("redemption", ["city"]), "redemption_category": ("redemption", ["category"]),
    "redemption_merchant_type": ("redemption", ["merchant_type"]),
    "refund_category": ("refund", ["category"]), "refund_city": ("refund", ["city"]), "refund_merchant": ("refund", ["merchant"]),
    "traffic_city_channel": ("traffic", ["city", "channel"]), "traffic_category_channel": ("traffic", ["category", "channel"]),
    "conversion_city_category": ("conversion", ["city", "category"]),
    "redemption_city_category": ("redemption", ["city", "category"]), "refund_city_category": ("refund", ["city", "category"]),
}


def shares(seed: int, week: int) -> dict:
    """Order shares by dimension in the eight weeks before `week`, in the world of `seed` with no event."""
    t = world.generate(seed, week - 1)
    o = t["fact_order"].merge(t["dim_store"], on="store_id").merge(t["dim_merchant"], on="merchant_id")
    o = o[o["pay_date"] >= np.datetime64(C.week_start(week - 8))]
    o["city"] = np.array(C.CITY_NAMES)[o["city_id"]]
    o["category"] = np.array(C.CATEGORY_NAMES)[o["category_id"]]
    n = len(o)
    out = {d: (o.groupby(d).size() / n).to_dict() for d in ("city", "category", "channel", "merchant_type")}
    out["gmv_category"] = (o.groupby("category")["order_amount"].sum() / o["order_amount"].sum()).to_dict()
    chains = o[o["merchant_type"] == "连锁"].groupby(["category", "merchant_name"]).size().rename("n").reset_index()
    chains["in_category"] = chains["n"] / chains["category"].map(o.groupby("category").size())
    chains["overall"] = chains["n"] / n
    out["chains"] = chains
    out["cells"] = {k: (o.groupby(list(k)).size() / n) for k in (("city", "channel"), ("category", "channel"), ("city", "category"))}
    return out


def draft_event(family: str, sh: dict, rng: np.random.Generator) -> dict:
    lever, dims = FAMILIES[family]
    pick = lambda options: options[int(rng.integers(len(options)))]
    big = lambda dim, floor: [k for k, v in sh[dim].items() if v >= floor]
    if dims == ["channel"]:
        seg = {"channel": pick(list(sh["channel"]))}
    elif dims == ["city"]:
        seg = {"city": pick(big("city", 0.07))}
    elif dims == ["category"]:
        if family == "traffic_up_category":
            seg = {"category": pick(["茶饮咖啡", "快餐小吃"])}
        elif lever in ("redemption", "refund"):
            seg = {"category": pick([k for k, v in sh["gmv_category"].items() if v >= 0.09])}
        else:
            seg = {"category": pick(big("category", 0.07))}
    elif dims == ["merchant_type"]:
        seg = {"merchant_type": pick(C.MERCHANT_TYPES)}
    elif dims == ["merchant"]:
        ch = sh["chains"]
        ch = ch[(ch["in_category"] >= 0.18) & (ch["overall"] >= 0.02)]
        seg = {"merchant": pick(ch["merchant_name"].tolist())}
    else:
        cells = sh["cells"][tuple(dims)]
        floor = 0.035 if "channel" in dims else 0.02
        seg = dict(zip(dims, pick(cells[cells >= floor].index.tolist())))
    two = len(dims) == 2
    if lever == "traffic":
        size = rng.uniform(0.6, 0.9) if family == "traffic_up_category" else -rng.uniform(0.55, 0.75) if two else -rng.uniform(0.3, 0.5)
    elif lever == "conversion":
        size = -rng.uniform(0.5, 0.65) if two else -rng.uniform(0.3, 0.45)
    elif lever == "supply":
        size = -1.0
    elif lever == "redemption":
        size = -rng.uniform(0.6, 0.8) if two else -rng.uniform(0.4, 0.6)
    else:
        size = rng.uniform(0.5, 0.8) if (two or dims == ["merchant"]) else rng.uniform(0.12, 0.25)
    return {"lever": lever, "segment": seg, "size": round(float(size), 3)}


def direction(ev: dict) -> str:
    return "up" if (ev["lever"] == "refund" or (ev["lever"] in ("traffic", "conversion") and ev["size"] > 0)) else "down"


def case_path(case: dict) -> Path:
    return C.DATA / "cases" / f"{case['id']}.duckdb"


def build(case: dict, force: bool = False) -> Path:
    p = case_path(case)
    if force or not p.exists():
        week = int(case["week"][-2:])
        db.build(world.generate(case["seed"], week, case["events"]), p)
    return p


def visible(case: dict, alerts: pd.DataFrame) -> bool:
    """Every planted event raised an alert on one of its own metrics, in total or in a slice of its segment."""
    for ev in case["events"]:
        a = alerts[alerts["metric"].isin(PRIMARY[ev["lever"]])]
        seg = ev["segment"]
        ok = False
        for r in a.itertuples():
            if r.dim == "total" or seg.get(r.dim) == r.slice:
                ok = True
            elif "merchant" in seg and r.dim in ("category", "merchant_type") and (r.slice == "连锁" or seg["merchant"].startswith(r.slice)):
                ok = True  # a chain is seen through its category or through the chain slice
        if not ok:
            return False
    return True


def make(case_id: str, family: str, seed: int, rng: np.random.Generator) -> dict:
    """Draft one case from a family name ('none', 'holiday', 'after_holiday', a FAMILIES key, or 'a+b')."""
    if family == "holiday":
        week, events, causes = HOLIDAY_WEEK, [], [{"segment": {}, "lever": "holiday", "direction": "up"}]
    elif family == "after_holiday":
        week, events, causes = AFTER_HOLIDAY_WEEK, [], []
    else:
        week = int(NORMAL_WEEKS[int(rng.integers(len(NORMAL_WEEKS)))])
        events = []
        if family != "none":
            sh = shares(seed, week)
            for fam in family.split("+"):
                for _ in range(20):
                    ev = draft_event(fam, sh, rng)
                    # two events in one case must not share a dimension value or a lever
                    if all(ev["lever"] != e["lever"] and not set(ev["segment"].items()) & set(e["segment"].items()) for e in events):
                        events.append(ev)
                        break
        causes = [{"segment": e["segment"], "lever": e["lever"], "direction": direction(e)} for e in events]
    return {"id": case_id, "family": family, "seed": int(seed), "week": C.week_id(week), "events": events, "truth": {"causes": causes}}


def make_set(prefix: str, families: list[str], first_seed: int, rng_seed: int, log=print) -> list[dict]:
    rng = np.random.default_rng(rng_seed)
    cases, seed = [], first_seed
    for i, family in enumerate(families, 1):
        for attempt in range(1, 13):
            case = make(f"{prefix}{i:02d}", family, seed, rng)
            seed += 1
            p = build(case, force=True)
            con = db.connect(p)
            al = monitor.alerts(con, case["week"])
            con.close()
            if len(case["events"]) == len(family.split("+")) or family in ("none", "holiday", "after_holiday"):
                if not case["events"] or visible(case, al):
                    case["drafts"] = attempt
                    case["alerts"] = int(len(al))
                    cases.append(case)
                    log(f"{case['id']} {family}: seed {case['seed']}, {case['week']}, drafts {attempt}, alerts {len(al)}, "
                        f"{[(e['lever'], e['segment'], e['size']) for e in case['events']]}")
                    break
        else:
            raise RuntimeError(f"no visible case for {family} after 12 drafts")
    return cases


def save(cases: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cases, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8", newline="\n")


def load(path: Path) -> list[dict]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
