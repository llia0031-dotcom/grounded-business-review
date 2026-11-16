"""Charts for the weekly review (matplotlib, static PNG).

Three forms, each for one job:
  trend         change over time: one metric by week, the baseline it is judged against, the week under review marked;
  contribution  magnitude: how much each slice added to or took from the change, the named cause highlighted;
  rate_mix      two parts of a ratio's change per slice: its own rate moving, and its share moving.
One y-axis per chart, text in ink (never in the series colour), red and green only for "worse" and "better".
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from . import config as C  # noqa: E402
from . import decompose as D, metrics as M  # noqa: E402

INK, INK2, MUTED, RULE, SHEET = "#110f0a", "#383530", "#58554f", "#dddad6", "#fbfaf7"
WORSE, BETTER, NEUTRAL_BAR = "#b32517", "#07602f", "#b9b5ae"
SERIES_1, SERIES_2 = "#2a78d6", "#eb6834"   # two categorical slots, used only by rate_mix
plt.rcParams.update({
    "font.family": ["Microsoft YaHei", "sans-serif"], "font.size": 10,
    "axes.edgecolor": RULE, "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.spines.top": False, "axes.spines.right": False, "axes.unicode_minus": False,
    "figure.facecolor": SHEET, "axes.facecolor": SHEET, "savefig.facecolor": SHEET,
})


def _num(m: dict):
    if m["unit"] == "%":
        return FuncFormatter(lambda v, _: f"{v:.1f}%")
    return FuncFormatter(lambda v, _: f"{v / 1e4:g} 万" if abs(v) >= 1e5 else f"{v:,.0f}")


def _title(ax, title: str, sub: str):
    ax.set_title(title, loc="left", fontsize=13, color=INK, fontweight="bold", pad=26)
    ax.text(0, 1.035, sub, transform=ax.transAxes, fontsize=9, color=MUTED, va="bottom")


def _good(m: dict, change: float) -> str:
    if m["direction"] == "neutral" or change == 0:
        return INK2
    return BETTER if (change > 0) == (m["direction"] == "up") else WORSE


def trend(con, metric: str, week: str, path: Path, filters: dict | None = None) -> Path:
    m = M.metric(metric)
    rows = con.execute(f"SELECT week_id, value FROM ({M.series_sql(m, [], filters)}) WHERE week_id <= ? ORDER BY 1", [week]).fetchall()
    weeks, vals = [r[0] for r in rows], [float(r[1]) for r in rows]
    hol = {r[0] for r in con.execute("SELECT week_id FROM dim_week WHERE is_holiday_week").fetchall()}
    base = set(D.baseline_weeks(con, week))
    base_vals = sorted(v for w, v in zip(weeks, vals) if w in base)
    median = (base_vals[len(base_vals) // 2 - 1] + base_vals[len(base_vals) // 2]) / 2 if len(base_vals) % 2 == 0 else base_vals[len(base_vals) // 2]
    x = list(range(len(weeks)))
    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=180)
    for i, w in enumerate(weeks):
        if w in hol:
            ax.axvspan(i - 0.5, i + 0.5, color=RULE, alpha=0.45, lw=0)
    first_base = min(i for i, w in enumerate(weeks) if w in base)
    ax.hlines(median, first_base - 0.5, x[-1] + 0.5, colors=MUTED, linestyles=(0, (4, 3)), lw=1.2)
    ax.annotate("基线中位数", (x[-1] + 0.5, median), xytext=(4, 0), textcoords="offset points", color=MUTED, fontsize=8.5, va="center")
    ax.plot(x, vals, color=INK, lw=2, solid_capstyle="round")
    colour = _good(m, vals[-1] - median)
    ax.scatter([x[-1]], [vals[-1]], s=70, color=colour, zorder=5, edgecolors=SHEET, linewidths=2)
    change = 100 * (vals[-1] - median) / abs(median) if m["unit"] != "%" else vals[-1] - median
    label = f"{week[-3:]}  {M.show(m, vals[-1])}\n较基线 {change:+.1f}{'pp' if m['unit'] == '%' else '%'}"
    ax.annotate(label, (x[-1], vals[-1]), xytext=(10, 0), textcoords="offset points", fontsize=9, color=INK, va="center")
    ax.set_xticks(x[::2]); ax.set_xticklabels([w[-3:] for w in weeks][::2])
    ax.yaxis.set_major_formatter(_num(m))
    ax.grid(axis="y", color=RULE, lw=0.6); ax.set_axisbelow(True)
    ax.set_xlim(-0.6, x[-1] + 4.5)
    scope = "、".join(f"{C.DIMENSIONS[k]}={v}" for k, v in (filters or {}).items())
    better = {"up": "越高越好", "down": "越低越好", "neutral": "结合其他指标看"}[m["direction"]]
    _title(ax, f"{m['label']}{'（' + scope + '）' if scope else ''}：按周",
           f"单位：{m['unit']}，{better}。灰底是含法定节假日的周，虚线是最近 8 个非节假日周的中位数。合成数据。")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path); plt.close(fig)
    return path


def contribution(con, metric: str, week: str, dim: str, path: Path, highlight: str | None = None,
                 filters: dict | None = None, compare: str = "baseline", top: int = 10) -> Path:
    m = M.metric(metric)
    df = D.breakdown(con, metric, week, dim, filters, compare).head(top).iloc[::-1]
    ref = "基线" if compare == "baseline" else "上周"
    fig, ax = plt.subplots(figsize=(9, 0.42 * len(df) + 1.7), dpi=180)
    colours = [(_good(m, e) if s == highlight else NEUTRAL_BAR) for s, e in zip(df["slice"], df["effect"])]
    ax.barh(df["slice"], df["effect"], color=colours, height=0.62)
    span = max(abs(df["effect"].min()), abs(df["effect"].max())) or 1.0
    for s, e, c in zip(df["slice"], df["effect"], df["contribution_pct"]):
        txt = f"{e:+,.0f}" if m["unit"] != "%" else f"{e:+.2f}pp"
        ax.text(e + (span * 0.015 if e >= 0 else -span * 0.015), s, f"{txt}（{c:.0f}%）", va="center", ha="left" if e >= 0 else "right",
                fontsize=8.5, color=INK)
    ax.axvline(0, color=MUTED, lw=0.8)
    ax.set_xlim(min(df["effect"].min(), 0) - span * 0.32, max(df["effect"].max(), 0) + span * 0.32)
    ax.xaxis.set_major_formatter(_num(m) if m["unit"] != "%" else FuncFormatter(lambda v, _: f"{v:+.1f}"))
    ax.grid(axis="x", color=RULE, lw=0.6); ax.set_axisbelow(True)
    ax.tick_params(axis="y", length=0, labelcolor=INK)
    scope = "、".join(f"{C.DIMENSIONS[k]}={v}" for k, v in (filters or {}).items())
    total = df.attrs["total_change"]
    _title(ax, f"{m['label']}的变化来自哪里：按{C.DIMENSIONS[dim]}" + (f"（{scope}内）" if scope else ""),
           f"{week} 对比{ref}，总变化 {total:+,.0f} {m['unit']}。" if m["unit"] != "%" else f"{week} 对比{ref}，总变化 {total:+.2f}pp。")
    ax.set_xlabel("条形：这一组的变化量；括号里是它占总变化的比例（贡献度）。合成数据。", loc="left", fontsize=8.5, color=MUTED, labelpad=8)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path); plt.close(fig)
    return path


def rate_mix(con, metric: str, week: str, dim: str, path: Path, compare: str = "baseline", top: int = 10) -> Path:
    m = M.metric(metric)
    df = D.breakdown(con, metric, week, dim, None, compare).head(top).iloc[::-1]
    ref = "基线" if compare == "baseline" else "上周"
    fig, ax = plt.subplots(figsize=(9, 0.5 * len(df) + 1.9), dpi=180)
    y = list(range(len(df)))
    ax.barh([i + 0.19 for i in y], df["rate_effect"], height=0.34, color=SERIES_1, label="率效应：这一组自己的率变了")
    ax.barh([i - 0.19 for i in y], df["mix_effect"], height=0.34, color=SERIES_2, label="结构效应：这一组的占比变了")
    ax.set_yticks(y); ax.set_yticklabels(df["slice"])
    ax.axvline(0, color=MUTED, lw=0.8)
    ax.grid(axis="x", color=RULE, lw=0.6); ax.set_axisbelow(True)
    ax.tick_params(axis="y", length=0, labelcolor=INK)
    unit = "pp" if m["unit"] == "%" else m["unit"]
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:+.1f}"))
    ax.legend(loc="lower right", frameon=False, fontsize=9, labelcolor=INK2)
    _title(ax, f"{m['label']}的变化：率效应和结构效应，按{C.DIMENSIONS[dim]}",
           f"{week} 对比{ref}，总变化 {df.attrs['total_change']:+.2f} {unit}；各组两种效应相加等于总变化。单位：{unit}。合成数据。")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path); plt.close(fig)
    return path
