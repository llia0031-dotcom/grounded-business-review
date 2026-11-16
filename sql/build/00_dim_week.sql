-- 周表：每周一行。
-- is_holiday_week：这一周含法定节假日。
-- in_report：进不进周汇总。核销和退款按发生日入账，数据抽取开始后的前 6 周里，
-- 它们对应的订单有一部分是抽取开始之前支付的，数字不完整，所以周汇总从 2026-05-11 那一周开始。
CREATE OR REPLACE TABLE dim_week AS
SELECT week_id,
       MIN(date)                                AS week_start,
       MAX(date)                                AS week_end,
       BOOL_OR(is_holiday)                      AS is_holiday_week,
       STRING_AGG(DISTINCT holiday_name, '、')   AS holiday_names,
       MIN(date) >= DATE '2026-05-11'           AS in_report
FROM dim_date
GROUP BY week_id;
