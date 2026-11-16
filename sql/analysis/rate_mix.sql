-- 比率指标的拆解：这一周（$week）的核销率和基线比，变化里有多少是“各品类自己的核销率变了”（率效应），
-- 有多少是“各品类的支付占比变了”（结构效应）。
--
--   率效应   = (基线占比 + 本周占比) / 2 × (本周率 − 基线率)
--   结构效应 = (本周占比 − 基线占比) × [ (基线率 + 本周率) / 2 − (总基线率 + 总本周率) / 2 ]
--   各品类的（率效应 + 结构效应）相加，正好等于总核销率的变化，没有剩余项。
-- 用两期占比的平均当权重，出处：Kitagawa 1955；Das Gupta 1993《Standardization and Decomposition of Rates》。
-- 这样拆是为了不被占比变化骗到：各品类的率都没变，总的率也可能因为占比变了而变（辛普森悖论）。
WITH base_weeks AS (
    SELECT week_id FROM dim_week
    WHERE in_report AND NOT is_holiday_week AND week_id < $week
    ORDER BY week_id DESC LIMIT 8
),
by_slice AS (
    SELECT category,
           COALESCE(SUM(redeem_gmv) FILTER (WHERE week_id <> $week), 0) / (SELECT COUNT(*) FROM base_weeks) AS num0,
           COALESCE(SUM(gmv)        FILTER (WHERE week_id <> $week), 0) / (SELECT COUNT(*) FROM base_weeks) AS den0,
           COALESCE(SUM(redeem_gmv) FILTER (WHERE week_id = $week), 0)                                      AS num1,
           COALESCE(SUM(gmv)        FILTER (WHERE week_id = $week), 0)                                      AS den1
    FROM cube_trade
    WHERE week_id = $week OR week_id IN (SELECT week_id FROM base_weeks)
    GROUP BY category
),
rates AS (
    SELECT category,
           100.0 * num0 / den0                           AS r0,
           100.0 * num1 / den1                           AS r1,
           den0 / SUM(den0) OVER ()                      AS w0,
           den1 / SUM(den1) OVER ()                      AS w1,
           100.0 * SUM(num0) OVER () / SUM(den0) OVER () AS total_r0,
           100.0 * SUM(num1) OVER () / SUM(den1) OVER () AS total_r1
    FROM by_slice
)
SELECT category,
       ROUND(r0, 2)                                                        AS base_rate,
       ROUND(r1, 2)                                                        AS this_week_rate,
       ROUND(r1 - r0, 2)                                                   AS rate_change_pp,
       ROUND((w0 + w1) / 2 * (r1 - r0), 2)                                 AS rate_effect,
       ROUND((w1 - w0) * ((r0 + r1) / 2 - (total_r0 + total_r1) / 2), 2)   AS mix_effect,
       ROUND((w0 + w1) / 2 * (r1 - r0)
             + (w1 - w0) * ((r0 + r1) / 2 - (total_r0 + total_r1) / 2), 2) AS effect,
       ROUND(100.0 * w0, 2)                                                AS base_share_pct,
       ROUND(100.0 * w1, 2)                                                AS this_week_share_pct,
       ROUND(total_r1 - total_r0, 2)                                       AS total_change_pp
FROM rates
ORDER BY ABS((w0 + w1) / 2 * (r1 - r0) + (w1 - w0) * ((r0 + r1) / 2 - (total_r0 + total_r1) / 2)) DESC;
