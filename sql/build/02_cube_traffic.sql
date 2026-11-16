-- 流量周汇总：周 × 城市 × 品类 × 渠道。访问次数只到这三个维度，没有商家维度。
CREATE OR REPLACE TABLE cube_traffic AS
SELECT
    d.week_id,
    c.city_name      AS city,
    k.category_name  AS category,
    t.channel,
    SUM(t.visit_cnt) AS visits
FROM fact_traffic_daily t
JOIN dim_date     d ON d.date = t.visit_date
JOIN dim_city     c ON c.city_id = t.city_id
JOIN dim_category k ON k.category_id = t.category_id
WHERE d.week_id IN (SELECT week_id FROM dim_week WHERE in_report)
GROUP BY ALL;
