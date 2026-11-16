-- 收入健康度：佣金收入、货币化率、平台补贴、补贴率、退款金额、退款率、净收入。每周一行。
--
-- 口径
--   佣金收入   核销金额 × 所属品类的佣金率，按核销日入账（核销后才计佣金）。
--   货币化率   佣金收入 ÷ 核销GMV。
--   平台补贴   支付订单里由平台承担的补贴，按支付日入账。
--   补贴率     平台补贴 ÷ 支付GMV。
--   退款金额   按退款日入账，含主动退和过期退。
--   退款率     当周退款金额 ÷ 当周支付GMV（分子分母不是同一批订单）。
--   净收入     佣金收入 − 平台补贴。
WITH paid AS (
    SELECT d.week_id, SUM(o.order_amount) AS gmv, SUM(o.platform_subsidy) AS subsidy
    FROM fact_order o
    JOIN dim_date d ON d.date = o.pay_date
    GROUP BY d.week_id
),
redeemed AS (
    SELECT d.week_id,
           SUM(r.redeem_amount)                     AS redeem_gmv,
           SUM(r.redeem_amount * k.commission_rate) AS commission
    FROM fact_redeem r
    JOIN dim_date     d ON d.date = r.redeem_date
    JOIN fact_order   o ON o.order_id = r.order_id
    JOIN dim_store    s ON s.store_id = o.store_id
    JOIN dim_category k ON k.category_id = s.category_id
    GROUP BY d.week_id
),
refunded AS (
    SELECT d.week_id, SUM(f.refund_amount) AS refund_amt
    FROM fact_refund f
    JOIN dim_date d ON d.date = f.refund_date
    GROUP BY d.week_id
)
SELECT w.week_id,
       ROUND(r.commission, 2)                              AS commission,
       ROUND(100.0 * r.commission / r.redeem_gmv, 2)       AS take_rate,
       ROUND(p.subsidy, 2)                                 AS subsidy,
       ROUND(100.0 * p.subsidy / p.gmv, 2)                 AS subsidy_rate,
       ROUND(COALESCE(f.refund_amt, 0), 2)                 AS refund_amt,
       ROUND(100.0 * COALESCE(f.refund_amt, 0) / p.gmv, 2) AS refund_rate,
       ROUND(r.commission - p.subsidy, 2)                  AS net_revenue
FROM dim_week w
JOIN paid          p ON p.week_id = w.week_id
JOIN redeemed      r ON r.week_id = w.week_id
LEFT JOIN refunded f ON f.week_id = w.week_id
WHERE w.in_report
ORDER BY w.week_id;
