-- 同一批订单的核销情况：按支付周分批，看这批订单支付后 7 天内、30 天内核销了多少，最终退了多少。
--
-- 为什么要有这张表：监控用的核销率是当周核销GMV ÷ 当周支付GMV，分子分母不是同一批订单，
-- 支付放量的那一周它会被拉低。这里固定一批订单往后看，回答的是“这批券到底用掉了多少”。
-- 只有观察期已经走完的批次才有数（7 日核销率要等这一周结束后再过 7 天）。
WITH last_day AS (
    SELECT MAX(date) AS d FROM dim_date
),
orders AS (
    SELECT o.order_id, o.order_amount, o.pay_date, d.week_id AS pay_week, w.week_end,
           r.redeem_date, f.refund_date
    FROM fact_order o
    JOIN dim_date d ON d.date = o.pay_date
    JOIN dim_week w ON w.week_id = d.week_id
    LEFT JOIN fact_redeem r ON r.order_id = o.order_id
    LEFT JOIN fact_refund f ON f.order_id = o.order_id
    WHERE w.in_report
),
cohort AS (
    SELECT pay_week,
           ANY_VALUE(week_end)                                                   AS week_end,
           COUNT(*)                                                              AS orders,
           SUM(order_amount)                                                     AS gmv,
           SUM(order_amount) FILTER (WHERE redeem_date <= pay_date + 7)          AS redeemed_7d,
           SUM(order_amount) FILTER (WHERE redeem_date <= pay_date + 30)         AS redeemed_30d,
           SUM(order_amount) FILTER (WHERE refund_date IS NOT NULL)              AS refunded,
           MEDIAN(redeem_date - pay_date) FILTER (WHERE redeem_date IS NOT NULL) AS median_days_to_redeem
    FROM orders
    GROUP BY pay_week
)
SELECT c.pay_week,
       c.orders,
       ROUND(c.gmv, 2) AS gmv,
       CASE WHEN c.week_end + 7  <= l.d THEN ROUND(100.0 * c.redeemed_7d  / c.gmv, 2) END AS redeem_rate_7d,
       CASE WHEN c.week_end + 30 <= l.d THEN ROUND(100.0 * c.redeemed_30d / c.gmv, 2) END AS redeem_rate_30d,
       CASE WHEN c.week_end + 31 <= l.d THEN ROUND(100.0 * c.refunded     / c.gmv, 2) END AS refund_rate_final,
       c.median_days_to_redeem,
       -- 和上一批比：7 日核销率变了多少个百分点
       CASE WHEN c.week_end + 7 <= l.d
            THEN ROUND(100.0 * c.redeemed_7d / c.gmv
                       - LAG(100.0 * c.redeemed_7d / c.gmv) OVER (ORDER BY c.pay_week), 2) END AS redeem_rate_7d_change
FROM cohort c
CROSS JOIN last_day l
ORDER BY c.pay_week;
