-- One row per scheduled flight that passed every error-level check.
-- Quarantined rows (staging.rejected_rows) never reach this table.

CREATE OR REPLACE TABLE warehouse.fact_flights (
    flight_key           VARCHAR PRIMARY KEY,  -- hash of the natural key (see load.py)
    source_row           BIGINT NOT NULL,      -- lineage: row number in the source file
    flight_date          DATE NOT NULL,
    carrier_code         VARCHAR NOT NULL,
    flight_number        INTEGER NOT NULL,
    tailnum              VARCHAR,
    origin               VARCHAR NOT NULL,
    dest                 VARCHAR NOT NULL,
    scheduled_departure  TIMESTAMP NOT NULL,   -- local time at origin
    sched_dep_hour       INTEGER NOT NULL,
    dep_delay_min        INTEGER,
    arr_delay_min        INTEGER,
    air_time_min         INTEGER,
    distance_mi          INTEGER NOT NULL,
    status               VARCHAR NOT NULL,     -- completed | cancelled | no_arrival
    is_arr_delayed_15    BOOLEAN,              -- DOT definition: arrived 15+ min late; NULL unless completed
    has_weather          BOOLEAN NOT NULL,
    aircraft_in_registry BOOLEAN NOT NULL
);

INSERT INTO warehouse.fact_flights
SELECT
    f.flight_key,
    f.source_row,
    make_date(f.year, f.month, f.day),
    f.carrier,
    f.flight,
    f.tailnum,
    f.origin,
    f.dest,
    make_timestamp(f.year, f.month, f.day, f.sched_dep_time // 100, f.sched_dep_time % 100, 0),
    f.sched_dep_time // 100,
    f.dep_delay::INTEGER,
    f.arr_delay::INTEGER,
    f.air_time::INTEGER,
    f.distance::INTEGER,
    CASE
        WHEN f.dep_time IS NULL  THEN 'cancelled'
        WHEN f.arr_delay IS NULL THEN 'no_arrival'   -- departed, no arrival recorded (e.g. diverted)
        ELSE 'completed'
    END,
    CASE WHEN f.dep_time IS NOT NULL AND f.arr_delay IS NOT NULL THEN f.arr_delay >= 15 END,
    w.origin IS NOT NULL,
    p.tailnum IS NOT NULL
FROM staging.flights f
LEFT JOIN staging.weather w ON w.origin = f.origin AND w.time_hour = f.time_hour
LEFT JOIN staging.planes  p ON p.tailnum = f.tailnum
WHERE f.source_row NOT IN (SELECT source_row FROM staging.rejected_rows);
