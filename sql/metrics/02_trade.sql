-- 交易效率：支付订单数、支付GMV、访问支付转化率、客单价、核销GMV、核销率。每周一行。
--
-- 口径
--   支付订单数、支付GMV  按支付日入账，不扣退款。GMV 是团购售价，含平台补贴的部分。
--   访问支付转化率       支付订单数 ÷ 访问次数。
--   客单价               支付GMV ÷ 支付订单数。
--   核销GMV              按核销日入账。
--   核销率               当周核销GMV ÷ 当周支付GMV。分子分母不是同一批订单，
--                        同一批订单的核销情况见 05_cohort_redeem.sql。
WITH paid AS (
    SELECT d.week_id, COUNT(*) AS orders, SUM(o.order_amount) AS gmv
    FROM fact_order o
    JOIN dim_date d ON d.date = o.pay_date
    GROUP BY d.week_id
),
redeemed AS (
    SELECT d.week_id, SUM(r.redeem_amount) AS redeem_gmv
    FROM fact_redeem r
    JOIN dim_date d ON d.date = r.redeem_date
    GROUP BY d.week_id
),
traffic AS (
    SELECT d.week_id, SUM(t.visit_cnt) AS visits
    FROM fact_traffic_daily t
    JOIN dim_date d ON d.date = t.visit_date
    GROUP BY d.week_id
)
SELECT w.week_id,
       p.orders,
       ROUND(p.gmv, 2)                                     AS gmv,
       ROUND(100.0 * p.orders / t.visits, 2)               AS conversion_rate,
       ROUND(p.gmv / p.orders, 2)                          AS aov,
       ROUND(COALESCE(r.redeem_gmv, 0), 2)                 AS redeem_gmv,
       ROUND(100.0 * COALESCE(r.redeem_gmv, 0) / p.gmv, 2) AS redeem_rate
FROM dim_week w
JOIN paid          p ON p.week_id = w.week_id
JOIN traffic       t ON t.week_id = w.week_id
LEFT JOIN redeemed r ON r.week_id = w.week_id
WHERE w.in_report
ORDER BY w.week_id;
