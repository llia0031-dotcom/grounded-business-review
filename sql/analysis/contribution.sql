-- 加法指标的贡献度：这一周（$week）的支付GMV 和基线比，每个品类贡献了变化的百分之几。
-- 基线是本周之前最近 8 个非节假日周的平均。贡献度 = 这个品类的变化 ÷ 总变化，各品类相加是 100%。
-- 公式出处：Adtributor（Bhagwan 等，NSDI 2014）的 explanatory power。
-- 换指标就换 SUM(gmv)，换维度就换 category。
WITH base_weeks AS (
    SELECT week_id FROM dim_week
    WHERE in_report AND NOT is_holiday_week AND week_id < $week
    ORDER BY week_id DESC LIMIT 8
),
by_slice AS (
    SELECT category,
           COALESCE(SUM(gmv) FILTER (WHERE week_id <> $week), 0) / (SELECT COUNT(*) FROM base_weeks) AS base_value,
           COALESCE(SUM(gmv) FILTER (WHERE week_id = $week), 0)                                      AS this_week
    FROM cube_trade
    WHERE week_id = $week OR week_id IN (SELECT week_id FROM base_weeks)
    GROUP BY category
)
SELECT category,
       ROUND(base_value, 2)                                                        AS base_value,
       ROUND(this_week, 2)                                                         AS this_week,
       ROUND(this_week - base_value, 2)                                            AS change,
       ROUND(100.0 * (this_week - base_value) / base_value, 2)                     AS change_pct,
       ROUND(100.0 * (this_week - base_value) / SUM(this_week - base_value) OVER (), 2) AS contribution_pct,
       ROUND(100.0 * base_value / SUM(base_value) OVER (), 2)                      AS base_share_pct,
       ROUND(100.0 * this_week / SUM(this_week) OVER (), 2)                        AS this_week_share_pct
FROM by_slice
ORDER BY ABS(this_week - base_value) DESC;
