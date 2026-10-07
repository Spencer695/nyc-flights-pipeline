-- title: Monthly trend (from the daily rollup table)
-- note: Reads warehouse.agg_daily_carrier instead of the fact table, as a dashboard would.
SELECT
    monthname(flight_date)                                                   AS month,
    sum(scheduled_flights)                                                   AS flights,
    round(100.0 * sum(cancelled_flights) / sum(scheduled_flights), 1)        AS cancelled_pct,
    round(100.0 * sum(delayed_15_flights) / sum(completed_flights), 1)       AS arrived_late_pct
FROM warehouse.agg_daily_carrier
GROUP BY month(flight_date), monthname(flight_date)
ORDER BY month(flight_date);
