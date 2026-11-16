-- 用户增长：访问次数、支付用户数、新客数、新客订单占比。每周一行，直接从明细表算。
--
-- 口径
--   访问次数       团购商品详情页的访问次数，按访问日所在周。
--   支付用户数     当周至少支付过一笔订单的去重用户数。
--   新客数         当周完成有记录以来第一笔支付的用户数（首单日等于支付日）。
--   新客订单占比   新客数 ÷ 支付订单数（每个新客只算首单那一笔）。
WITH traffic AS (
    SELECT d.week_id, SUM(t.visit_cnt) AS visits
    FROM fact_traffic_daily t
    JOIN dim_date d ON d.date = t.visit_date
    GROUP BY d.week_id
),
paid AS (
    SELECT d.week_id,
           COUNT(*)                                                               AS orders,
           COUNT(DISTINCT o.user_id)                                              AS buyers,
           COUNT(DISTINCT o.user_id) FILTER (WHERE u.first_pay_date = o.pay_date) AS new_buyers
    FROM fact_order o
    JOIN dim_date d ON d.date = o.pay_date
    JOIN dim_user u ON u.user_id = o.user_id
    GROUP BY d.week_id
)
SELECT w.week_id,
       t.visits,
       p.buyers,
       p.new_buyers,
       ROUND(100.0 * p.new_buyers / p.orders, 2) AS new_buyer_share
FROM dim_week w
JOIN traffic t ON t.week_id = w.week_id
JOIN paid    p ON p.week_id = w.week_id
WHERE w.in_report
ORDER BY w.week_id;
