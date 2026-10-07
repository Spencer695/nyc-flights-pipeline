-- title: Delays build through the day
-- note: Departed flights by scheduled departure hour (local time). Hours with 500+ flights.
SELECT
    sched_dep_hour                                              AS scheduled_hour,
    count(*)                                                    AS flights,
    round(avg(dep_delay_min), 1)                                AS avg_departure_delay_min,
    round(100.0 * count(*) FILTER (WHERE dep_delay_min >= 15) / count(*), 1) AS departed_late_pct
FROM warehouse.fact_flights
WHERE status <> 'cancelled'
GROUP BY sched_dep_hour
HAVING count(*) >= 500
ORDER BY scheduled_hour;
