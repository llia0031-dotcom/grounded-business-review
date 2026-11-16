-- 交易周汇总：周 × 城市 × 品类 × 渠道 × 商家类型 × 商家。
-- 三类事件各按自己发生的那一周入账：支付按支付日，核销按核销日，退款按退款日。
-- 所以同一行里的“核销金额”和“支付金额”不一定来自同一批订单（见 metrics/metrics.yaml 里核销率的口径）。
CREATE OR REPLACE TABLE cube_trade AS
WITH paid AS (
    SELECT pay_week AS week_id, city, category, channel, merchant_type, merchant,
           COUNT(*)                                   AS orders,
           SUM(order_amount)                          AS gmv,
           SUM(platform_subsidy)                      AS subsidy,
           COUNT(*) FILTER (WHERE is_first_order)     AS new_orders,
           0 AS redeem_orders, 0.0 AS redeem_gmv, 0.0 AS commission,
           0 AS refund_orders, 0.0 AS refund_amt
    FROM order_wide
    GROUP BY ALL
),
redeemed AS (
    SELECT d.week_id, o.city, o.category, o.channel, o.merchant_type, o.merchant,
           0, 0.0, 0.0, 0,
           COUNT(*), SUM(r.redeem_amount), SUM(r.redeem_amount * o.commission_rate),
           0, 0.0
    FROM fact_redeem r
    JOIN order_wide o ON o.order_id = r.order_id
    JOIN dim_date   d ON d.date = r.redeem_date
    GROUP BY ALL
),
refunded AS (
    SELECT d.week_id, o.city, o.category, o.channel, o.merchant_type, o.merchant,
           0, 0.0, 0.0, 0,
           0, 0.0, 0.0,
           COUNT(*), SUM(f.refund_amount)
    FROM fact_refund f
    JOIN order_wide o ON o.order_id = f.order_id
    JOIN dim_date   d ON d.date = f.refund_date
    GROUP BY ALL
)
SELECT week_id, city, category, channel, merchant_type, merchant,
       SUM(orders)::BIGINT          AS orders,
       SUM(gmv)                     AS gmv,
       SUM(subsidy)                 AS subsidy,
       SUM(new_orders)::BIGINT      AS new_orders,
       SUM(redeem_orders)::BIGINT   AS redeem_orders,
       SUM(redeem_gmv)              AS redeem_gmv,
       SUM(commission)              AS commission,
       SUM(refund_orders)::BIGINT   AS refund_orders,
       SUM(refund_amt)              AS refund_amt
FROM (SELECT * FROM paid UNION ALL SELECT * FROM redeemed UNION ALL SELECT * FROM refunded)
WHERE week_id IN (SELECT week_id FROM dim_week WHERE in_report)
GROUP BY ALL;
