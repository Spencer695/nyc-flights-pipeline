# NYC 2023 Flights: Pipeline Report

Generated from run `f41d4240bed2` (finished 2026-10-07 21:03 UTC). Warehouse holds 435,351 flights.

## Pipeline stages

| stage | status | duration_ms | rows_in | rows_out |
|---|---|---|---|---|
| extract | succeeded | 4,544 |  | 467,668 |
| load | succeeded | 924 |  | 467,668 |
| quality | succeeded | 236 | 435,352 | 435,351 |
| transform | succeeded | 1,850 | 435,351 | 435,351 |

## Data-quality results

Error checks quarantine failing rows; warnings keep the row and record the count.

| check | severity | rows_flagged | description |
|---|---|---|---|
| airports_exist | error | 0 | Origin and destination exist in the airports table |
| carrier_exists | error | 0 | Carrier code exists in the airlines table |
| dep_delay_consistent | error | 1 | Scheduled departure + reported delay lands on the actual departure clock time (time-zone aware, so overnight delays across a daylight-saving change are handled) |
| flight_key_not_null | error | 0 | Every flight has a date, carrier, flight number, origin, destination, and scheduled departure |
| flight_key_unique | error | 0 | No two rows describe the same scheduled flight (the first is kept, later copies quarantined) |
| reconcile_daily_rollup | error | 0 | Daily rollup flight counts add up to the fact table |
| reconcile_distance | error | 0 | Total miles in staging (excluding quarantined rows) equal total miles in the warehouse |
| reconcile_row_counts | error | 0 | staged (435,352) = warehouse (435,351) + quarantined (1) |
| valid_calendar_date | error | 0 | year/month/day form a real calendar date |
| valid_ranges | error | 0 | Clock times are real HHMM times; distance and air time are positive |
| aircraft_in_registry | warn | 9,371 | Tail number appears in the aircraft registry (planes table) |
| delay_under_24h | warn | 9 | Departure delay is under 24 hours (longer ones are real but rare) |
| tailnum_present_if_departed | warn | 0 | Flights that departed have a tail number |
| volume_vs_last_run | warn | 0 | Flight row count within 10% of the last successful run |
| weather_for_flight_hour | warn | 826 | An hourly weather reading exists for the flight's origin and scheduled hour |
| weather_hours_complete | warn | 73 | Each origin airport has a weather reading for every hour in the flight date range |

## On-time performance by airline

"Late" uses the DOT definition: arrived 15+ minutes after schedule. Airlines with 1,000+ flights.

| airline | flights | cancelled_pct | arrived_late_pct | avg_arrival_delay_min |
|---|---|---|---|---|
| Republic Airline | 88,785 | 3.4 | 13.7 | -4.6 |
| Endeavor Air Inc. | 54,141 | 3.3 | 16.2 | -2.2 |
| Delta Air Lines Inc. | 61,562 | 1.5 | 19.7 | 1.6 |
| American Airlines Inc. | 40,525 | 1.4 | 20.3 | 5.3 |
| Alaska Airlines Inc. | 7,843 | 0.9 | 20.4 | 0.1 |
| Southwest Airlines Co. | 12,385 | 2.3 | 22.9 | 5.8 |
| SkyWest Airlines Inc. | 6,432 | 3.4 | 24.0 | 13.7 |
| United Air Lines Inc. | 79,641 | 2.3 | 24.9 | 9.0 |
| Spirit Air Lines | 15,189 | 2.4 | 25.6 | 9.9 |
| JetBlue Airways | 66,168 | 2.3 | 31.4 | 15.6 |
| Frontier Airlines Inc. | 1,286 | 5.3 | 37.6 | 26.2 |

## Delays build through the day

Departed flights by scheduled departure hour (local time). Hours with 500+ flights.

| scheduled_hour | flights | avg_departure_delay_min | departed_late_pct |
|---|---|---|---|
| 5 | 3,218 | 6.5 | 10.5 |
| 6 | 28,448 | 3.4 | 8.9 |
| 7 | 29,481 | 5.0 | 11.0 |
| 8 | 28,671 | 6.2 | 13.1 |
| 9 | 31,227 | 7.2 | 14.3 |
| 10 | 23,455 | 8.2 | 16.4 |
| 11 | 25,766 | 8.2 | 15.5 |
| 12 | 21,131 | 10.0 | 17.5 |
| 13 | 23,911 | 13.4 | 20.8 |
| 14 | 26,126 | 15.8 | 23.5 |
| 15 | 28,550 | 17.1 | 25.4 |
| 16 | 25,714 | 17.5 | 26.0 |
| 17 | 26,847 | 21.4 | 29.2 |
| 18 | 25,103 | 21.0 | 29.8 |
| 19 | 26,531 | 24.0 | 32.5 |
| 20 | 23,524 | 23.7 | 33.4 |
| 21 | 19,752 | 21.9 | 31.7 |
| 22 | 6,893 | 26.9 | 35.8 |

## Precipitation at the origin airport vs. delays and cancellations

Each flight is joined to the weather reading for its origin and scheduled hour.

| precipitation | flights | cancelled_pct | departed_late_pct | avg_departure_delay_min |
|---|---|---|---|---|
| 1. None | 378,287 | 2.0 | 20.4 | 12.6 |
| 2. Light (under 0.1 in/hr) | 55,480 | 5.4 | 29.9 | 22.5 |
| 3. Heavy (0.1 in/hr or more) | 758 | 12.1 | 41.6 | 42.5 |

## Monthly trend (from the daily rollup table)

Reads warehouse.agg_daily_carrier instead of the fact table, as a dashboard would.

| month | flights | cancelled_pct | arrived_late_pct |
|---|---|---|---|
| January | 36,020 | 1.4 | 21.5 |
| February | 34,761 | 1.6 | 22.3 |
| March | 39,513 | 1.5 | 26.1 |
| April | 37,476 | 2.8 | 25.0 |
| May | 38,710 | 0.4 | 14.2 |
| June | 35,921 | 6.0 | 28.1 |
| July | 36,211 | 8.7 | 35.8 |
| August | 36,765 | 1.7 | 21.0 |
| September | 35,505 | 5.0 | 22.8 |
| October | 36,586 | 0.1 | 13.4 |
| November | 34,521 | 0.1 | 11.9 |
| December | 33,362 | 0.5 | 14.4 |
