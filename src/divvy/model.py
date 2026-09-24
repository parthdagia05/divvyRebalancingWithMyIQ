"""Build the workflow model (Class 7) by running sql/*.sql in order.

    station (entity) --< station_event (undock/dock) >-- ride (interaction)
    station_event --> station_day (outcome: swing vs capacity, inferred intervention)
    station       --< station_status_obs (observed outcome, GBFS polling)

Window edges: the final operational day of the window is excluded. Its
00:00-03:59 events and its late-evening rides that end after midnight belong to
the *next* month's file, which a run does not load, so that day would look
artificially calm.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import duckdb

from .config import PROJECT_ROOT, Config

log = logging.getLogger("divvy.model")
SQL_DIR = PROJECT_ROOT / "sql"


def window(cfg: Config) -> tuple[date, date]:
    first = date.fromisoformat(cfg.months[0] + "-01")
    y, m = map(int, cfg.months[-1].split("-"))
    after_last = date(y + (m == 12), m % 12 + 1, 1)
    return first, after_last - timedelta(days=2)  # drop the boundary op day


def build_model(con: duckdb.DuckDBPyConnection, cfg: Config) -> None:
    start, end = window(cfg)
    params = {
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "op_day_start_hour": cfg["model"]["op_day_start_hour"],
        "staff_pattern": cfg["validation"]["staff_station_pattern"],
    }
    log.info("model window: operational days %s .. %s", start, end)
    for sql_file in sorted(Path(SQL_DIR).glob("*.sql")):
        con.execute(sql_file.read_text().format(**params))
        table = sql_file.read_text().split("create or replace table ")[1].split()[0]
        rows = con.execute(f"select count(*) from {table}").fetchone()[0]
        log.info("built %-28s %12s rows  (%s)", table, f"{rows:,}", sql_file.name)
    _check_model(con)


def _check_model(con: duckdb.DuckDBPyConnection) -> None:
    """Invariants that must hold if the model is correct. Fail loudly if not."""
    checks = {
        "every flow ride yields at most 2 events":
            """select count(*) from (select ride_id from model.fact_station_event
               group by 1 having count(*) > 2)""",
        "station_day departures reconcile with events":
            """select abs((select sum(departures) from model.station_day)
                        - (select count(*) from model.fact_station_event e
                           join (select distinct op_date from model.station_day) d using (op_date)
                           where e.event_type = 'undock'))""",
        "swing is never smaller than |net flow|":
            "select count(*) from model.station_day where swing < abs(net_flow)",
        "dim_station ids are unique":
            "select count(*) - count(distinct station_id) from model.dim_station",
    }
    for name, sql in checks.items():
        bad = con.execute(sql).fetchone()[0]
        if bad:
            raise AssertionError(f"model invariant failed: {name} ({bad})")
        log.info("model check ok: %s", name)
