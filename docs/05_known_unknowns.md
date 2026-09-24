# 05 — Known / Unknown / Assumption / Limitation

## Known (measured, with evidence in this repo)

| # | Statement | Evidence |
|---|---|---|
| K1 | Retrieval is complete: 2,499,792 rides across 92 of 92 days, every row reconciled from CSV to warehouse | `data/raw/trips/manifest.json`, rules V01–V04 |
| K2 | About **1.6%** of active docking-station-days need an intervention by construction (M1), and the rate is stable month to month (1.49 / 1.78 / 1.66%) | `outputs/metrics_summary.csv` |
| K3 | At least **115 bikes a day** must be moved to keep docking stations usable (M2), with more on weekends (153/day) than weekdays (100/day) | `outputs/daily_kpi.csv` |
| K4 | The work is highly concentrated: the top 50 stations account for **93%** of required moves (M3) | `outputs/metrics_summary.csv` |
| K5 | Most of the problem is **docks filling up** (lakefront and Loop destinations in the evening), not stations running dry | `dominant_pressure` in `outputs/rebalancing_targets.csv` |
| K6 | About **38%** of rides are invisible to station balance because they start or end off-dock (M5 = 61.7%) | Rules F08 and F09 |
| K7 | Divvy's published claim that rides under 60s are removed is not true for this data (63,490 remain) | Rule F01 |

## Unknown (would change the answer, and we cannot see it)

| # | Question | Why it matters | Who could answer |
|---|---|---|---|
| U1 | When and where did trucks and valets actually move bikes? | We can only compute the minimum moves required, not compare that with the moves made | Divvy / Lyft Field Ops |
| U2 | What was each station's capacity *during* Jun–Aug? | We use today's GBFS capacity | Lyft Station Planning |
| U3 | How many riders arrived at an empty or full station and gave up? | Demand turned away never shows up as a ride, so the model under-counts the worst stations | App-open / search logs (Lyft Product) |
| U4 | Do lakefront "valet" corrals operate at the fill-pressure stations? | Valet service raises effective capacity on event days and would explain some M1 flags | Divvy Ops event calendar |

## Assumptions (made explicitly, with how to test them)

| # | Assumption | Why it's reasonable | How to test or relax it |
|---|---|---|---|
| A1 | The operational day starts at 04:00 local time | The hourly profile has its minimum at 03:00–04:59, and overnight crews reset stations | Change `model.op_day_start_hour` and rerun |
| A2 | Current GBFS capacity applies to the whole window | Dock hardware changes rarely, and 99.4% of dock-named ids still resolve | Keep polling GBFS monthly; historical snapshots then make capacity date-effective |
| A3 | Trip timestamps are Chicago local time | They line up with the known commute peaks (08:00 and 17:00) | Would matter across a DST change; there is none in Jun–Aug |
| A4 | Within a single second, arrivals are processed before departures | Optimistic, so it slightly *understates* swing | Swap the tie-break order in `sql/50_station_day.sql` |
| A5 | A ride's start and end station ids are where the bike physically was | This is the operator's own attribution | None available publicly |

## Limitations (what this output must not be used for)

| # | Limitation | Consequence |
|---|---|---|
| L1 | **M2 is a lower bound.** It assumes perfect overnight stocking. Real starting stock isn't optimal, so real moves are higher | Use it as the minimum fleet-move budget, never as a target to cut to |
| L2 | **Dockless e-bike demand is outside scope** (about 38% of rides) | The KPI describes docking stations only; e-bike battery-swap routing needs a separate model |
| L3 | **GBFS corroboration is small and time-biased.** Snapshots were polled for a few hours on one September morning, not during the trip months | It is directional evidence that chronic stations really fail more often, not a measured stockout rate |
| L4 | Public racks and corrals are excluded from the KPI | Corral overflow at events (North Ave Beach Overflow Corral) is invisible to M1 |
| L5 | The final day of each run window is excluded (its after-midnight events are in the next month's file) | A 3-month run reports 91 operational days, not 92 |
