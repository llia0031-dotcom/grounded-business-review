-- 门店周汇总：周 × 城市 × 品类 × 商家类型 × 商家。
-- 在线门店：已开业，且这一周没有整周下线。动销门店：这一周至少有一笔支付订单。新上线门店：开业日在这一周。
CREATE OR REPLACE TABLE cube_store AS
WITH weeks AS (
    SELECT week_id, week_start AS wk_start, week_end AS wk_end
    FROM dim_week
    WHERE in_report
),
store_week AS (
    SELECT w.week_id, s.store_id,
           c.city_name AS city, k.category_name AS category, m.merchant_type, m.merchant_name AS merchant,
           (s.open_date BETWEEN w.wk_start AND w.wk_end) AS is_new,
           NOT EXISTS (
               SELECT 1 FROM fact_store_offline f
               WHERE f.store_id = s.store_id AND f.offline_from <= w.wk_start AND f.offline_to >= w.wk_end
           ) AS is_online
    FROM weeks w
    JOIN dim_store    s ON s.open_date <= w.wk_end
    JOIN dim_merchant m ON m.merchant_id = s.merchant_id
    JOIN dim_city     c ON c.city_id = s.city_id
    JOIN dim_category k ON k.category_id = s.category_id
),
active AS (
    SELECT DISTINCT pay_week AS week_id, store_id FROM order_wide
)
SELECT sw.week_id, sw.city, sw.category, sw.merchant_type, sw.merchant,
       COUNT(*) FILTER (WHERE sw.is_online)  AS online_stores,
       COUNT(a.store_id)                     AS active_stores,
       COUNT(*) FILTER (WHERE sw.is_new)     AS new_stores
FROM store_week sw
LEFT JOIN active a ON a.week_id = sw.week_id AND a.store_id = sw.store_id
GROUP BY ALL;
