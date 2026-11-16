-- 商家运营：在线门店数、动销门店数、动销率、店均GMV、新上线门店数。每周一行。
--
-- 口径
--   在线门店数     已开业（开业日不晚于这一周的周日），并且这一周没有整周下线。
--   动销门店数     当周至少有一笔支付订单的门店。
--   动销率         动销门店数 ÷ 在线门店数。
--   店均GMV        支付GMV ÷ 动销门店数。
--   新上线门店数   开业日落在当周。
WITH store_week AS (
    SELECT w.week_id, s.store_id,
           (s.open_date BETWEEN w.week_start AND w.week_end) AS is_new,
           NOT EXISTS (
               SELECT 1 FROM fact_store_offline f
               WHERE f.store_id = s.store_id
                 AND f.offline_from <= w.week_start AND f.offline_to >= w.week_end
           ) AS is_online
    FROM dim_week w
    JOIN dim_store s ON s.open_date <= w.week_end
    WHERE w.in_report
),
sales AS (
    SELECT d.week_id, o.store_id, SUM(o.order_amount) AS gmv
    FROM fact_order o
    JOIN dim_date d ON d.date = o.pay_date
    GROUP BY d.week_id, o.store_id
)
SELECT sw.week_id,
       COUNT(*) FILTER (WHERE sw.is_online)                                       AS online_stores,
       COUNT(s.store_id)                                                          AS active_stores,
       ROUND(100.0 * COUNT(s.store_id) / COUNT(*) FILTER (WHERE sw.is_online), 2) AS active_store_rate,
       ROUND(SUM(s.gmv) / COUNT(s.store_id), 2)                                   AS gmv_per_active_store,
       COUNT(*) FILTER (WHERE sw.is_new)                                          AS new_stores
FROM store_week sw
LEFT JOIN sales s ON s.week_id = sw.week_id AND s.store_id = sw.store_id
GROUP BY sw.week_id
ORDER BY sw.week_id;
