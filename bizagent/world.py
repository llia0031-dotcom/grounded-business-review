"""Synthetic in-store group-buying data: stores, deals, paid orders, redemptions, refunds, traffic.

Everything here is generated from a random seed. It is not, and does not imitate, any platform's
real business data. The rates, prices and commission levels are illustrative.

The world is a product of factors (base level x city trend x day-of-week x holiday x noise), in the
style of Nike's timeseries-generator; entities interact over time as in dbt's jaffle-shop-generator.
An *event* changes one lever (traffic, conversion, supply, redemption, refund) for one segment during
one week. Events are applied to the rates *before* rows are generated, so orders, redemptions and
refunds stay consistent with each other. Within a segment every cell moves by the same proportion
(the "ripple effect" assumption of HotSpot / Squeeze); Poisson and binomial draws add the noise.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from . import config as C

VALID_DAYS = 30          # a voucher can be redeemed for 30 days after payment
DAILY_VISITS = 21000.0   # average visits to deal pages per day at the start of the window
DEAL_TYPES = [("代金券", 0.45, 0.30), ("单人套餐", 0.70, 0.25), ("双人套餐", 1.00, 0.30), ("多人套餐", 1.60, 0.15)]
N_CITY, N_CAT, N_CHAN = len(C.CITIES), len(C.CATEGORIES), len(C.CHANNELS)


def _affinity() -> np.ndarray:
    """How much each category leans on each channel (rows: categories, columns: channels)."""
    a = np.ones((N_CAT, N_CHAN))
    for k, (name, industry, *_rest) in enumerate(C.CATEGORIES):
        if name in ("茶饮咖啡", "快餐小吃"):
            a[k] = [0.95, 0.70, 1.20, 1.30]
        elif industry == "餐饮":
            a[k] = [1.00, 1.30, 0.90, 0.85]
        else:
            a[k] = [1.15, 0.90, 1.10, 0.80]
    return a


def static_world(rng: np.random.Generator) -> dict:
    """Merchants, stores and deals. Chains own several stores of one category across cities."""
    stores, merchants = [], []
    for k, (cat, *_r) in enumerate(C.CATEGORIES):
        chains = []
        for j, (letter, n_cities, share) in enumerate([("A", (8, 12), 0.25), ("B", (5, 8), 0.15), ("C", (3, 5), 0.10)]):
            mid = len(merchants)
            merchants.append({"merchant_id": mid, "merchant_name": f"{cat}连锁{letter}", "merchant_type": "连锁"})
            cities = rng.choice(N_CITY, size=int(rng.integers(n_cities[0], n_cities[1] + 1)), replace=False)
            chains.append((mid, set(int(c) for c in cities), share))
        for c, (city, _tier, size, _g) in enumerate(C.CITIES):
            n = max(3, int(round(size * C.CATEGORIES[k][4] * 90)))
            owners = []
            for mid, cities, share in chains:
                if c in cities:
                    owners += [mid] * max(1, int(round(n * share)))
            owners = owners[: n - 1]  # every cell keeps at least one independent store
            for i in range(n):
                if i < len(owners):
                    mid, chain = owners[i], True
                else:
                    mid, chain = len(merchants), False
                    merchants.append({"merchant_id": mid, "merchant_name": f"{city}{cat}单店{i:03d}", "merchant_type": "单店"})
                stores.append({"merchant_id": mid, "city_id": c, "category_id": k, "chain": chain})
    st = pd.DataFrame(stores)
    st.insert(0, "store_id", np.arange(len(st)))
    n = len(st)
    st["weight"] = rng.lognormal(0.0, 1.1, n) * np.where(st["chain"], 1.8, 1.0)
    st["quality"] = np.clip(rng.lognormal(0.0, 0.03, n), 0.9, 1.1)  # multiplies the redemption probability
    # 6% of stores open during the window; the first store of each cell is always an old one
    open_day = np.where(rng.random(n) < 0.06, rng.integers(7, 7 * (C.LAST_WEEK - C.FIRST_WEEK + 1), n), -rng.integers(30, 720, n))
    first = st.groupby(["city_id", "category_id"])["store_id"].min().to_numpy()
    open_day[first] = -rng.integers(30, 720, len(first))
    st["open_day"] = open_day
    price_level = rng.lognormal(0.0, 0.15, n)
    deals = []
    for s in range(n):
        base = C.CATEGORIES[st.at[s, "category_id"]][2] * price_level[s]
        picks = sorted(rng.choice(len(DEAL_TYPES), size=int(rng.integers(2, 4)), replace=False))
        for t in picks:
            name, mult, w = DEAL_TYPES[t]
            sale = round(base * mult, 1)
            deals.append({"store_id": s, "deal_type": name, "sale_price": sale,
                          "list_price": round(sale / rng.uniform(0.55, 0.8), 0), "w": w})
    dl = pd.DataFrame(deals)
    dl.insert(0, "deal_id", np.arange(len(dl)))
    return {"stores": st, "merchants": pd.DataFrame(merchants), "deals": dl}


def _cells(segment: dict) -> np.ndarray:
    """Boolean mask over (city, category, channel) for a segment stated in those three dimensions."""
    m = np.ones((N_CITY, N_CAT, N_CHAN), dtype=bool)
    for dim, value in segment.items():
        if dim == "city":
            m &= (np.arange(N_CITY) == C.CITY_NAMES.index(value))[:, None, None]
        elif dim == "category":
            m &= (np.arange(N_CAT) == C.CATEGORY_NAMES.index(value))[None, :, None]
        elif dim == "channel":
            m &= (np.arange(N_CHAN) == C.CHANNEL_NAMES.index(value))[None, None, :]
        else:
            raise ValueError(f"a {dim} segment cannot be used with a traffic or conversion event")
    return m


def _rows(segment: dict, city, cat, chan, store, world) -> np.ndarray:
    """Boolean mask over order rows for a segment in any of the five dimensions."""
    m = np.ones(len(city), dtype=bool)
    st, me = world["stores"], world["merchants"]
    for dim, value in segment.items():
        if dim == "city":
            m &= city == C.CITY_NAMES.index(value)
        elif dim == "category":
            m &= cat == C.CATEGORY_NAMES.index(value)
        elif dim == "channel":
            m &= chan == C.CHANNEL_NAMES.index(value)
        elif dim == "merchant_type":
            m &= (st["chain"].to_numpy()[store]) == (value == "连锁")
        elif dim == "merchant":
            mid = int(me.loc[me["merchant_name"] == value, "merchant_id"].iloc[0])
            m &= st["merchant_id"].to_numpy()[store] == mid
        else:
            raise ValueError(f"unknown dimension {dim}")
    return m


def generate(seed: int, last_week: int, events: list[dict] | None = None) -> dict[str, pd.DataFrame]:
    """Build every table for ISO weeks FIRST_WEEK..last_week.

    events: [{"lever": ..., "segment": {dim: value}, "size": float}], all applied in `last_week`.
      traffic / conversion: size is the relative change of the rate (-0.4 = 40% lower).
      supply: segment {"merchant": name}; all its stores are offline for the week.
      redemption: size < 0, the share of the week's planned redemptions that do not happen.
      refund: size > 0, the share of outstanding vouchers refunded during the week.
    """
    events = events or []
    rng = np.random.default_rng(seed)
    world = static_world(rng)
    st, deals = world["stores"], world["deals"]
    n_weeks = last_week - C.FIRST_WEEK + 1
    n_days = 7 * n_weeks
    days = np.arange(n_days)
    week_ix = days // 7
    dates = [C.START + dt.timedelta(days=int(d)) for d in days]
    t0, t1 = 7 * (n_weeks - 1), n_days - 1  # first and last day of the analysis week
    in_t = (days >= t0)[:, None, None, None]

    # ---- traffic ------------------------------------------------------------------------------
    city_share = np.array([c[2] for c in C.CITIES]); city_share = city_share / city_share.sum()
    cat_share = np.array([c[4] for c in C.CATEGORIES]); cat_share = cat_share / cat_share.sum()
    chan_share = np.array([c[1] for c in C.CHANNELS])
    mix = cat_share[:, None] * chan_share[None, :] * _affinity()
    mix = mix / mix.sum()
    growth = np.array([c[3] for c in C.CITIES])
    trend = (1.0 + growth[None, :]) ** week_ix[:, None]                                   # day x city
    dow_food = np.array([0.86, 0.84, 0.86, 0.90, 1.10, 1.28, 1.16])
    dow_other = np.array([0.80, 0.80, 0.82, 0.86, 1.05, 1.40, 1.27])
    is_food = np.array([c[1] == "餐饮" for c in C.CATEGORIES])
    dow = np.where(is_food[None, :], dow_food[days % 7][:, None], dow_other[days % 7][:, None])  # day x cat
    hol = np.ones(n_days)
    for i, d in enumerate(dates):
        name = C.HOLIDAYS.get(d)
        if name:
            hol[i] = 1.45 if name in ("国庆", "劳动节") else 1.30
        elif dt.date(2026, 10, 8) <= d <= dt.date(2026, 10, 10):
            hol[i] = 0.90
    wk = lambda n, sd: rng.lognormal(0.0, sd, (n_weeks, n))[week_ix]                       # weekly noise, per day
    lam = (DAILY_VISITS * city_share[None, :, None, None] * mix[None, None, :, :] * trend[:, :, None, None]
           * dow[:, None, :, None] * hol[:, None, None, None] * rng.lognormal(0.0, 0.015, n_days)[:, None, None, None]
           * wk(N_CITY, 0.015)[:, :, None, None] * wk(N_CAT, 0.015)[:, None, :, None] * wk(N_CHAN, 0.012)[:, None, None, :])
    conv_cat = np.array([1.25 if c[0] in ("茶饮咖啡", "快餐小吃") else (1.0 if c[1] == "餐饮" else (0.6 if c[0] == "运动健身" else 0.8))
                         for c in C.CATEGORIES])
    conv = (np.array([c[2] for c in C.CHANNELS])[None, None, None, :] * conv_cat[None, None, :, None]
            * rng.lognormal(0.0, 0.05, N_CITY)[None, :, None, None] * np.where(hol > 1, 1.05, 1.0)[:, None, None, None]
            * wk(N_CITY, 0.012)[:, :, None, None] * wk(N_CAT, 0.012)[:, None, :, None])
    subsidy_p = np.broadcast_to(np.array([c[4] for c in C.CHANNELS])[None, None, None, :], lam.shape).copy()
    for ev in events:
        if ev["lever"] == "traffic":
            lam = np.where(in_t & _cells(ev["segment"])[None], lam * (1.0 + ev["size"]), lam)
        elif ev["lever"] == "conversion":
            hit = in_t & _cells(ev["segment"])[None]
            conv = np.where(hit, conv * (1.0 + ev["size"]), conv)
            subsidy_p = np.where(hit, np.clip(subsidy_p * (1.0 + 1.5 * ev["size"]), 0.0, 0.9), subsidy_p)
    visits = rng.poisson(lam)
    orders_n = rng.binomial(visits, np.clip(conv, 0.0, 0.9))

    # ---- orders -------------------------------------------------------------------------------
    flat = np.repeat(np.arange(orders_n.size), orders_n.ravel())
    day, rest = np.divmod(flat, N_CITY * N_CAT * N_CHAN)
    city, rest = np.divmod(rest, N_CAT * N_CHAN)
    cat, chan = np.divmod(rest, N_CHAN)
    n = len(day)
    store = np.empty(n, dtype=np.int64)
    s_open, s_w = st["open_day"].to_numpy(), st["weight"].to_numpy()
    group = city * N_CAT + cat
    order_ix = np.argsort(group, kind="stable")
    bounds = np.searchsorted(group[order_ix], np.arange(N_CITY * N_CAT + 1))
    s_group = (st["city_id"] * N_CAT + st["category_id"]).to_numpy()
    for g in range(N_CITY * N_CAT):
        rows = order_ix[bounds[g]:bounds[g + 1]]
        ids = np.flatnonzero(s_group == g)
        pick = rng.choice(ids, size=len(rows), p=s_w[ids] / s_w[ids].sum())
        early = day[rows] < s_open[pick]          # the store was not open yet: give the order to an old store
        old = ids[s_open[ids] < 0]
        pick[early] = rng.choice(old, size=int(early.sum()), p=s_w[old] / s_w[old].sum())
        store[rows] = pick
    keep = np.ones(n, dtype=bool)
    offline = []
    for ev in events:
        if ev["lever"] == "supply":
            hit = _rows(ev["segment"], city, cat, chan, store, world)
            keep &= ~(hit & (day >= t0))
            mid = int(world["merchants"].loc[world["merchants"]["merchant_name"] == ev["segment"]["merchant"], "merchant_id"].iloc[0])
            for s in st.loc[(st["merchant_id"] == mid) & (st["open_day"] <= t1), "store_id"]:
                offline.append({"store_id": int(s), "offline_from": dates[t0], "offline_to": dates[t1]})
    day, city, cat, chan, store = day[keep], city[keep], cat[keep], chan[keep], store[keep]
    n = len(day)

    d_first = deals.groupby("store_id")["deal_id"].min().to_numpy()
    d_count = deals.groupby("store_id")["deal_id"].count().to_numpy()
    d_w, d_price = deals["w"].to_numpy(), deals["sale_price"].to_numpy()
    # every store has two or three deals; pick one by weight
    f = d_first[store]
    w0, w1 = d_w[f], d_w[f + 1]
    w2 = np.where(d_count[store] == 3, d_w[np.minimum(f + 2, len(d_w) - 1)], 0.0)
    u = rng.random(n) * (w0 + w1 + w2)
    deal = f + (u > w0).astype(np.int64) + (u > w0 + w1).astype(np.int64)
    amount = d_price[deal]
    has_sub = rng.random(n) < subsidy_p[day, city, cat, chan]
    subsidy = np.where(has_sub, np.round(amount * rng.uniform(0.08, 0.20, n), 2), 0.0)

    # ---- buyers -------------------------------------------------------------------------------
    p_new = np.array([c[3] for c in C.CHANNELS])[chan] * (1.0 - 0.15 * day / max(n_days - 1, 1))
    is_new = rng.random(n) < p_new
    user = np.empty(n, dtype=np.int64)
    pool0 = np.maximum(200, (DAILY_VISITS * 0.065 * city_share * 45).astype(int))
    starts = np.concatenate([[0], np.cumsum(pool0)])
    pools = [list(range(int(starts[c]), int(starts[c + 1]))) for c in range(N_CITY)]
    first_day = [-rng.integers(1, 400, int(pool0[c])) for c in range(N_CITY)]
    u_city = [np.full(int(pool0[c]), c) for c in range(N_CITY)]
    next_id = int(starts[-1])
    by_dc = np.lexsort((city, day))
    cut = np.searchsorted((day * N_CITY + city)[by_dc], np.arange(n_days * N_CITY + 1))
    new_first, new_city = [], []
    for g in range(n_days * N_CITY):
        rows = by_dc[cut[g]:cut[g + 1]]
        if not len(rows):
            continue
        c = g % N_CITY
        fresh = is_new[rows]
        user[rows[~fresh]] = np.asarray(pools[c])[rng.integers(0, len(pools[c]), int((~fresh).sum()))]
        k_new = int(fresh.sum())
        ids = np.arange(next_id, next_id + k_new)
        user[rows[fresh]] = ids
        pools[c].extend(ids.tolist())
        new_first.append(np.full(k_new, g // N_CITY)); new_city.append(np.full(k_new, c))
        next_id += k_new

    # ---- redemption and refund ----------------------------------------------------------------
    redeem_p = np.clip(np.array([c[5] for c in C.CATEGORIES])[cat] * st["quality"].to_numpy()[store], 0.0, 0.98)
    redeemed = rng.random(n) < redeem_p
    mean_lag = np.array([c[6] for c in C.CATEGORIES])[cat]
    lag = np.minimum(np.floor(rng.gamma(1.2, mean_lag / 1.2)).astype(int), VALID_DAYS)
    redeem_day = np.where(redeemed, day + lag, -1)
    active = rng.random(n) < 0.55
    refund_day = np.where(redeemed, -1, np.where(active, day + rng.integers(0, 26, n), day + VALID_DAYS + 1))
    refund_type = np.where(active, "主动退", "过期退")
    for ev in events:
        hit = _rows(ev["segment"], city, cat, chan, store, world) if ev["lever"] in ("redemption", "refund") else None
        if ev["lever"] == "redemption":
            late = hit & redeemed & (redeem_day >= t0) & (redeem_day <= t1) & (rng.random(n) < -ev["size"])
            redeem_day = np.where(late, redeem_day + rng.integers(7, 15, n), redeem_day)
        elif ev["lever"] == "refund":
            open_ = (day <= t1) & np.where(redeemed, redeem_day >= t0, refund_day >= t0)
            back = hit & open_ & (rng.random(n) < ev["size"])
            redeemed = redeemed & ~back
            redeem_day = np.where(back, -1, redeem_day)
            refund_day = np.where(back, np.minimum(np.maximum(day, t0 + rng.integers(0, 7, n)), t1), refund_day)
            refund_type = np.where(back, "主动退", refund_type)

    # ---- tables -------------------------------------------------------------------------------
    date_arr = np.array(dates, dtype="datetime64[D]")
    seconds = rng.integers(9 * 3600, 23 * 3600, n)
    order = np.lexsort((seconds, day))
    oid = np.empty(n, dtype=np.int64); oid[order] = np.arange(1, n + 1)
    fact_order = pd.DataFrame({
        "order_id": oid, "user_id": user, "deal_id": deal, "store_id": store,
        "channel": np.array(C.CHANNEL_NAMES)[chan], "pay_date": date_arr[day],
        "pay_time": date_arr[day].astype("datetime64[s]") + seconds.astype("timedelta64[s]"),
        "order_amount": amount, "platform_subsidy": subsidy,
    }).sort_values("order_id", ignore_index=True)
    done = redeemed & (redeem_day <= t1)
    fact_redeem = pd.DataFrame({"order_id": oid[done], "redeem_date": date_arr[redeem_day[done]], "redeem_amount": amount[done]})
    back = (~redeemed) & (refund_day <= t1)
    fact_refund = pd.DataFrame({"order_id": oid[back], "refund_date": date_arr[refund_day[back]],
                                "refund_amount": amount[back], "refund_type": refund_type[back]})
    d_ix, c_ix, k_ix, h_ix = np.indices(visits.shape)
    fact_traffic = pd.DataFrame({"visit_date": date_arr[d_ix.ravel()], "city_id": c_ix.ravel(), "category_id": k_ix.ravel(),
                                 "channel": np.array(C.CHANNEL_NAMES)[h_ix.ravel()], "visit_cnt": visits.ravel()})
    hol_w = C.holiday_weeks()
    dim_date = pd.DataFrame({
        "date": date_arr, "week_id": [C.week_id(C.week_of(d)) for d in dates],
        "week_start": np.array([C.week_start(C.week_of(d)) for d in dates], dtype="datetime64[D]"),
        "day_of_week": [d.isoweekday() for d in dates], "is_holiday": [d in C.HOLIDAYS for d in dates],
        "holiday_name": [C.HOLIDAYS.get(d) for d in dates], "is_holiday_week": [C.week_of(d) in hol_w for d in dates],
    })
    u_first = np.concatenate(first_day + new_first) if new_first else np.concatenate(first_day)
    dim_user = pd.DataFrame({
        "user_id": np.arange(next_id), "city_id": np.concatenate(u_city + new_city) if new_city else np.concatenate(u_city),
        "first_pay_date": (np.datetime64(C.START) + u_first.astype("timedelta64[D]")).astype("datetime64[D]"),
    })
    dim_store = st.loc[st["open_day"] <= t1, ["store_id", "merchant_id", "city_id", "category_id"]].copy()
    dim_store["open_date"] = (np.datetime64(C.START) + st.loc[dim_store.index, "open_day"].to_numpy().astype("timedelta64[D]")).astype("datetime64[D]")
    return {
        "dim_date": dim_date,
        "dim_city": pd.DataFrame({"city_id": range(N_CITY), "city_name": C.CITY_NAMES, "city_tier": [c[1] for c in C.CITIES]}),
        "dim_category": pd.DataFrame({"category_id": range(N_CAT), "category_name": C.CATEGORY_NAMES,
                                      "industry": [c[1] for c in C.CATEGORIES], "commission_rate": [c[3] for c in C.CATEGORIES]}),
        "dim_merchant": world["merchants"][world["merchants"]["merchant_id"].isin(dim_store["merchant_id"])].reset_index(drop=True),
        "dim_store": dim_store.reset_index(drop=True),
        "dim_deal": deals.loc[deals["store_id"].isin(dim_store["store_id"]), ["deal_id", "store_id", "deal_type", "list_price", "sale_price"]].reset_index(drop=True),
        "dim_user": dim_user,
        "fact_traffic_daily": fact_traffic,
        "fact_order": fact_order,
        "fact_redeem": fact_redeem.sort_values("order_id", ignore_index=True),
        "fact_refund": fact_refund.sort_values("order_id", ignore_index=True),
        "fact_store_offline": pd.DataFrame(offline, columns=["store_id", "offline_from", "offline_to"]),
    }
