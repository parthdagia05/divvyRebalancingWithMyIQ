# 02 — Retrieval (Class 5)

Three retrieval modes are used, each chosen because of how the source behaves.

| Mode | Source | Code | Why this mode |
|---|---|---|---|
| **File** (ZIP → CSV over HTTPS) | Divvy trip history, S3 bucket `divvy-tripdata` | [`src/divvy/ingest_trips.py`](../src/divvy/ingest_trips.py) | The operator publishes history only as monthly archives |
| **API** (JSON over HTTPS, GBFS 2.3) | `gbfs.json` discovery → `station_information`, `station_status`, `system_information` | [`src/divvy/ingest_gbfs.py`](../src/divvy/ingest_gbfs.py) | Capacity and live status exist only in the real-time feed |
| **SQL** (DuckDB) | The local warehouse built from the two sources above | [`src/divvy/load.py`](../src/divvy/load.py), [`sql/`](../sql) | All joins, the model, and the metrics are SQL, so every number is traceable to a query |

## How we know retrieval is complete

### Trip archives (per month)

| Check | How | Failure behaviour |
|---|---|---|
| Month is published | `HEAD` request; 404 means not yet published | `RetrievalError`, run stops |
| Download not truncated | Bytes on disk == server `Content-Length`; written to `.part` and renamed only on a match | Up to 4 retries with backoff, then stop |
| Archive not corrupt | `zipfile.testzip()` CRC check on every member | Stop |
| Exactly one data file | One `.csv` in the archive (macOS `__MACOSX/` resource forks ignored) | Stop |
| Every row reached the warehouse | CSV line count (manifest) == rows loaded into `raw.trips` | Load rolled back, run stops |
| Schema unchanged | The 13 column names match exactly, in order | `SchemaError`, run stops |
| No day missing | Validation rule V04: every calendar day has rides | `ValidationError`, run stops |

Result for this project (from [`data/raw/trips/manifest.json`](../data/raw/trips/manifest.json)):

| Month | Archive bytes | sha256 (prefix) | CSV rows | Rows loaded |
|---|---:|---|---:|---:|
| 2026-06 | 28,468,364 | `34bde1eddace…` | 762,550 | 762,550 |
| 2026-07 | 32,910,286 | `4e6bc8725a92…` | 869,051 | 869,051 |
| 2026-08 | 32,611,393 | `3f4de338ee56…` | 868,191 | 868,191 |
| **Total** | | | **2,499,792** | **2,499,792** |

### GBFS API (per snapshot)

| Check | How | Failure behaviour |
|---|---|---|
| Feeds exist | Every required feed is listed in `gbfs.json` discovery | Stop |
| Data is fresh | `now - last_updated` ≤ 900s for each feed | Snapshot rejected |
| Feeds are consistent | The `station_information` and `station_status` station-id sets are identical; otherwise we caught a mid-refresh | Retried up to 4 times |

## How raw inputs are preserved

| Source | Preserved as | Committed to git? |
|---|---|---|
| Trip archives | The original `.zip` plus the verbatim extracted `.csv` in `data/raw/trips/` | **No** (95MB zipped, and re-downloadable). **The manifest is committed**: url, bytes, ETag, sha256, row count, retrieval time. A rerun re-downloads only when the server copy differs, and logs a warning if a month was republished. |
| GBFS responses | The exact response bytes, gzipped, in `data/snapshots/gbfs/<UTC timestamp>/`, plus `_meta.json` | **Yes**. GBFS returns only "now", so a snapshot can never be fetched again. Each is about 130KB. |
| Warehouse `raw` schema | Every trip column as VARCHAR, plus `source_month`, `source_file`, `source_row` | Rebuilt every run |

Nothing is cleaned at retrieval or load. Type coercion and every correction
happen in validation, where they are counted (see
[03_validation_rules.md](03_validation_rules.md)).

## Findings made during retrieval

* **Files are split by `ended_at`, not `started_at`.** In the August file, 173
  rides started in July. We checked for overlap across all three months:
  **0 duplicate `ride_id`s**. A ride appears only in the file for the month it
  ended, so we assign rides to days by their own timestamps, never by file.
* **The archive includes a macOS resource-fork member** (`__MACOSX/._…csv`).
  Naive "extract the first CSV" code would pick up a 598-byte junk file.
* **GBFS `station_id` is not the trip station id.** The join key is GBFS
  `short_name` (see the source map).
