"""Tiny synthetic Divvy world: two docking stations, one public rack, a handful of rides.

Tests run the real load/validate/model/metrics code against it, offline.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from divvy.config import Config, load_config

HEADER = ("ride_id,rideable_type,started_at,ended_at,start_station_name,start_station_id,"
          "end_station_name,end_station_id,start_lat,start_lng,end_lat,end_lng,member_casual")

A = ("Alpha St & 1st Ave", "CHI00001")   # capacity 3
B = ("Beta St & 2nd Ave", "CHI00002")    # capacity 20
RACK = ("Public Rack - Gamma", "CHI00003")


def ride(rid, start, end, t0, t1, bike="classic_bike"):
    s_name, s_id = start if start else ("", "")
    e_name, e_id = end if end else ("", "")
    return (f"{rid},{bike},2026-08-{t0},2026-08-{t1},{s_name},{s_id},{e_name},{e_id},"
            f"41.88,-87.63,41.89,-87.62,member")


def base_rides() -> list[str]:
    rows = []
    # Every day in August gets one ordinary B->B ride so the V04 completeness rule passes.
    for d in range(1, 32):
        rows.append(ride(f"D{d:02d}", B, B, f"{d:02d} 12:00:00", f"{d:02d} 12:20:00"))
    # 2026-08-05 at station A (capacity 3): 5 departures in the morning,
    # then 2 arrivals. Trough -5, peak 0, swing 5 > 3, so it needs rebalancing
    # and excess = 2.
    for i in range(5):
        rows.append(ride(f"A{i}", A, B, f"05 07:0{i}:00", f"05 07:3{i}:00"))
    for i in range(2):
        rows.append(ride(f"R{i}", B, A, f"05 18:0{i}:00", f"05 18:3{i}:00"))
    # Noise that validation must catch rather than fix:
    rows.append(ride("SHORT", A, A, "06 09:00:00", "06 09:00:30"))            # F01 under 60s
    rows.append(ride("DOCKLESS", None, None, "06 10:00:00", "06 10:15:00",
                     bike="electric_bike"))                                     # F08/F09
    rows.append(ride("RACK", RACK, B, "06 11:00:00", "06 11:10:00"))          # public rack
    rows.append(ride("NEG", A, B, "07 10:00:00", "07 09:00:00"))              # R01 reject
    return rows


def write_world(root: Path, rides: list[str]) -> Config:
    raw = root / "raw"
    raw.mkdir(parents=True)
    (raw / "202608-divvy-tripdata.csv").write_text("\n".join([HEADER, *rides]) + "\n")

    snap = root / "snapshots" / "20260924T120000Z"
    snap.mkdir(parents=True)
    stations = [
        {"station_id": "g1", "short_name": A[1], "name": A[0], "lat": 41.88, "lon": -87.63, "capacity": 3},
        {"station_id": "g2", "short_name": B[1], "name": B[0], "lat": 41.89, "lon": -87.62, "capacity": 20},
        {"station_id": "g3", "name": RACK[0], "lat": 41.90, "lon": -87.61, "capacity": 2},
    ]
    status = [{"station_id": s["station_id"], "num_bikes_available": 0 if s["station_id"] == "g1" else 5,
               "num_ebikes_available": 0, "num_bikes_disabled": 0, "num_docks_available": 3,
               "num_docks_disabled": 0, "is_installed": 1, "is_renting": 1, "is_returning": 1,
               "last_reported": 1790000000} for s in stations]
    for name, data in {"station_information": {"stations": stations},
                       "station_status": {"stations": status},
                       "system_information": {"name": "test"}}.items():
        with gzip.open(snap / f"{name}.json.gz", "wb") as fh:
            fh.write(json.dumps({"last_updated": 1790000000, "ttl": 60, "data": data}).encode())
    (snap / "_meta.json").write_text("{}")

    cfg = load_config()
    raw_cfg = dict(cfg.raw)
    raw_cfg["months"] = ["2026-08"]
    raw_cfg["paths"] = {"raw_trips": str(raw), "gbfs_snapshots": str(root / "snapshots"),
                        "warehouse": str(root / "wh.duckdb"), "outputs": str(root / "outputs"),
                        "logs": str(root / "logs")}
    return Config(raw_cfg)


def manifest_entry(cfg: Config) -> dict:
    csv = cfg.path("raw_trips") / "202608-divvy-tripdata.csv"
    rows = sum(1 for _ in open(csv)) - 1
    return {"month": "2026-08", "csv_file": csv.name, "csv_rows": rows}


@pytest.fixture
def world(tmp_path) -> Config:
    return write_world(tmp_path, base_rides())
