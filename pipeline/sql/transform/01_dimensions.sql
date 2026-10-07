-- Dimension tables: the "who / where / when" that flights point to.

CREATE SCHEMA IF NOT EXISTS warehouse;

CREATE OR REPLACE TABLE warehouse.dim_carrier (
    carrier_code  VARCHAR PRIMARY KEY,
    carrier_name  VARCHAR NOT NULL
);
INSERT INTO warehouse.dim_carrier
SELECT carrier, name FROM staging.airlines;

CREATE OR REPLACE TABLE warehouse.dim_airport (
    airport_code  VARCHAR PRIMARY KEY,
    airport_name  VARCHAR NOT NULL,
    latitude      DOUBLE,
    longitude     DOUBLE,
    altitude_ft   DOUBLE,
    timezone      VARCHAR
);
INSERT INTO warehouse.dim_airport
SELECT faa, name, lat, lon, alt, tzone FROM staging.airports;

CREATE OR REPLACE TABLE warehouse.dim_aircraft (
    tailnum        VARCHAR PRIMARY KEY,
    manufacturer   VARCHAR,
    model          VARCHAR,
    year_built     INTEGER,
    seats          INTEGER,
    engines        INTEGER,
    engine_type    VARCHAR
);
INSERT INTO warehouse.dim_aircraft
SELECT tailnum, manufacturer, model, year, seats, engines, engine FROM staging.planes;

-- One row per calendar day the flights cover.
CREATE OR REPLACE TABLE warehouse.dim_date (
    date_key        DATE PRIMARY KEY,
    year            INTEGER NOT NULL,
    quarter         INTEGER NOT NULL,
    month           INTEGER NOT NULL,
    month_name      VARCHAR NOT NULL,
    day             INTEGER NOT NULL,
    day_name        VARCHAR NOT NULL,
    iso_day_of_week INTEGER NOT NULL,
    is_weekend      BOOLEAN NOT NULL
);
INSERT INTO warehouse.dim_date
SELECT d, year(d), quarter(d), month(d), monthname(d), day(d), dayname(d), isodow(d), isodow(d) >= 6
FROM (
    SELECT unnest(generate_series(min(time_hour)::DATE, max(time_hour)::DATE, INTERVAL 1 DAY))::DATE AS d
    FROM staging.flights
);
