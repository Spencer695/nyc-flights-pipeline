-- title: Precipitation at the origin airport vs. delays and cancellations
-- note: Each flight is joined to the weather reading for its origin and scheduled hour.
SELECT
    CASE
        WHEN w.precip_in = 0   THEN '1. None'
        WHEN w.precip_in < 0.1 THEN '2. Light (under 0.1 in/hr)'
        ELSE                        '3. Heavy (0.1 in/hr or more)'
    END                                                                      AS precipitation,
    count(*)                                                                 AS flights,
    round(100.0 * count(*) FILTER (WHERE f.status = 'cancelled') / count(*), 1) AS cancelled_pct,
    round(100.0 * count(*) FILTER (WHERE f.dep_delay_min >= 15)
                / count(*) FILTER (WHERE f.status <> 'cancelled'), 1)        AS departed_late_pct,
    round(avg(f.dep_delay_min), 1)                                           AS avg_departure_delay_min
FROM warehouse.fact_flights f
JOIN warehouse.fact_weather_hourly w
  ON w.origin = f.origin
 AND w.observed_hour = date_trunc('hour', f.scheduled_departure)
GROUP BY ALL
ORDER BY precipitation;
