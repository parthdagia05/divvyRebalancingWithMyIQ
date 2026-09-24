"""Load preserved raw inputs into the DuckDB `raw` schema, untouched.

Every trip column is loaded as VARCHAR so that nothing is coerced or silently
repaired at load time. Type problems surface in validation, where they are
counted and explained. Each row keeps its lineage: source month, file, and row
number.

Rerun behaviour: loading is delete-then-insert per source month inside one
transaction, so rerunning a month replaces it exactly and never duplicates it.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import duckdb
import pandas as pd

from .config import Config
from .ingest_gbfs import list_snapshots, load_snapshot

log = logging.getLogger("divvy.load")

TRIP_COLUMNS = [
    "ride_id", "rideable_type", "started_at", "ended_at",
    "start_station_name", "start_station_id", "end_station_name", "end_station_id",
    "start_lat", "start_lng", "end_lat", "end_lng", "member_casual",
]


class SchemaError(RuntimeError):
    """The source changed shape. Stops the run: downstream logic would be wrong."""


def connect(cfg: Config) -> duckdb.DuckDBPyConnection:
    path = cfg.path("warehouse")
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    for schema in ("raw", "staging", "model", "metrics", "audit"):
        con.execute(f"create schema if not exists {schema}")
    return con


def load_trips(con: duckdb.DuckDBPyConnection, cfg: Config, entries: list[dict]) -> None:
    con.execute(f"""
        create table if not exists raw.trips (
            {", ".join(f"{c} varchar" for c in TRIP_COLUMNS)},
            source_month varchar, source_file varchar, source_row bigint
        )""")
    raw_dir = cfg.path("raw_trips")
    for e in entries:
        csv_path = raw_dir / e["csv_file"]
        header = con.execute(
            f"select * from read_csv('{csv_path}', all_varchar=true, header=true) limit 0"
        ).description
        cols = [d[0] for d in header]
        if cols != TRIP_COLUMNS:
            raise SchemaError(f"{csv_path.name}: columns changed.\n"
                              f"  expected {TRIP_COLUMNS}\n  got      {cols}")
        con.execute("begin")
        con.execute("delete from raw.trips where source_month = ?", [e["month"]])
        con.execute(f"""
            insert into raw.trips
            select *, ? as source_month, ? as source_file,
                   row_number() over () as source_row
            from read_csv('{csv_path}', all_varchar=true, header=true)
        """, [e["month"], e["csv_file"]])
        loaded = con.execute("select count(*) from raw.trips where source_month = ?",
                             [e["month"]]).fetchone()[0]
        if loaded != e["csv_rows"]:
            con.execute("rollback")
            raise SchemaError(f"{e['month']}: loaded {loaded} rows but the raw CSV has "
                              f"{e['csv_rows']} (malformed quoting or embedded newlines?)")
        con.execute("commit")
        log.info("%s: loaded %s raw trip rows (reconciled with manifest)", e["month"], f"{loaded:,}")


def load_gbfs(con: duckdb.DuckDBPyConnection, cfg: Config) -> None:
    snaps = list_snapshots(cfg)
    if not snaps:
        raise FileNotFoundError("no GBFS snapshots found; run `python -m divvy snapshot` first")

    # Station inventory: use the latest snapshot (capacity is current-state).
    latest = snaps[-1]
    info = load_snapshot(latest, "station_information")
    stations = pd.DataFrame(info["data"]["stations"])
    stations = stations.reindex(columns=["station_id", "short_name", "name", "lat", "lon",
                                         "capacity", "region_id"])
    stations["snapshot"] = latest.name
    con.execute("create or replace table raw.gbfs_station_information as select * from stations")

    # Station status: every snapshot, appended into one time series.
    frames = []
    for snap in snaps:
        doc = load_snapshot(snap, "station_status")
        df = pd.DataFrame(doc["data"]["stations"]).reindex(columns=[
            "station_id", "num_bikes_available", "num_ebikes_available", "num_bikes_disabled",
            "num_docks_available", "num_docks_disabled", "is_installed", "is_renting",
            "is_returning", "last_reported"])
        df["snapshot"] = snap.name
        df["feed_last_updated"] = doc["last_updated"]
        frames.append(df)
    status = pd.concat(frames, ignore_index=True)
    con.execute("create or replace table raw.gbfs_station_status as select * from status")
    log.info("GBFS: %d stations (snapshot %s), %d status snapshots / %s rows",
             len(stations), latest.name, len(snaps), f"{len(status):,}")


def snapshot_meta(cfg: Config) -> list[dict]:
    return [json.loads((p / "_meta.json").read_text()) | {"snapshot": p.name}
            for p in list_snapshots(cfg)]
