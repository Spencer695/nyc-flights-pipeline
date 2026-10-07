-- One weather reading per origin airport per hour, renamed with units.

CREATE OR REPLACE TABLE warehouse.fact_weather_hourly (
    origin          VARCHAR NOT NULL,
    observed_hour   TIMESTAMP NOT NULL,   -- local time at the origin airport
    temp_f          DOUBLE,
    dewpoint_f      DOUBLE,
    humidity_pct    DOUBLE,
    wind_dir_deg    DOUBLE,
    wind_speed_mph  DOUBLE,
    wind_gust_mph   DOUBLE,
    precip_in       DOUBLE,
    pressure_mb     DOUBLE,
    visibility_mi   DOUBLE,
    PRIMARY KEY (origin, observed_hour)
);
INSERT INTO warehouse.fact_weather_hourly
SELECT origin, time_hour, temp, dewp, humid, wind_dir, wind_speed, wind_gust, precip, pressure, visib
FROM staging.weather;
