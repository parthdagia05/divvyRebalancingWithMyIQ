"""Profile and validate raw inputs (Class 6).

Three kinds of rule, each with a business reason (see docs/03_validation_rules.md):

  BLOCK      The input cannot be trusted as a whole: the run stops, and the
             previous outputs stay in place.
  REJECT     The row is unusable (no valid time, a duplicate). It moves to
             staging.trips_rejected with a reason code and is never deleted.
  FLAG       The row is real but needs special handling downstream. It stays
             in staging.trips with a boolean flag column, and the model decides
             how to treat it. Nothing is silently fixed.

Every rule's result is written to audit.validation_results for this run_id.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import duckdb

from .config import Config

log = logging.getLogger("divvy.validate")


class ValidationError(RuntimeError):
    """A blocking rule failed. The run stops before transform."""


@dataclass
class RuleResult:
    rule_id: str
    severity: str
    description: str
    rows_affected: int
    denominator: int
    passed: bool
    action: str

    @property
    def pct(self) -> float:
        return 100.0 * self.rows_affected / self.denominator if self.denominator else 0.0


def build_staging(con: duckdb.DuckDBPyConnection, cfg: Config) -> None:
    v = cfg["validation"]
    b = v["bbox"]
    months = ", ".join(f"'{m}'" for m in cfg.months)
    types = ", ".join(f"'{t}'" for t in v["allowed_rideable_types"])
    members = ", ".join(f"'{t}'" for t in v["allowed_member_casual"])
    staff = v["staff_station_pattern"]

    def out_of_area(lat: str, lng: str) -> str:
        return (f"({lat} is not null and ({lat} not between {b['lat_min']} and {b['lat_max']} "
                f"or {lng} not between {b['lng_min']} and {b['lng_max']}))")

    con.execute(f"""
    create or replace table staging.trips_all as
    with typed as (
        select
            ride_id,
            rideable_type,
            member_casual,
            try_cast(started_at as timestamp)        as started_at,
            try_cast(ended_at   as timestamp)        as ended_at,
            nullif(trim(start_station_id), '')       as start_station_id,
            nullif(trim(start_station_name), '')     as start_station_name,
            nullif(trim(end_station_id), '')         as end_station_id,
            nullif(trim(end_station_name), '')       as end_station_name,
            try_cast(start_lat as double) as start_lat, try_cast(start_lng as double) as start_lng,
            try_cast(end_lat   as double) as end_lat,   try_cast(end_lng   as double) as end_lng,
            source_month, source_file, source_row,
            -- A ride that crosses a month boundary can appear in two files.
            -- Keep the first occurrence deterministically and flag the rest.
            row_number() over (partition by ride_id
                               order by source_month, source_row) as occurrence
        from raw.trips
        where source_month in ({months})
    )
    select *,
        date_diff('second', started_at, ended_at)                 as duration_s,
        -- REJECT reasons
        (started_at is null or ended_at is null)                   as r_bad_timestamp,
        (ended_at < started_at)                                    as r_negative_duration,
        (occurrence > 1)                                           as r_duplicate,
        -- FLAG reasons
        (date_diff('second', started_at, ended_at) < 60)           as f_under_60s,
        (date_diff('second', started_at, ended_at) > 86400)        as f_over_24h,
        (end_lat is null and end_station_id is null)               as f_unreturned,
        coalesce(regexp_matches(start_station_name, '{staff}'), false)
          or coalesce(regexp_matches(end_station_name, '{staff}'), false) as f_staff_station,
        {out_of_area('start_lat', 'start_lng')} or {out_of_area('end_lat', 'end_lng')}
                                                                   as f_out_of_area,
        (rideable_type not in ({types}))                           as f_unknown_rideable_type,
        (member_casual not in ({members}))                         as f_unknown_member_type,
        (strftime(ended_at, '%Y-%m') is distinct from source_month) as f_file_month_mismatch
    from typed
    """)
    con.execute("""
        create or replace table staging.trips_rejected as
        select *, case when r_bad_timestamp then 'bad_timestamp'
                       when r_negative_duration then 'negative_duration'
                       else 'duplicate_ride_id' end as reject_reason
        from staging.trips_all
        where r_bad_timestamp or coalesce(r_negative_duration, false) or r_duplicate
    """)
    con.execute("""
        create or replace table staging.trips as
        select * exclude (occurrence, r_bad_timestamp, r_negative_duration, r_duplicate)
        from staging.trips_all
        where not (r_bad_timestamp or coalesce(r_negative_duration, false) or r_duplicate)
    """)

    # Station inventory from GBFS, typed. A station is a *docking station* if
    # GBFS gives it a short_name (the id trips use); public racks and corrals
    # come without one.
    con.execute(f"""
        create or replace table staging.gbfs_stations as
        select station_id as gbfs_station_id,
               nullif(short_name, '') as short_name,
               name, lat, lon,
               try_cast(capacity as integer) as capacity,
               case when nullif(short_name, '') is not null
                         and not regexp_matches(name, '(?i)public rack|corral')
                    then 'docking_station' else 'public_rack' end as station_kind,
               regexp_matches(name, '{staff}') as is_staff_facility
        from raw.gbfs_station_information
    """)


def _scalar(con, sql: str) -> int:
    return int(con.execute(sql).fetchone()[0] or 0)


def run_rules(con: duckdb.DuckDBPyConnection, cfg: Config, run_id: str) -> list[RuleResult]:
    v = cfg["validation"]
    n_all = _scalar(con, "select count(*) from staging.trips_all")
    n = _scalar(con, "select count(*) from staging.trips")
    results: list[RuleResult] = []

    def add(rule_id, severity, description, affected, denom, passed, action):
        results.append(RuleResult(rule_id, severity, description, affected, denom, passed, action))

    # ---- BLOCK: the input as a whole -------------------------------------------------
    bad_ts = _scalar(con, "select count(*) from staging.trips_all where r_bad_timestamp")
    add("V01", "BLOCK", "Timestamps parse", bad_ts, n_all,
        100 * bad_ts / n_all <= v["max_pct_unparseable_timestamps"], "stop run if exceeded")

    dups = _scalar(con, "select count(*) from staging.trips_all where r_duplicate")
    add("V02", "BLOCK", "ride_id unique across loaded months", dups, n_all,
        100 * dups / n_all <= v["max_pct_duplicate_ride_ids"],
        "keep first occurrence, quarantine rest; stop if exceeded")

    mism = _scalar(con, "select count(*) from staging.trips where f_file_month_mismatch")
    add("V03", "BLOCK", "Each file only holds rides that END in its month", mism, n,
        100 * mism / n <= v["max_pct_file_month_mismatch"],
        "stop: our partition assumption would be wrong")

    for month in cfg.months:
        days_expected = _scalar(con, f"""select date_diff('day', date '{month}-01',
                                          date '{month}-01' + interval 1 month)""")
        days_seen = _scalar(con, f"""select count(distinct cast(started_at as date))
                                      from staging.trips where strftime(started_at, '%Y-%m') = '{month}'""")
        missing = days_expected - days_seen
        add(f"V04:{month}", "BLOCK", f"Every day of {month} has rides", missing, days_expected,
            100 * days_seen / days_expected >= v["min_days_with_rides_pct"],
            "stop: a missing day means an incomplete export")

    dock_rides = _scalar(con, """select count(*) from staging.trips t
        where start_station_id is not null
          and not regexp_matches(start_station_name, '(?i)public rack|corral')""")
    joined = _scalar(con, """select count(*) from staging.trips t
        join staging.gbfs_stations s on s.short_name = t.start_station_id
        where not regexp_matches(t.start_station_name, '(?i)public rack|corral')""")
    add("V05", "BLOCK", "Dock-named trip stations resolve to a GBFS short_name",
        dock_rides - joined, dock_rides,
        100 * joined / dock_rides >= v["min_gbfs_docking_join_pct"],
        "stop: capacity join would silently drop demand")

    # ---- REJECT: row is unusable -----------------------------------------------------
    neg = _scalar(con, "select count(*) from staging.trips_rejected where reject_reason = 'negative_duration'")
    add("R01", "REJECT", "ended_at >= started_at", neg, n_all, True, "quarantine row")

    # ---- FLAG: row is real, handled explicitly downstream ----------------------------
    flag_rules = [
        ("F01", "f_under_60s", "Ride shorter than 60s (Divvy says these are removed; they are not)",
         "exclude from flows: same-dock re-docks / false starts"),
        ("F02", "f_over_24h", "Ride longer than 24h",
         "keep departure; arrival kept but counted (likely recovered bike)"),
        ("F03", "f_unreturned", "No end station and no end coordinates",
         "departure counts, no arrival (lost/stolen or unreconciled)"),
        ("F04", "f_staff_station", "Starts or ends at an operator facility",
         "exclude from flows: not customer demand"),
        ("F05", "f_out_of_area", "Coordinates outside Chicago service area", "keep, counted"),
        ("F06", "f_unknown_rideable_type", "rideable_type outside known domain", "keep, counted"),
        ("F07", "f_unknown_member_type", "member_casual outside known domain", "keep, counted"),
    ]
    for rule_id, col, desc, action in flag_rules:
        k = _scalar(con, f"select count(*) from staging.trips where {col}")
        add(rule_id, "FLAG", desc, k, n, True, action)

    no_start = _scalar(con, "select count(*) from staging.trips where start_station_id is null")
    add("F08", "FLAG", "No start station (dockless e-bike undock)", no_start, n, True,
        "outside station-balance scope; reported as coverage metric M5")
    no_end = _scalar(con, "select count(*) from staging.trips where end_station_id is null")
    add("F09", "FLAG", "No end station (dockless e-bike park)", no_end, n, True,
        "outside station-balance scope; reported as coverage metric M5")

    multi_name = _scalar(con, """select count(*) from (
        select start_station_id from staging.trips where start_station_id is not null
        group by 1 having count(distinct start_station_name) > 1)""")
    n_ids = _scalar(con, "select count(distinct start_station_id) from staging.trips")
    add("F10", "FLAG", "Station id appears under more than one name", multi_name, n_ids, True,
        "identify stations by id only; canonical name from GBFS")

    # ---- GBFS inventory ---------------------------------------------------------------
    n_st = _scalar(con, "select count(*) from staging.gbfs_stations")
    dup_sn = _scalar(con, """select coalesce(sum(k - 1), 0) from (select count(*) k
        from staging.gbfs_stations where short_name is not null group by short_name)""")
    add("G01", "BLOCK", "GBFS short_name unique", dup_sn, n_st, dup_sn == 0,
        "stop: the join key would fan out")
    zero_cap = _scalar(con, """select count(*) from staging.gbfs_stations
        where station_kind = 'docking_station' and coalesce(capacity, 0) <= 0""")
    add("G02", "FLAG", "Docking station with capacity <= 0", zero_cap, n_st, True,
        "excluded from capacity KPI")
    over_cap = _scalar(con, """select count(*) from raw.gbfs_station_status st
        join staging.gbfs_stations s on s.gbfs_station_id = st.station_id
        where s.station_kind = 'docking_station'
          and st.num_bikes_available + st.num_docks_available
              + st.num_bikes_disabled + st.num_docks_disabled > s.capacity""")
    n_status = _scalar(con, "select count(*) from raw.gbfs_station_status")
    add("G03", "FLAG", "Status bikes+docks exceed stated capacity", over_cap, n_status, True,
        "capacity treated as approximate (assumption A2)")
    dock_status = _scalar(con, """select count(*) from raw.gbfs_station_status st
        join staging.gbfs_stations s on s.gbfs_station_id = st.station_id
        where s.station_kind = 'docking_station'""")
    stale = _scalar(con, """select count(*) from raw.gbfs_station_status st
        join staging.gbfs_stations s on s.gbfs_station_id = st.station_id
        where s.station_kind = 'docking_station' and st.is_installed = 1
          and st.feed_last_updated - st.last_reported > 3600""")
    add("G04", "FLAG", "Docking station status not reported for over 1h", stale, dock_status,
        True, "excluded from observed empty/full rate")

    con.execute("""create table if not exists audit.validation_results (
        run_id varchar, rule_id varchar, severity varchar, description varchar,
        rows_affected bigint, denominator bigint, pct double, passed boolean, action varchar)""")
    con.execute("delete from audit.validation_results where run_id = ?", [run_id])
    con.executemany("insert into audit.validation_results values (?,?,?,?,?,?,?,?,?)",
                    [(run_id, r.rule_id, r.severity, r.description, r.rows_affected,
                      r.denominator, round(r.pct, 4), r.passed, r.action) for r in results])

    for r in results:
        level = logging.INFO if r.passed else logging.ERROR
        log.log(level, "%-10s %-6s %-5s %9s / %-9s (%6.2f%%) %s", r.rule_id, r.severity,
                "PASS" if r.passed else "FAIL", f"{r.rows_affected:,}", f"{r.denominator:,}",
                r.pct, r.description)
    return results


def validate(con: duckdb.DuckDBPyConnection, cfg: Config, run_id: str) -> list[RuleResult]:
    build_staging(con, cfg)
    results = run_rules(con, cfg, run_id)
    failed = [r for r in results if r.severity == "BLOCK" and not r.passed]
    if failed:
        raise ValidationError("blocking validation failed: " +
                              "; ".join(f"{r.rule_id} {r.description} ({r.pct:.2f}%)" for r in failed))
    return results
