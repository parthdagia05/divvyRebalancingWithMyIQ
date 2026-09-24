# 04 — Workflow & Data Model (Class 7)

## The operational workflow

A bike's life at a station, and where the operator steps in:

```mermaid
stateDiagram-v2
    direction LR
    [*] --> Docked
    Docked --> InRide: undock (rider)
    InRide --> Docked: dock at a station
    InRide --> Parked: e-bike locked off-dock (rack or free-floating)
    Parked --> InRide: undock (rider)
    InRide --> Missing: never returned
    Docked --> InTruck: rebalancing pickup (operator)
    InTruck --> Docked: rebalancing drop-off (operator)
    Parked --> InTruck: collection / battery swap

    note right of InTruck
      NOT in any published source.
      We infer when it MUST have happened
      (swing > capacity) and how many bikes
      it moved at minimum.
    end note
```

What we can observe, and from which source:

| Workflow element | Type | Observed in | Table |
|---|---|---|---|
| Station | Entity | GBFS `station_information` + trip station ids | `model.dim_station` |
| Ride | Interaction | Trip history | `model.fact_ride` |
| Undock / dock | Event | Derived from each ride's start and end | `model.fact_station_event` |
| Station running balance | State | Derived: running sum of events since 04:00 | `model.station_day` (peak, trough, swing) |
| Rebalancing move | Intervention | **Not observed**; inferred as a lower bound | `model.station_day.excess_moves` |
| Station empty / full | Outcome | GBFS `station_status` (our own polling) | `model.station_status_obs` |

## Relational model

```mermaid
erDiagram
    dim_station ||--o{ fact_station_event : "has"
    fact_ride   ||--o{ fact_station_event : "produces 0..2"
    dim_station ||--o{ station_hour : "aggregates to"
    dim_station ||--o{ station_day : "aggregates to"
    dim_station ||--o{ station_status_obs : "observed as"

    dim_station {
        varchar station_id PK "trip id = GBFS short_name, e.g. CHI00252"
        varchar station_name "GBFS name, else most common trip spelling"
        varchar gbfs_station_id "19-digit GBFS id"
        int capacity "docks, from the latest GBFS snapshot"
        varchar station_kind "docking_station | public_rack | not_in_gbfs | staff_facility"
    }
    fact_ride {
        varchar ride_id PK
        varchar rideable_type "classic_bike | electric_bike"
        varchar member_casual
        timestamp started_at
        timestamp ended_at
        varchar start_station_id FK "null for dockless"
        varchar end_station_id FK "null for dockless"
        bool counts_for_flow "false for under-60s and staff rides"
    }
    fact_station_event {
        varchar ride_id FK
        varchar station_id FK
        timestamp event_ts
        varchar event_type "undock | dock"
        int bike_delta "-1 | +1"
        date op_date "day starts 04:00"
    }
    station_hour {
        varchar station_id FK
        date op_date
        int event_hour
        int departures
        int arrivals
        int net_flow
    }
    station_day {
        varchar station_id FK
        date op_date
        int peak "max running gain, at least 0"
        int trough "min running gain, at most 0"
        int swing "peak - trough"
        int capacity
        bool needs_rebalance "swing > capacity"
        int excess_moves "max(swing - capacity, 0)"
        varchar pressure "drain | fill"
    }
    station_status_obs {
        varchar station_id FK
        varchar snapshot
        bool is_empty
        bool is_full
    }
```

Layers in the DuckDB warehouse:

| Schema | Built by | Contents |
|---|---|---|
| `raw` | `load.py` | Source rows verbatim (all VARCHAR) with lineage columns |
| `staging` | `validate.py` | Typed rows with flag columns; `trips_rejected` quarantine |
| `model` | `sql/10..60_*.sql` | The entities, events, and outcomes above |
| `metrics` | `metrics.py` | Station summary and metric tables |
| `audit` | `validate.py`, `pipeline.py` | Validation results and run history per `run_id` |

## Why swing and not net flow

Net flow (arrivals minus departures over the whole day) hides the problem. A
station that loses 15 bikes by 9am and gets 15 back by 7pm has a net flow of
**0**, yet with 11 docks it was empty for part of the morning. Swing captures
exactly this:

```
running gain   0 ─┐                         ┌─ 0      peak   = 0
                  └──┐                  ┌───┘         trough = -15
                     └─────── -15 ──────┘             swing  = 15 > capacity 11
      04:00        09:00              19:00           → needs_rebalance, excess_moves = 4
```

To stay usable, a station starting with *s* bikes needs *s ≥ −trough* (enough
bikes for the deepest loss) and *s + peak ≤ capacity* (enough docks for the
biggest gain). Both can hold only if *swing ≤ capacity*.

## Metrics linked to the KPI

| ID | Metric | Formula | Decision it supports |
|---|---|---|---|
| **M1 (KPI)** | Rebalance-required station-day rate | `avg(needs_rebalance)` over active docking-station-days | Is the system getting better or worse, month over month? |
| M2 | Minimum bike moves per day | `sum(excess_moves) / operational days` | Truck and valet capacity to plan (a floor, not a ceiling) |
| M3 | Top-50 share of required moves | Share of `excess_moves` at the 50 highest stations | Can a fixed route replace ad-hoc dispatch? |
| M4 | Chronic imbalance stations | Stations with `needs_rebalance` on ≥ 50% of active days (min 10 days) | Candidates for fixed routes, valet corrals, or dock expansion |
| M5 | Dock-attributed ride coverage | Share of rides that start **and** end at a docking station | How much of demand M1–M4 can see; a trust metric |

Corroboration (C1): stations that M4 calls chronic should appear empty or full
in live GBFS snapshots more often than other stations.
