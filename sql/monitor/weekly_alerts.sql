-- 每周监控：这一周（$week）的每个指标，和它前面最近 8 个“非节假日周”比，判定是不是异动。
--
-- 规则（两条同时满足才算异动）：
--   1. 修正 Z 分数的绝对值 >= 3.5。
--      修正 Z = 0.6745 × (本周值 − 基线中位数) / MAD，MAD 是基线各周与中位数之差的绝对值的中位数。
--      用中位数和 MAD 是因为它们不容易被基线里个别异常的周带偏；3.5 是 NIST 手册引用的建议阈值。
--      本周值不放进基线。
--   2. 相对基线中位数的变化 >= 这个指标的最小幅度（metrics.yaml 的 min_change）。
--      数量和金额类指标看相对变化（%），百分比类指标看绝对变化（个百分点）。
--      指标很平稳时 MAD 接近 0，所以给 MAD 设一个下限：最小幅度的十分之一。
-- 九个核心指标（metrics.yaml 里 monitor_slices: true 的）还按城市、品类、渠道、商家类型逐个分组查。
-- 分组的门槛更高，因为分组的日常波动更大、要查的格子也更多：只查占比够大的分组，
-- Z 分数和最小幅度的门槛都抬高（数值在 cfg 里）。
-- 门槛是在 30 个没有埋异动的周上定的（见评测预注册）：总量 0 次误报，分组 2 次误报。
-- 只展示、不判定的指标（monitored = false）不会被标成异动。
WITH cfg AS (
    SELECT 3.5  AS z_total,          -- 总量：修正 Z 分数门槛
           6.0  AS z_slice,          -- 分组：修正 Z 分数门槛
           3.0  AS slice_change_x,   -- 分组：最小幅度是总量的几倍
           0.05 AS slice_min_share   -- 分组：基线期平均占比至少这么大才查
),
base_weeks AS (
    SELECT week_id
    FROM dim_week
    WHERE in_report AND NOT is_holiday_week AND week_id < $week
    ORDER BY week_id DESC
    LIMIT 8
),
prev_week AS (
    SELECT MAX(week_id) AS week_id FROM dim_week WHERE in_report AND week_id < $week
),
base AS (
    SELECT metric, dim, slice,
           MEDIAN(value) AS base_median,
           AVG(share)    AS base_share,
           COUNT(*)      AS base_weeks
    FROM metric_weekly
    WHERE week_id IN (SELECT week_id FROM base_weeks)
    GROUP BY ALL
),
spread AS (
    SELECT m.metric, m.dim, m.slice,
           MEDIAN(ABS(m.value - b.base_median)) AS mad
    FROM metric_weekly m
    JOIN base b USING (metric, dim, slice)
    WHERE m.week_id IN (SELECT week_id FROM base_weeks)
    GROUP BY ALL
),
scored AS (
    SELECT c.metric, c.label, c.grp, c.unit, c.dim, c.slice,
           CASE WHEN c.dim = 'total' THEN c.monitored ELSE c.monitored_slices END AS monitored,
           c.value                                   AS this_week,
           p.value                                   AS last_week,
           b.base_median,
           b.base_weeks,
           b.base_share,
           c.change_kind,
           c.min_change * CASE WHEN c.dim = 'total' THEN 1 ELSE (SELECT slice_change_x FROM cfg) END AS min_change,
           CASE WHEN c.change_kind = 'rel'
                THEN 100.0 * (c.value - b.base_median) / NULLIF(ABS(b.base_median), 0)
                ELSE c.value - b.base_median END     AS change_vs_base,
           CASE WHEN c.change_kind = 'rel'
                THEN 100.0 * (c.value - p.value) / NULLIF(ABS(p.value), 0)
                ELSE c.value - p.value END           AS change_vs_last_week,
           GREATEST(s.mad,
                    CASE WHEN c.change_kind = 'rel'
                         THEN ABS(b.base_median) * c.min_change / 1000.0
                         ELSE c.min_change / 10.0 END) AS mad
    FROM metric_weekly c
    JOIN base   b USING (metric, dim, slice)
    JOIN spread s USING (metric, dim, slice)
    LEFT JOIN metric_weekly p
           ON p.metric = c.metric AND p.dim = c.dim AND p.slice = c.slice
          AND p.week_id = (SELECT week_id FROM prev_week)
    WHERE c.week_id = $week
)
SELECT metric, label, grp, unit, dim, slice,
       ROUND(this_week, 2)            AS this_week,
       ROUND(last_week, 2)            AS last_week,
       ROUND(base_median, 2)          AS base_median,
       ROUND(change_vs_last_week, 2)  AS change_vs_last_week,
       ROUND(change_vs_base, 2)       AS change_vs_base,
       change_kind,
       ROUND(0.6745 * (this_week - base_median) / NULLIF(mad, 0), 2) AS robust_z,
       ROUND(base_share, 4)           AS base_share,
       base_weeks,
       (monitored
        AND base_weeks >= 6
        AND (dim = 'total' OR base_share >= (SELECT slice_min_share FROM cfg))
        AND ABS(0.6745 * (this_week - base_median) / NULLIF(mad, 0))
            >= CASE WHEN dim = 'total' THEN (SELECT z_total FROM cfg) ELSE (SELECT z_slice FROM cfg) END
        AND ABS(change_vs_base) >= min_change) AS is_alert
FROM scored
ORDER BY (dim <> 'total'), is_alert DESC, ABS(0.6745 * (this_week - base_median) / NULLIF(mad, 0)) DESC;
