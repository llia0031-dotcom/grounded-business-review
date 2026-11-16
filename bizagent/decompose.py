"""Break a metric's change down by a dimension. No model is involved.

Formulas, all from public sources:
  * additive metric: contribution of a slice = its change / the total change
    (Adtributor's explanatory power, Bhagwan et al., NSDI 2014, eq. 4);
  * ratio metric: rate effect + mix effect with the two periods' average as weights, which leaves no
    residual (Kitagawa 1955; Das Gupta 1993). rate effect = the slice's own rate moved;
    mix effect = the slice's share of the denominator moved and its rate differs from the overall rate;
  * which slices to report: Adtributor's rule. Inside a dimension, take slices in order of surprise
    (Jensen-Shannon divergence between the slice's share before and after), keep those that explain
    more than 10% each, stop once they explain 67% together; rank dimensions by total surprise.

The comparison period is either the previous week or the monitor's baseline: the 8 most recent
non-holiday weeks before the week under analysis, averaged.
"""
from __future__ import annotations


import numpy as np
import pandas as pd

from . import config as C
from . import metrics as M

T_EEP, T_EP = 0.10, 0.67  # Adtributor's thresholds: per-slice and cumulative explanatory power
DIM_LABEL = {**{k: v for k, v in C.DIMENSIONS.items()}}


def baseline_weeks(con, week: str) -> list[str]:
    rows = con.execute(
        "SELECT week_id FROM dim_week WHERE in_report AND NOT is_holiday_week AND week_id < ? ORDER BY week_id DESC LIMIT ?",
        [week, C.BASELINE_WEEKS]).fetchall()
    return [r[0] for r in rows]


def previous_week(con, week: str) -> str | None:
    return con.execute("SELECT MAX(week_id) FROM dim_week WHERE in_report AND week_id < ?", [week]).fetchone()[0]


def _two_periods(con, m: dict, week: str, dim: str | None, filters: dict | None, compare: str) -> pd.DataFrame:
    """num and den of each slice in the comparison period (suffix 0) and in `week` (suffix 1)."""
    if compare == "last_week":
        base = [previous_week(con, week)]
    elif compare == "baseline":
        base = baseline_weeks(con, week)
    else:
        raise ValueError("compare 只能是 baseline 或 last_week")
    if not base or base[0] is None:
        raise ValueError(f"{week} 之前没有可比的周")
    dims = [dim] if dim else []
    df = con.execute(M.series_sql(m, dims, filters, base + [week])).fetchdf()
    key = dim or "_all"
    if not dim:
        df[key] = "全部"
    df["den"] = df["den"].astype(float)
    cur = df[df["week_id"] == week].groupby(key)[["num", "den"]].sum()
    ref = df[df["week_id"] != week].groupby(key)[["num", "den"]].sum() / len(base)
    out = (ref.add_suffix("0").join(cur.add_suffix("1"), how="outer").fillna(0.0)
           .reset_index().rename(columns={key: "slice"}))
    out.attrs["base_weeks"] = base
    return out


def _js(p: np.ndarray, q: np.ndarray) -> np.ndarray:
    """Per-slice term of the Jensen-Shannon divergence between shares p (before) and q (after)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        a = np.where(p > 0, p * np.log10(2 * p / (p + q)), 0.0)
        b = np.where(q > 0, q * np.log10(2 * q / (p + q)), 0.0)
    return 0.5 * (a + b)


def breakdown(con, metric: str, week: str, dim: str | None = None, filters: dict | None = None,
              compare: str = "baseline") -> pd.DataFrame:
    """One row per slice of `dim` (or one row in total), sorted by the size of its contribution.

    Columns for an additive metric: base, cur, change, change_pct, contribution_pct, share_base, share_cur.
    Extra columns for a ratio: num/den of both periods, rate_effect, mix_effect (in the metric's unit).
    `surprise` is Adtributor's surprise. attrs: total_base, total_cur, total_change, base_weeks.
    """
    m = M.metric(metric)
    t = _two_periods(con, m, week, dim, filters, compare)
    k = M.scale(m)
    if m["type"] == "ratio":
        N0, D0, N1, D1 = (t[c].sum() for c in ("num0", "den0", "num1", "den1"))
        R0, R1 = (k * N0 / D0 if D0 else float("nan")), (k * N1 / D1 if D1 else float("nan"))
        with np.errstate(divide="ignore", invalid="ignore"):
            r0 = np.where(t["den0"] > 0, k * t["num0"] / t["den0"], np.nan)
            r1 = np.where(t["den1"] > 0, k * t["num1"] / t["den1"], np.nan)
        r0f, r1f = np.where(np.isnan(r0), r1, r0), np.where(np.isnan(r1), r0, r1)  # a slice absent in one period has no rate change
        w0, w1 = (t["den0"] / D0 if D0 else 0.0), (t["den1"] / D1 if D1 else 0.0)
        rate_eff = np.nan_to_num((w0 + w1) / 2 * (r1f - r0f))
        mix_eff = np.nan_to_num((w1 - w0) * ((r0f + r1f) / 2 - (R0 + R1) / 2))
        total = R1 - R0
        out = pd.DataFrame({
            "slice": t["slice"], "num_base": t["num0"], "den_base": t["den0"], "base": r0,
            "num_cur": t["num1"], "den_cur": t["den1"], "cur": r1, "change": r1 - r0,
            "rate_effect": rate_eff, "mix_effect": mix_eff, "effect": rate_eff + mix_eff,
            "share_base": 100 * w0, "share_cur": 100 * w1,
        })
        out["contribution_pct"] = 100 * out["effect"] / total if abs(total) > 1e-12 else np.nan
        out["surprise"] = (_js(np.asarray(t["num0"] / N0 if N0 else 0.0), np.asarray(t["num1"] / N1 if N1 else 0.0))
                           + _js(np.asarray(w0), np.asarray(w1)))
        attrs = {"total_base": R0, "total_cur": R1, "total_change": total}
    else:
        F, A = t["num0"].sum(), t["num1"].sum()
        change = t["num1"] - t["num0"]
        with np.errstate(divide="ignore", invalid="ignore"):
            pct = np.where(t["num0"] != 0, 100 * change / t["num0"].abs(), np.nan)
        out = pd.DataFrame({
            "slice": t["slice"], "base": t["num0"], "cur": t["num1"], "change": change, "change_pct": pct,
            "share_base": 100 * t["num0"] / F if F else np.nan, "share_cur": 100 * t["num1"] / A if A else np.nan,
        })
        additive = M.parts(m)[0].get("additive", True)
        out["effect"] = change if additive else np.nan
        out["contribution_pct"] = 100 * change / (A - F) if additive and abs(A - F) > 1e-9 else np.nan
        out["surprise"] = _js(np.asarray(t["num0"] / F if F else 0.0), np.asarray(t["num1"] / A if A else 0.0)) if additive else np.nan
        attrs = {"total_base": F, "total_cur": A, "total_change": A - F}
    out = out.sort_values("effect", key=lambda s: s.abs(), ascending=False, na_position="last", ignore_index=True)
    out.attrs.update(attrs, base_weeks=t.attrs["base_weeks"], metric=metric, dim=dim, compare=compare, week=week,
                     filters=dict(filters or {}))
    return out


def pick(df: pd.DataFrame) -> dict | None:
    """Adtributor's choice of slices inside one dimension, or None when no set explains enough."""
    d = df[df["contribution_pct"].notna()].sort_values("surprise", ascending=False)
    chosen, ep, surprise = [], 0.0, 0.0
    for row in d.itertuples(index=False):
        e = row.contribution_pct / 100.0
        if e > T_EEP:
            chosen.append(row.slice); ep += e; surprise += row.surprise
            if ep > T_EP:
                return {"slices": chosen, "explains": ep, "surprise": surprise}
    return None


def adtributor(con, metric: str, week: str, dims: list[str] | None = None, filters: dict | None = None,
               compare: str = "baseline") -> list[dict]:
    """Candidate explanations, one per dimension, the most surprising first."""
    m = M.metric(metric)
    out = []
    for dim in dims or [d for d in M.dims_of(m) if d not in (filters or {})]:
        df = breakdown(con, metric, week, dim, filters, compare)
        got = pick(df)
        if got:
            out.append({"dim": dim, **got})
    return sorted(out, key=lambda c: -c["surprise"])


# ---- tables for people and models ---------------------------------------------------------------
def _unit(m: dict) -> str:
    return {"%": "%", "元": "元"}.get(m["unit"], m["unit"])


def table(df: pd.DataFrame, top: int = 12) -> pd.DataFrame:
    """The breakdown with Chinese headers and rounded values, as shown in tool results and evidence files."""
    m = M.metric(df.attrs["metric"])
    dim = df.attrs["dim"]
    name = DIM_LABEL.get(dim, "范围") if dim else "范围"
    ref = "基线" if df.attrs["compare"] == "baseline" else "上周"
    u = _unit(m)
    d = df.head(top)
    r2 = lambda s: s.astype(float).round(2)
    if m["type"] == "ratio":
        pp = "变化_pp" if m["unit"] == "%" else f"变化_{u}"
        t = pd.DataFrame({
            name: d["slice"], f"{ref}{m['label']}_{u}": r2(d["base"]), f"本周{m['label']}_{u}": r2(d["cur"]), pp: r2(d["change"]),
            "率效应": r2(d["rate_effect"]), "结构效应": r2(d["mix_effect"]), "合计贡献": r2(d["effect"]),
            "贡献度_pct": r2(d["contribution_pct"]), f"{ref}占比_pct": r2(d["share_base"]), "本周占比_pct": r2(d["share_cur"]),
            f"{ref}分子": r2(d["num_base"]), f"{ref}分母": r2(d["den_base"]), "本周分子": r2(d["num_cur"]), "本周分母": r2(d["den_cur"]),
        })
    else:
        t = pd.DataFrame({
            name: d["slice"], f"{ref}_{u}": r2(d["base"]), f"本周_{u}": r2(d["cur"]), f"变化_{u}": r2(d["change"]),
            "变化率_pct": r2(d["change_pct"]), "贡献度_pct": r2(d["contribution_pct"]),
            f"{ref}占比_pct": r2(d["share_base"]), "本周占比_pct": r2(d["share_cur"]),
        })
    return t


def total_line(df: pd.DataFrame) -> str:
    m = M.metric(df.attrs["metric"])
    ref = "基线（前 8 个非节假日周的平均）" if df.attrs["compare"] == "baseline" else "上周"
    a = df.attrs
    scope = "、".join(f"{DIM_LABEL.get(k, k)}={v}" for k, v in a["filters"].items()) or "全部"
    unit = "pp" if m["unit"] == "%" else m["unit"]
    return (f"{m['label']}（范围：{scope}）：{ref} {a['total_base']:.2f}，本周 {a['total_cur']:.2f}，"
            f"变化 {a['total_change']:.2f} {unit}")
