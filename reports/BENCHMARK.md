# Query Benchmark

4,353,510 rows (10x the warehouse fact table). Median of 5 warm runs, in milliseconds. Machine: Linux x86_64, 2 CPUs, Python 3.13.16, DuckDB 1.5.6, SQLite 3.45.1. All five setups returned identical results for every query.

| query | sqlite | sqlite_indexed | duckdb_table | duckdb_csv | duckdb_parquet | fastest |
|---|---|---|---|---|---|---|
| Q1 lookup: one airline, one day | 246 | 0.24 | 5.18 | 791 | 6.60 | sqlite_indexed (1,045x vs. sqlite) |
| Q2 full aggregate: delay by airline and month | 2,815 | 2,768 | 58 | 875 | 78 | duckdb_table (48x vs. sqlite) |
| Q3 filtered aggregate: JFK in July by hour | 379 | 61 | 12 | 766 | 17 | duckdb_table (31x vs. sqlite) |

## Why the numbers look like this

- **Q1 (selective lookup):** without an index SQLite reads every row. The composite index turns that into a direct seek. DuckDB has no B-tree index here but skips most of the file using per-block min/max stats.
- **Q2 (scan everything, few columns):** a column store reads only the 4 columns the query needs and aggregates them in parallel; a row store reads every column of every row. Indexes cannot help a full scan.
- **Q3 (filter + aggregate):** the index narrows SQLite to one airport-month, and DuckDB's column pruning plus block skipping does the same without one.
- **CSV vs. Parquet:** the same engine is much slower on CSV because it has to parse text for every query, while Parquet is typed, compressed, and columnar.

## SQLite query plan for Q1

- Before indexing: `SCAN bench; USE TEMP B-TREE FOR ORDER BY`
- After indexing: `SEARCH bench USING INDEX idx_carrier_date (carrier_code=? AND flight_date=?); USE TEMP B-TREE FOR ORDER BY`
- Building both indexes took 5.5 s.

## Storage size

| format | size_mb |
|---|---|
| CSV | 423.1 |
| Parquet | 181.6 |
| DuckDB | 148.9 |
| SQLite (with indexes) | 625.2 |

Benchmark inputs took 27 s to build.
