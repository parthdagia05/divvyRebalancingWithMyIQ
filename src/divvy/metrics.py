"""Compute the project metrics (Class 7) and write the evidence outputs.

KPI (M1): share of active docking-station-days that needed rebalancing
          (intraday swing > dock capacity; see sql/50_station_day.sql).

  M1  Rebalance-required station-day rate      how often the system fails on its own
  M2  Minimum bike moves per day               how much truck/valet work is implied
  M3  Top-N concentration of required moves    can a fixed route cover it?
  M4  Chronic stations                         which stations to put on that route
  M5  Dock-attributed ride coverage            how much of demand the model can see

Plus C1, a corroboration check against live GBFS status: stations the model
calls chronic should be empty or full more often than the rest.
"""
from __future__ import annotations

import logging

import duckdb
import pandas as pd

from .config import Config

log = logging.getLogger("divvy.metrics")

METRIC_DEFS = {
    "M1": ("Rebalance-required station-day rate", "%",
           "Share of active docking-station-days whose intraday swing exceeded dock capacity"),
    "M2": ("Minimum bike moves per day", "bikes/day",
           "Sum over station-days of (swing - capacity), divided by operational days (lower bound)"),
    "M3": ("Top-{n} station share of required moves", "%",
           "Share of all required moves at the {n} stations with the most required moves"),
    "M4": ("Chronic imbalance stations", "stations",
           "Docking stations needing rebalancing on >= {pct:.0%} of their active days (min 10 days)"),
    "M5": ("Dock-attributed ride coverage", "%",
           "Share of rides that start AND end at an in-scope docking station"),
}


def compute(con: duckdb.DuckDBPyConnection, cfg: Config) -> dict[str, pd.DataFrame]:
    m = cfg["metrics"]
    top_n, chronic_pct = m["concentration_top_n"], m["chronic_min_share_of_days"]
    q = lambda sql: con.execute(sql).df()

    con.execute("""
        create or replace table metrics.station_summary as
        select station_id,
               count(*)                                         as active_days,
               sum(needs_rebalance::int)                        as days_needing_rebalance,
               avg(needs_rebalance::int)                        as share_days_needing_rebalance,
               sum(excess_moves)                                as required_moves,
               sum(departures)                                  as departures,
               sum(arrivals)                                    as arrivals,
               avg(net_flow)                                    as avg_daily_net_flow,
               mode(pressure) filter (where needs_rebalance)    as dominant_pressure,
               any_value(capacity)                              as capacity
        from model.station_day where in_kpi_scope group by 1
    """)

    def by_month(select: str, frm: str = "model.station_day where in_kpi_scope") -> str:
        return f"""select 'ALL' as month, {select} from {frm}
                   union all
                   select strftime(op_date, '%Y-%m'), {select} from {frm} group by 1 order by 1"""

    m1 = q(by_month("100 * avg(needs_rebalance::int) as value"))
    m2 = q(by_month("sum(excess_moves) / count(distinct op_date) as value"))
    m3 = q(f"""
        with ranked as (
            select strftime(op_date, '%Y-%m') as month, station_id, sum(excess_moves) x
            from model.station_day where in_kpi_scope group by all
            union all
            select 'ALL', station_id, sum(excess_moves)
            from model.station_day where in_kpi_scope group by all
        ), r as (
            select *, row_number() over (partition by month order by x desc) rk from ranked
        )
        select month, 100 * sum(x) filter (where rk <= {top_n}) / sum(x) as value
        from r group by 1 order by 1""")
    m4 = q(f"""
        with s as (
            select strftime(op_date, '%Y-%m') as month, station_id,
                   avg(needs_rebalance::int) as share_days, count(*) as n_days
            from model.station_day where in_kpi_scope group by all
            union all
            select 'ALL', station_id, avg(needs_rebalance::int), count(*)
            from model.station_day where in_kpi_scope group by all
        )
        select month, count(*) filter (where share_days >= {chronic_pct} and n_days >= 10) as value
        from s group by 1 order by 1""")
    # coalesce: a dockless end has no station row, and must count as "not covered"
    # rather than drop out of the average as NULL.
    months = ", ".join(f"'{mo}'" for mo in cfg.months)
    m5 = q(f"""
        with r as (
            select strftime(r.started_at, '%Y-%m') as month,
                   coalesce(ss.station_kind = 'docking_station'
                            and es.station_kind = 'docking_station', false) as covered
            from model.fact_ride r
            left join model.dim_station ss on ss.station_id = r.start_station_id
            left join model.dim_station es on es.station_id = r.end_station_id
            where strftime(r.started_at, '%Y-%m') in ({months})
        )
        select 'ALL' as month, 100 * avg(covered::int) as value from r
        union all
        select month, 100 * avg(covered::int) from r group by 1 order by 1""")

    rows = []
    for mid, df in {"M1": m1, "M2": m2, "M3": m3, "M4": m4, "M5": m5}.items():
        name, unit, definition = METRIC_DEFS[mid]
        fmt = {"n": top_n, "pct": chronic_pct}
        for _, r in df.iterrows():
            rows.append({"metric_id": mid, "metric": name.format(**fmt), "unit": unit,
                         "month": r["month"], "value": round(float(r["value"]), 2),
                         "definition": definition.format(**fmt)})
    summary = pd.DataFrame(rows)
    con.execute("create or replace table metrics.summary as select * from summary")

    targets = q(f"""
        with obs as (
            select station_id, avg(is_empty::int) as observed_empty_rate,
                   avg(is_full::int) as observed_full_rate, count(*) as snapshots
            from model.station_status_obs group by 1
        ), peak as (
            select station_id, arg_max(event_hour, abs_net) as worst_hour
            from (select station_id, event_hour, abs(avg(net_flow)) abs_net
                  from model.station_hour where isodow(op_date) <= 5 group by all)
            group by 1
        )
        select s.station_id, d.station_name, s.capacity, s.dominant_pressure,
               s.active_days, s.days_needing_rebalance,
               round(100 * s.share_days_needing_rebalance, 1) as pct_days_needing_rebalance,
               s.required_moves,
               round(s.required_moves / s.active_days, 1)      as required_moves_per_day,
               round(s.avg_daily_net_flow, 1)                  as avg_daily_net_flow,
               p.worst_hour                                    as worst_weekday_hour,
               round(100 * o.observed_empty_rate, 1)           as observed_empty_pct,
               round(100 * o.observed_full_rate, 1)            as observed_full_pct,
               d.lat, d.lon
        from metrics.station_summary s
        join model.dim_station d using (station_id)
        left join obs o using (station_id)
        left join peak p using (station_id)
        where s.required_moves > 0
        order by s.required_moves desc
        limit {m['target_list_size']}""")

    corroboration = q(f"""
        select case when s.share_days_needing_rebalance >= {chronic_pct} and s.active_days >= 10
                    then 'chronic (M4)' else 'other docking stations' end as station_group,
               count(distinct o.station_id)          as stations_observed,
               count(*)                              as status_observations,
               round(100 * avg(o.is_empty::int), 1)  as observed_empty_pct,
               round(100 * avg(o.is_full::int), 1)   as observed_full_pct,
               round(100 * avg((o.is_empty or o.is_full)::int), 1) as observed_empty_or_full_pct
        from model.station_status_obs o
        join metrics.station_summary s using (station_id)
        group by 1 order by 1""")

    hourly = q(f"""
        select event_hour as hour,
               case when s.share_days_needing_rebalance >= {chronic_pct} and s.active_days >= 10
                    then 'chronic_' || s.dominant_pressure else 'other' end as station_group,
               round(avg(h.net_flow), 3) as avg_net_flow_per_station_hour
        from model.station_hour h
        join metrics.station_summary s using (station_id)
        where isodow(h.op_date) <= 5
        group by all order by 2, 1""")

    daily = q("""
        select op_date, count(*) as active_stations,
               sum(needs_rebalance::int) as stations_needing_rebalance,
               round(100 * avg(needs_rebalance::int), 2) as pct_needing_rebalance,
               sum(excess_moves) as required_moves
        from model.station_day where in_kpi_scope group by 1 order by 1""")

    for mid in ("M1", "M2", "M3", "M4", "M5"):
        v = summary[(summary.metric_id == mid) & (summary.month == "ALL")].iloc[0]
        log.info("%s %-45s %10.2f %s", mid, v["metric"], v["value"], v["unit"])
    return {"metrics_summary": summary, "rebalancing_targets": targets,
            "corroboration_gbfs": corroboration, "hourly_profile": hourly,
            "daily_kpi": daily}
