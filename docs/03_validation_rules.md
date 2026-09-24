# 03 — Profiling & Validation (Class 6)

The principle: **record, don't repair.** No row is deleted and no value is
overwritten. Every rule has a business reason, a severity, and an explicit
action. Results for each run go to `audit.validation_results` and
[`outputs/validation_report.csv`](../outputs/validation_report.csv).

| Severity | Meaning | Where the row ends up |
|---|---|---|
| **BLOCK** | The input as a whole can't be trusted | Nowhere. The run stops, the previous outputs stay live, and the exit code is 2 |
| **REJECT** | The row is unusable | `staging.trips_rejected`, with a `reject_reason` |
| **FLAG** | The row is real but needs a decision | `staging.trips`, with a boolean `f_*` column; the model applies the decision |

## Profile of the raw data (Jun–Aug 2026, 2,499,792 rides)

| Dimension | Finding |
|---|---|
| Volume | 762,550 / 869,051 / 868,191 rides per month; every day present |
| Rider mix (Aug) | 60% member, 40% casual |
| Bike mix (Aug) | 74% electric, 26% classic |
| Ride duration | Median about 10 min; 2.5% under 60s; 0.09% over 24h; 0 negative |
| Station attribution | 21.5% of rides have **no start station** and 22.4% have **no end station**, almost all e-bikes |
| Station ids | 1,727 distinct trip station ids; 17 appear under more than one name |
| Coordinates | Rounded to 2 decimals (about 1km) for many e-bike rides, so they can't be snapped to a station |
| GBFS inventory | 2,059 stations: 1,162 docking stations with a `short_name`, and 897 public racks or corrals without one |
| Hourly shape | Lowest demand 03:00–04:59; peak 17:00. This supports the 04:00 operational-day boundary |

## Rules

| ID | Rule | Business reason | Severity | 3-month result | Action |
|---|---|---|---|---|---|
| V01 | Timestamps parse | A ride without a time can't be placed in the day | BLOCK > 0.1% | 0 | Stop run |
| V02 | `ride_id` unique across months | Month files could overlap at boundaries; double counting inflates flow | BLOCK > 1% | 0 | Keep the first occurrence, quarantine the rest |
| V03 | Each file holds only rides that **end** in its month | Our partition assumption. If false, the boundary handling is wrong | BLOCK > 1% | 0 | Stop run |
| V04 | Every calendar day has rides | A missing day means an incomplete export; the KPI would look better than reality | BLOCK < 100% | 92/92 days | Stop run |
| V05 | Dock-named trip stations resolve to a GBFS `short_name` | Without capacity a station drops out of the KPI silently | BLOCK < 90% | 99.41% resolve | Stop run |
| G01 | GBFS `short_name` unique | The join would fan out and duplicate flow | BLOCK > 0 | 0 | Stop run |
| R01 | `ended_at ≥ started_at` | A negative duration is a system error | REJECT | 0 | Quarantine |
| F01 | Ride shorter than 60s | Divvy's own documentation says these are removed. **They are not: 63,490 rides (2.5%).** Most are same-dock re-docks: the rider undocks and immediately returns | FLAG | 63,490 (2.54%) | Excluded from station flow; counted |
| F02 | Ride longer than 24h | Usually a lost bike recovered later | FLAG | 2,129 (0.09%) | Kept. The departure and arrival did happen |
| F03 | No end station and no end coordinates | Bike never returned, or return not reconciled | FLAG | 2,182 (0.09%) | Departure counts; no arrival |
| F04 | Starts or ends at an operator facility (warehouse, staff, test) | Operator moves are not customer demand | FLAG | 19 | Excluded from station flow |
| F05 | Coordinates outside the Chicago service area | GPS error or a bike taken out of the area | FLAG | 20 | Kept, counted (station ids are used, not coordinates) |
| F06 / F07 | `rideable_type` / `member_casual` outside the known domain | A new bike type or rider class would need its own treatment | FLAG | 0 / 0 | Kept, counted |
| F08 / F09 | No start / end station | Dockless e-bike use is invisible to station balance | FLAG | 21.5% / 22.4% | Out of KPI scope; **reported as M5 coverage** |
| F10 | Station id has multiple names | Renames and spelling drift. Grouping by name would split one station in two | FLAG | 17 ids | Identify stations by id only; canonical name from GBFS |
| G02 | Docking station capacity ≤ 0 | Capacity is the KPI's denominator | FLAG | 0 | Excluded from KPI |
| G03 | Live bikes + docks exceed stated capacity | Capacity is approximate (for example, valet overflow) | FLAG | 0.29% of status rows | Assumption A2 |
| G04 | Docking station status not reported for over 1h | A stale status would count as empty or full when it isn't | FLAG | 0.86% | Excluded from the observed empty/full rate |

## Decisions we made explicitly rather than silently

1. **Public racks and corrals are out of KPI scope, but not deleted.** They
   have nominal capacities of 1–3, or 50 for a corral, and they are not
   restocked like docks. Including them would inflate M1 with meaningless
   capacity breaches. Their demand stays visible through M5.
2. **Sub-60s rides are excluded from flow, not from the data.** A same-dock
   re-dock nets to zero anyway. A sub-60s ride between two different docks is
   physically implausible, and counting it would move a bike that never moved.
3. **Station identity is the id, never the name.** Examples: `CHI00752` is both
   "Sheridan Rd & Loyola Ave" and "Sheridan Rd & Arthur Ave", and `CHI02366` is
   both "North Ave Beach" and "North Avenue Beach".
4. **Blocking thresholds are in config** ([`config/pipeline.yaml`](../config/pipeline.yaml)),
   not in code. The client can tighten them without a code change.

## Tests

[`tests/test_pipeline.py`](../tests/test_pipeline.py) builds a synthetic
month containing each defect. It asserts that each defect is rejected, flagged,
or blocking as documented above, and that a blocking failure leaves the
previous outputs untouched.
