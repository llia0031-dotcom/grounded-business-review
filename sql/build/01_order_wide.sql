-- 订单宽表：一行一笔已支付的团购订单，带上门店、商家、城市、品类和支付所在周。
-- is_first_order：这笔订单是不是该用户有记录以来的第一笔（新客首单）。
CREATE OR REPLACE TABLE order_wide AS
SELECT
    o.order_id,
    o.user_id,
    o.deal_id,
    o.store_id,
    s.merchant_id,
    m.merchant_name      AS merchant,
    m.merchant_type,
    c.city_name          AS city,
    c.city_tier,
    k.category_name      AS category,
    k.industry,
    k.commission_rate,
    o.channel,
    o.pay_date,
    o.pay_time,
    d.week_id            AS pay_week,
    o.order_amount,
    o.platform_subsidy,
    (u.first_pay_date = o.pay_date
     AND ROW_NUMBER() OVER (PARTITION BY o.user_id ORDER BY o.pay_time, o.order_id) = 1) AS is_first_order
FROM fact_order o
JOIN dim_date     d ON d.date = o.pay_date
JOIN dim_store    s ON s.store_id = o.store_id
JOIN dim_merchant m ON m.merchant_id = s.merchant_id
JOIN dim_city     c ON c.city_id = s.city_id
JOIN dim_category k ON k.category_id = s.category_id
JOIN dim_user     u ON u.user_id = o.user_id;
