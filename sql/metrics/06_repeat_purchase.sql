-- 复购：按支付周分批，看这一周下过单的用户里，有多少人在这一周结束后的 28 天内又下了一单。
-- 只统计观察期已经走完的批次（这一周结束后再过 28 天）。
WITH last_day AS (
    SELECT MAX(date) AS d FROM dim_date
),
user_orders AS (
    SELECT o.user_id, o.pay_date, d.week_id AS pay_week,
           LEAD(o.pay_date) OVER (PARTITION BY o.user_id ORDER BY o.pay_time, o.order_id) AS next_pay_date
    FROM fact_order o
    JOIN dim_date d ON d.date = o.pay_date
),
buyer_week AS (
    -- 一个用户一周一行：这一周最后一单之后，下一单是哪天
    SELECT pay_week, user_id, MAX(next_pay_date) AS next_pay_date
    FROM user_orders
    GROUP BY pay_week, user_id
)
SELECT b.pay_week,
       COUNT(*)                                                           AS buyers,
       COUNT(*) FILTER (WHERE b.next_pay_date > w.week_end
                          AND b.next_pay_date <= w.week_end + 28)         AS repeat_buyers_28d,
       ROUND(100.0 * COUNT(*) FILTER (WHERE b.next_pay_date > w.week_end
                                        AND b.next_pay_date <= w.week_end + 28) / COUNT(*), 2) AS repeat_rate_28d
FROM buyer_week b
JOIN dim_week w ON w.week_id = b.pay_week
CROSS JOIN last_day l
WHERE w.in_report AND w.week_end + 28 <= l.d
GROUP BY b.pay_week
ORDER BY b.pay_week;
