-- title: On-time performance by airline
-- note: "Late" uses the DOT definition: arrived 15+ minutes after schedule. Airlines with 1,000+ flights.
SELECT
    c.carrier_name                                                        AS airline,
    count(*)                                                              AS flights,
    round(100.0 * count(*) FILTER (WHERE f.status = 'cancelled') / count(*), 1) AS cancelled_pct,
    round(100.0 * count(*) FILTER (WHERE f.is_arr_delayed_15)
                / count(*) FILTER (WHERE f.status = 'completed'), 1)      AS arrived_late_pct,
    round(avg(f.arr_delay_min), 1)                                        AS avg_arrival_delay_min
FROM warehouse.fact_flights f
JOIN warehouse.dim_carrier c USING (carrier_code)
GROUP BY ALL
HAVING count(*) >= 1000
ORDER BY arrived_late_pct;
