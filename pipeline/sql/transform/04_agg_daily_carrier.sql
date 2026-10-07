-- Daily reporting rollup: one row per date x carrier x origin.
-- Dashboards read this small table instead of scanning every flight.

CREATE OR REPLACE TABLE warehouse.agg_daily_carrier AS
SELECT
    flight_date,
    carrier_code,
    origin,
    count(*)                                         AS scheduled_flights,
    count(*) FILTER (WHERE status = 'cancelled')     AS cancelled_flights,
    count(*) FILTER (WHERE status = 'completed')     AS completed_flights,
    count(*) FILTER (WHERE is_arr_delayed_15)        AS delayed_15_flights,
    avg(dep_delay_min)                               AS avg_dep_delay_min,
    avg(arr_delay_min)                               AS avg_arr_delay_min,
    sum(distance_mi) FILTER (WHERE status <> 'cancelled') AS miles_flown
FROM warehouse.fact_flights
GROUP BY ALL;
