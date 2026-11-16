"""The metric catalog (metrics/metrics.yaml) and the SQL it compiles to.

A metric is a simple additive measure, a ratio of two measures, or a distinct count. Every number
the monitor, the decomposition and the reports use comes out of the SQL built here.
"""
from __future__ import annotations

from functools import lru_cache

import yaml

from . import config as C

MONITOR_DIMS = ["city", "category", "channel", "merchant_type"]  # slices the weekly monitor watches


@lru_cache(maxsize=1)
def catalog() -> dict:
    cat = yaml.safe_load((C.ROOT / "metrics" / "metrics.yaml").read_text(encoding="utf-8"))
    cat["by_name"] = {m["name"]: m for m in cat["metrics"]}
    return cat


def metric(name: str) -> dict:
    try:
        return catalog()["by_name"][name]
    except KeyError:
        raise KeyError(f"没有这个指标：{name}。可用的指标：{', '.join(catalog()['by_name'])}") from None


def parts(m: dict) -> tuple[dict, dict | None]:
    """The numerator measure and, for a ratio, the denominator measure."""
    ms = catalog()["measures"]
    if m["type"] == "ratio":
        return ms[m["numerator"]], ms[m["denominator"]]
    return ms[m["measure"]], None


def dims_of(m: dict) -> list[str]:
    num, den = parts(m)
    return [d for d in num["dims"] if den is None or d in den["dims"]]


def scale(m: dict) -> float:
    return 100.0 if m["unit"] == "%" else 1.0


def change_kind(m: dict) -> str:
    """'abs': a percentage-type ratio moves in percentage points; 'rel': everything else moves in %."""
    return "abs" if m["unit"] == "%" else "rel"


def _where(filters: dict | None, weeks: list[str] | None, week_col: str) -> str:
    conds = [f"{d} = '{str(v).replace(chr(39), chr(39) * 2)}'" for d, v in (filters or {}).items()]
    if weeks is not None:
        conds.append(f"{week_col} IN ({', '.join(repr(w) for w in weeks)})")
    else:
        conds.append(f"{week_col} IN (SELECT week_id FROM dim_week WHERE in_report)")
    return "WHERE " + " AND ".join(conds)


def measure_sql(ms: dict, dims: list[str], filters: dict | None = None, weeks: list[str] | None = None) -> str:
    wk = ms.get("week", "week_id")
    cols = "".join(f", {d}" for d in dims)
    return f"SELECT {wk} AS week_id{cols}, {ms['expr']} AS v FROM {ms['table']} {_where(filters, weeks, wk)} GROUP BY ALL"


def series_sql(m: dict, dims: list[str] | None = None, filters: dict | None = None, weeks: list[str] | None = None) -> str:
    """Weekly numerator, denominator and value of a metric, by the given dimensions."""
    dims = dims or []
    allowed = dims_of(m)
    for d in list(dims) + list(filters or {}):
        if d not in allowed:
            raise ValueError(f"{m['label']} 不能按 {catalog()['dimensions'].get(d, {}).get('label', d)} 拆。可用的维度：{', '.join(allowed)}")
    num, den = parts(m)
    keys = ["week_id"] + dims
    using = ", ".join(keys)
    if den is None:
        return (f"SELECT {using}, v AS num, NULL AS den, v AS value "
                f"FROM ({measure_sql(num, dims, filters, weeks)})")
    return (f"SELECT {using}, COALESCE(n.v, 0) AS num, COALESCE(d.v, 0) AS den, "
            f"{scale(m)} * COALESCE(n.v, 0) / NULLIF(d.v, 0) AS value "
            f"FROM ({measure_sql(num, dims, filters, weeks)}) n "
            f"FULL JOIN ({measure_sql(den, dims, filters, weeks)}) d USING ({using})")


def build_metric_weekly(con) -> None:
    """One long table: every metric, in total and by each monitored dimension, for every report week.

    share: the slice's part of the total (of the measure for a simple metric, of the denominator for a ratio).
    """
    con.execute("""CREATE OR REPLACE TABLE metric_weekly (
        metric VARCHAR, label VARCHAR, grp VARCHAR, unit VARCHAR, change_kind VARCHAR, min_change DOUBLE,
        monitored BOOLEAN, monitored_slices BOOLEAN, dim VARCHAR, slice VARCHAR, week_id VARCHAR, num DOUBLE, den DOUBLE, value DOUBLE, share DOUBLE)""")
    for m in catalog()["metrics"]:
        head = (f"'{m['name']}', '{m['label']}', '{m['group']}', '{m['unit']}', '{change_kind(m)}', {float(m['min_change'])}, "
                f"{'true' if m.get('monitor', True) else 'false'}, {'true' if m.get('monitor_slices') else 'false'}")
        weight = "den" if m["type"] == "ratio" else "num"
        con.execute(f"INSERT INTO metric_weekly SELECT {head}, 'total', '全部', week_id, num, den, value, 1.0 FROM ({series_sql(m)})")
        for d in [d for d in MONITOR_DIMS if d in dims_of(m)]:
            con.execute(f"""INSERT INTO metric_weekly
                SELECT {head}, '{d}', {d}, week_id, num, den, value,
                       {weight} / NULLIF(SUM({weight}) OVER (PARTITION BY week_id), 0)
                FROM ({series_sql(m, [d])})""")


def show(m: dict, v) -> str:
    """A value with its unit, for prose."""
    if v is None or v != v:
        return "无"
    if m["unit"] == "%":
        return f"{v:.2f}%"
    if m["unit"] == "元":
        return f"{v:,.2f} 元" if abs(v) < 1000 else f"{v:,.0f} 元"
    return f"{v:,.0f} {m['unit']}"
