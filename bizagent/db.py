"""Build a DuckDB file from generated tables, and open it for reading only.

A reader connection cannot write to the database, cannot touch the file system and cannot load
extensions (DuckDB's enable_external_access = false), and the SQL tool lets through one SELECT at a time.
"""
from __future__ import annotations

import threading
from pathlib import Path

import duckdb
import pandas as pd

from . import config as C

BUILD_SQL = sorted((C.ROOT / "sql" / "build").glob("*.sql"))
MAX_ROWS = 60  # rows of a query result shown to a model or written to an evidence file
QUERY_SECONDS = 20.0  # a query from the SQL tool is interrupted after this long
DATE_COLS = {
    "dim_date": ["date", "week_start"], "dim_user": ["first_pay_date"], "dim_store": ["open_date"],
    "fact_traffic_daily": ["visit_date"], "fact_order": ["pay_date"], "fact_redeem": ["redeem_date"],
    "fact_refund": ["refund_date"], "fact_store_offline": ["offline_from", "offline_to"],
}


def build(tables: dict[str, pd.DataFrame], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = duckdb.connect(str(path))
    try:
        for name, df in tables.items():
            casts = ", ".join(f'CAST("{c}" AS DATE) AS "{c}"' for c in DATE_COLS.get(name, []))
            if name == "fact_store_offline":
                casts += ', CAST(store_id AS BIGINT) AS store_id'
            con.register("_df", df)
            con.execute(f'CREATE TABLE "{name}" AS SELECT * {"REPLACE (" + casts + ")" if casts else ""} FROM _df')
            con.unregister("_df")
        for f in BUILD_SQL:
            con.execute(f.read_text(encoding="utf-8"))
        from .metrics import build_metric_weekly  # after the cubes exist
        build_metric_weekly(con)
    finally:
        con.close()
    return path


def connect(path: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(path), read_only=True)
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return con


class SqlRefused(Exception):
    """The statement is not a single read-only query."""


def check_select(sql: str) -> str:
    try:
        statements = duckdb.extract_statements(sql)
    except Exception as e:  # a syntax error: report it as the database would
        raise SqlRefused(f"SQL 解析失败：{e}") from e
    if len(statements) != 1:
        raise SqlRefused("一次只能执行一条 SQL。")
    if statements[0].type != duckdb.StatementType.SELECT:
        raise SqlRefused("只能执行 SELECT 查询。")
    return sql.strip().rstrip(";")


def query(con: duckdb.DuckDBPyConnection, sql: str, timeout: float = QUERY_SECONDS) -> pd.DataFrame:
    """Run one SELECT. A query that runs longer than `timeout` seconds is interrupted."""
    sql = check_select(sql)
    timer = threading.Timer(timeout, con.interrupt)
    timer.start()
    try:
        return con.execute(sql).fetchdf()
    except duckdb.InterruptException as e:
        raise SqlRefused(f"查询超过 {timeout:.0f} 秒被中止。请先在子查询里聚合，再做关联。") from e
    finally:
        timer.cancel()


def fmt(v) -> str:
    """One way to print a cell, so reports and evidence files show the same digits."""
    if v is None or (isinstance(v, float) and v != v) or v is pd.NaT:
        return ""
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".") if abs(v) < 1e15 else str(v)
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    return str(v)


def to_markdown(df: pd.DataFrame, max_rows: int = MAX_ROWS) -> str:
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in df.head(max_rows).itertuples(index=False):
        lines.append("| " + " | ".join(fmt(v.item() if hasattr(v, "item") else v) for v in row) + " |")
    if len(df) > max_rows:
        lines.append(f"（共 {len(df)} 行，只显示前 {max_rows} 行）")
    return "\n".join(lines)
