import json

import pytest

from divvy import load, metrics, model, pipeline, validate
from divvy.config import Config

from conftest import A, B, base_rides, manifest_entry, ride, write_world


def build(cfg: Config, run_id: str = "test"):
    con = load.connect(cfg)
    load.load_trips(con, cfg, [manifest_entry(cfg)])
    load.load_gbfs(con, cfg)
    results = validate.validate(con, cfg, run_id)
    model.build_model(con, cfg)
    return con, {r.rule_id: r for r in results}


def test_swing_exceeds_capacity_flags_station_day(world):
    con, _ = build(world)
    row = con.execute("""select departures, arrivals, peak, trough, swing, capacity,
                                needs_rebalance, excess_moves, pressure
                         from model.station_day
                         where station_id = 'CHI00001' and op_date = date '2026-08-05'""").fetchone()
    assert row == (5, 2, 0, -5, 5, 3, True, 2, "drain")


def test_large_station_absorbs_same_flow(world):
    con, _ = build(world)
    needs = con.execute("""select needs_rebalance from model.station_day
                           where station_id = 'CHI00002' and op_date = date '2026-08-05'""").fetchone()[0]
    assert needs is False  # capacity 20 absorbs a swing of 5


def test_bad_rows_are_quarantined_or_flagged_not_dropped(world):
    con, rules = build(world)
    rejected = dict(con.execute("select ride_id, reject_reason from staging.trips_rejected").fetchall())
    assert rejected == {"NEG": "negative_duration"}
    assert rules["F01"].rows_affected == 1          # SHORT is kept but flagged
    assert rules["F08"].rows_affected == 1          # DOCKLESS is kept but flagged
    kept = {r[0] for r in con.execute("select ride_id from staging.trips").fetchall()}
    assert {"SHORT", "DOCKLESS", "RACK"} <= kept
    # ...and the short same-dock ride does not move bikes in the model
    assert con.execute("select count(*) from model.fact_station_event where ride_id = 'SHORT'").fetchone()[0] == 0


def test_public_rack_is_out_of_kpi_scope(world):
    con, _ = build(world)
    kind = con.execute("select station_kind from model.dim_station where station_id = 'CHI00003'").fetchone()[0]
    assert kind == "public_rack"
    assert con.execute("select count(*) from model.station_day where station_id = 'CHI00003' "
                       "and in_kpi_scope").fetchone()[0] == 0


def test_rerun_load_is_idempotent(world):
    con, _ = build(world)
    first = con.execute("select count(*) from raw.trips").fetchone()[0]
    load.load_trips(con, world, [manifest_entry(world)])
    assert con.execute("select count(*) from raw.trips").fetchone()[0] == first


def test_missing_day_blocks_the_run(tmp_path):
    rides = [r for r in base_rides() if not r.startswith("D15")]
    cfg = write_world(tmp_path, rides)
    with pytest.raises(validate.ValidationError, match="V04"):
        build(cfg)


def test_duplicate_ride_ids_beyond_threshold_block(tmp_path):
    rides = base_rides()
    rides += [ride("A0", A, B, "05 07:00:00", "05 07:30:00")] * 5  # 5 dups of ~42 rows > 1%
    cfg = write_world(tmp_path, rides)
    with pytest.raises(validate.ValidationError, match="V02"):
        build(cfg)


def test_schema_change_blocks_load(tmp_path):
    cfg = write_world(tmp_path, base_rides())
    csv = cfg.path("raw_trips") / "202608-divvy-tripdata.csv"
    csv.write_text(csv.read_text().replace("member_casual", "rider_type", 1))
    con = load.connect(cfg)
    with pytest.raises(load.SchemaError):
        load.load_trips(con, cfg, [manifest_entry(cfg)])


def _run(cfg, monkeypatch, run_id):
    monkeypatch.setattr(pipeline.ingest_trips, "ingest_trips", lambda c: [manifest_entry(c)])
    return pipeline.run(cfg, run_id)


def test_full_run_publishes_and_rerun_is_identical(world, monkeypatch):
    _run(world, monkeypatch, "run1")
    out = world.path("outputs")
    first = (out / "metrics_summary.csv").read_text()
    _run(world, monkeypatch, "run2")
    assert (out / "metrics_summary.csv").read_text() == first
    assert json.loads((out / "last_run.json").read_text())["status"] == "success"


def test_failed_run_keeps_previous_outputs(world, monkeypatch):
    _run(world, monkeypatch, "good")
    out = world.path("outputs")
    good = (out / "metrics_summary.csv").read_text()

    # Break the input: drop a whole day, then rerun.
    csv = world.path("raw_trips") / "202608-divvy-tripdata.csv"
    csv.write_text("\n".join(l for l in csv.read_text().splitlines() if not l.startswith("D15")) + "\n")
    with pytest.raises(validate.ValidationError):
        _run(world, monkeypatch, "bad")

    assert (out / "metrics_summary.csv").read_text() == good
    status = json.loads((out / "last_run.json").read_text())
    assert status["status"] == "failed" and status["failed_step"] == "validate"


def test_metrics_are_computed(world):
    con, _ = build(world)
    out = metrics.compute(con, world)
    s = out["metrics_summary"].set_index(["metric_id", "month"])["value"]
    assert s[("M2", "ALL")] == pytest.approx(2 / 30, abs=0.01)  # 2 excess moves / 30 op days, rounded
    assert s[("M4", "ALL")] == 0                        # one bad day is not chronic
