"""Weekly monitor: runs sql/monitor/weekly_alerts.sql for one week."""
from __future__ import annotations

import pandas as pd

from . import config as C

ALERT_SQL = (C.ROOT / "sql" / "monitor" / "weekly_alerts.sql").read_text(encoding="utf-8")


def scan(con, week: str) -> pd.DataFrame:
    """Every metric in total and by monitored slice for `week`, with the baseline and the alert flag."""
    return con.execute(ALERT_SQL, {"week": week}).fetchdf()


def alerts(con, week: str) -> pd.DataFrame:
    df = scan(con, week)
    return df[df["is_alert"]].reset_index(drop=True)


def week_info(con, week: str) -> dict:
    row = con.execute("SELECT week_id, week_start, week_end, is_holiday_week, holiday_names FROM dim_week WHERE week_id = ?", [week]).fetchone()
    if row is None:
        raise ValueError(f"数据里没有这一周：{week}")
    return {"week_id": row[0], "week_start": str(row[1]), "week_end": str(row[2]), "is_holiday_week": bool(row[3]), "holiday_names": row[4]}


def latest_week(con) -> str:
    return con.execute("SELECT MAX(week_id) FROM dim_week WHERE in_report").fetchone()[0]
