"""Shared constants: paths, the synthetic calendar, dimensions and the levers an event can move."""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Generated databases are large and reproducible, so they live outside the repository by default.
DATA = Path(os.environ.get("BIZ_DATA_DIR") or (ROOT / "data"))
CHECK_SCRIPT = ROOT / "vendor" / "evidence-check" / "check_evidence.py"

# ---- calendar -----------------------------------------------------------------------------------
START = dt.date(2026, 3, 30)  # Monday of ISO week 14: the first day of the data extract
FIRST_WEEK, LAST_WEEK = 14, 45  # the extract covers ISO weeks 14..45 of 2026
# Redemptions and refunds are booked on the day they happen, so for the first weeks of an extract
# they are incomplete (the vouchers they belong to were paid before the extract starts).
# Weekly tables therefore start six weeks in.
REPORT_FIRST_WEEK = 20
HOLIDAYS = {  # public holidays inside the window (day -> name)
    **{dt.date(2026, 4, 4) + dt.timedelta(days=i): "清明" for i in range(3)},
    **{dt.date(2026, 5, 1) + dt.timedelta(days=i): "劳动节" for i in range(5)},
    **{dt.date(2026, 6, 19) + dt.timedelta(days=i): "端午" for i in range(3)},
    **{dt.date(2026, 9, 25) + dt.timedelta(days=i): "中秋" for i in range(3)},
    **{dt.date(2026, 10, 1) + dt.timedelta(days=i): "国庆" for i in range(7)},
}
BASELINE_WEEKS = 8  # the monitor compares a week with the 8 most recent non-holiday weeks before it


def week_id(week: int) -> str:
    return f"2026-W{week:02d}"


def week_start(week: int) -> dt.date:
    return START + dt.timedelta(days=7 * (week - FIRST_WEEK))


def week_of(day: dt.date) -> int:
    return FIRST_WEEK + (day - START).days // 7


def holiday_weeks() -> set[int]:
    return {week_of(d) for d in HOLIDAYS}


# ---- dimensions ---------------------------------------------------------------------------------
CITIES = [  # name, tier, size weight, weekly growth
    ("北京", "一线", 1.30, 0.0005), ("上海", "一线", 1.40, 0.0005), ("广州", "一线", 1.10, 0.0010),
    ("深圳", "一线", 1.05, 0.0010), ("成都", "新一线", 1.00, 0.0020), ("杭州", "新一线", 0.85, 0.0015),
    ("重庆", "新一线", 0.90, 0.0020), ("武汉", "新一线", 0.80, 0.0015), ("昆明", "二线", 0.50, 0.0025),
    ("福州", "二线", 0.48, 0.0025), ("南昌", "二线", 0.45, 0.0030), ("贵阳", "二线", 0.42, 0.0030),
]
# name, industry, typical deal price, commission rate, share of traffic, base redemption probability,
# mean days from payment to redemption
CATEGORIES = [
    ("火锅", "餐饮", 138.0, 0.026, 0.15, 0.86, 5.0),
    ("烧烤", "餐饮", 98.0, 0.026, 0.10, 0.85, 5.0),
    ("茶饮咖啡", "餐饮", 19.9, 0.030, 0.18, 0.92, 2.0),
    ("快餐小吃", "餐饮", 29.9, 0.030, 0.16, 0.90, 3.0),
    ("自助餐", "餐饮", 168.0, 0.026, 0.07, 0.84, 6.0),
    ("地方菜", "餐饮", 118.0, 0.026, 0.10, 0.85, 5.0),
    ("美发美甲", "综合", 128.0, 0.045, 0.08, 0.74, 10.0),
    ("休闲娱乐", "综合", 88.0, 0.040, 0.08, 0.78, 8.0),
    ("亲子乐园", "综合", 79.0, 0.040, 0.04, 0.80, 8.0),
    ("运动健身", "综合", 199.0, 0.045, 0.04, 0.70, 12.0),
]
# name, share of traffic, visit-to-order conversion, share of orders placed by first-time buyers,
# share of orders that carry a platform subsidy
CHANNELS = [
    ("短视频", 0.42, 0.030, 0.34, 0.30),
    ("直播", 0.22, 0.060, 0.30, 0.42),
    ("搜索", 0.20, 0.115, 0.15, 0.16),
    ("门店页", 0.16, 0.100, 0.12, 0.12),
]
MERCHANT_TYPES = ["连锁", "单店"]
CITY_NAMES = [c[0] for c in CITIES]
CATEGORY_NAMES = [c[0] for c in CATEGORIES]
CHANNEL_NAMES = [c[0] for c in CHANNELS]

# Dimensions a cause can be stated in (column names of the wide order view and of the weekly cube).
DIMENSIONS = {
    "city": "城市", "category": "品类", "channel": "渠道", "merchant_type": "商家类型", "merchant": "商家",
}
# What an event moves. The labels are what reports and answer keys use.
LEVERS = {
    "traffic": "流量",        # visits to the deal pages
    "conversion": "转化",     # share of visits that end in a paid order
    "supply": "供给",         # stores going offline
    "redemption": "核销",     # paid vouchers being redeemed in store
    "refund": "退款",         # paid vouchers being refunded
    "holiday": "节假日",      # the calendar: a public holiday, no segment
}
