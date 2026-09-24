"""Dependable pipeline (Class 8): ingest -> validate -> model -> metrics -> publish.

Usage:
    python -m divvy run                     # months from config/pipeline.yaml
    python -m divvy run --months 2026-08    # override the window
    python -m divvy run --snapshot          # also take a fresh GBFS snapshot first
    python -m divvy snapshot --count 6 --interval 600

Dependability guarantees:
  * Rerunnable: raw downloads are skipped when checksums match; loads are
    delete-then-insert per month; model and metric tables are rebuilt from
    scratch. Running twice gives identical outputs.
  * Fail closed: any exception, including a BLOCK validation rule, stops the run.
    outputs/ is only replaced after every step succeeds, so a failed run never
    leaves half-written metrics behind. The failure is recorded in
    outputs/last_run.json and audit.runs, and the process exits non-zero.
  * Single writer: a lock file prevents two runs from writing at once.
  * Traceable: every run has a run_id, a log file, and a manifest of input
    checksums, rule results, and output row counts.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import ingest_gbfs, ingest_trips, load, metrics, model, validate
from .config import PROJECT_ROOT, Config, load_config
from .log import new_run_id, setup_logging
from .report import write_evidence_report

log = logging.getLogger("divvy.pipeline")


@contextmanager
def run_lock(path: Path):
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError(f"another run holds {path}; delete it if that run is dead")
    try:
        os.write(fd, str(os.getpid()).encode())
        yield
    finally:
        os.close(fd)
        path.unlink(missing_ok=True)


@contextmanager
def step(name: str, timings: dict):
    log.info("---- step: %s ----", name)
    t0 = time.perf_counter()
    try:
        yield
    except Exception:
        log.error("step %s FAILED after %.1fs", name, time.perf_counter() - t0)
        raise
    timings[name] = round(time.perf_counter() - t0, 2)
    log.info("step %s ok (%.1fs)", name, timings[name])


def _publish(out_dir: Path, tables: dict, extra_files: dict[str, str]) -> dict[str, int]:
    """Write everything to a sibling staging dir, then swap files into place."""
    staging = out_dir.parent / f".{out_dir.name}.staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    counts = {}
    for name, df in tables.items():
        df.to_csv(staging / f"{name}.csv", index=False)
        counts[f"{name}.csv"] = len(df)
    for name, text in extra_files.items():
        (staging / name).write_text(text)
    out_dir.mkdir(exist_ok=True)
    for f in staging.iterdir():
        f.replace(out_dir / f.name)
    staging.rmdir()
    return counts


def _record_run(con, record: dict) -> None:
    con.execute("""create table if not exists audit.runs (
        run_id varchar, started_at varchar, finished_at varchar, status varchar,
        months varchar, failed_step varchar, error varchar, manifest json)""")
    con.execute("delete from audit.runs where run_id = ?", [record["run_id"]])
    con.execute("insert into audit.runs values (?,?,?,?,?,?,?,?)", [
        record["run_id"], record["started_at"], record.get("finished_at"), record["status"],
        ",".join(record["months"]), record.get("failed_step"), record.get("error"),
        json.dumps(record)])


def run(cfg: Config, run_id: str, take_snapshot: bool = False) -> dict:
    out_dir = cfg.path("outputs")
    record = {"run_id": run_id, "months": cfg.months, "status": "running",
              "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "step_seconds": {}}
    timings = record["step_seconds"]
    current = "setup"
    con = None
    try:
        with run_lock(PROJECT_ROOT / ".pipeline.lock"):
            current = "ingest"
            with step("ingest", timings):
                record["inputs"] = {"trips": ingest_trips.ingest_trips(cfg)}
                if take_snapshot or not ingest_gbfs.list_snapshots(cfg):
                    ingest_gbfs.take_snapshot(cfg)
                record["inputs"]["gbfs_snapshots"] = [p.name for p in ingest_gbfs.list_snapshots(cfg)]

            current = "load"
            with step("load", timings):
                con = load.connect(cfg)
                load.load_trips(con, cfg, record["inputs"]["trips"])
                load.load_gbfs(con, cfg)

            current = "validate"
            with step("validate", timings):
                results = validate.validate(con, cfg, run_id)
                record["validation"] = {r.rule_id: {"passed": r.passed, "rows": r.rows_affected,
                                                    "pct": round(r.pct, 3)} for r in results}

            current = "model"
            with step("model", timings):
                model.build_model(con, cfg)

            current = "metrics"
            with step("metrics", timings):
                tables = metrics.compute(con, cfg)
                tables["validation_report"] = con.execute(
                    "select * exclude (run_id) from audit.validation_results where run_id = ? "
                    "order by rule_id", [run_id]).df()

            current = "publish"
            with step("publish", timings):
                record["status"] = "success"
                record["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                report = write_evidence_report(cfg, record, tables)
                record["outputs"] = _publish(out_dir, tables, {"evidence_report.md": report})
                (out_dir / "last_run.json").write_text(json.dumps(record, indent=2, default=str))
                _record_run(con, record)
        log.info("RUN %s SUCCEEDED in %.0fs", run_id, sum(timings.values()))
        return record
    except Exception as exc:
        record.update(status="failed", failed_step=current, error=f"{type(exc).__name__}: {exc}",
                      finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        log.error("RUN %s FAILED at step '%s': %s", run_id, current, exc)
        log.debug(traceback.format_exc())
        out_dir.mkdir(exist_ok=True)
        # Previous metric CSVs are left untouched; only the run status is updated.
        (out_dir / "last_run.json").write_text(json.dumps(record, indent=2, default=str))
        if con is not None:
            _record_run(con, record)
        raise
    finally:
        if con is not None:
            con.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="divvy", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", help="run the full pipeline")
    p_run.add_argument("--months", nargs="+", help="YYYY-MM months (overrides config)")
    p_run.add_argument("--snapshot", action="store_true", help="take a fresh GBFS snapshot first")
    p_run.add_argument("--config", help="path to an alternative pipeline.yaml")
    p_snap = sub.add_parser("snapshot", help="poll GBFS station status")
    p_snap.add_argument("--count", type=int, default=1)
    p_snap.add_argument("--interval", type=int, default=600, help="seconds between snapshots")
    args = parser.parse_args(argv)

    cfg = load_config(getattr(args, "config", None))
    if getattr(args, "months", None):
        cfg = Config(cfg.raw | {"months": args.months})
    run_id = new_run_id()
    setup_logging(cfg.path("logs"), run_id)

    try:
        if args.command == "snapshot":
            ingest_gbfs.poll_snapshots(cfg, args.count, args.interval)
        else:
            run(cfg, run_id, take_snapshot=args.snapshot)
    except validate.ValidationError:
        return 2   # data problem: fix or explain the input, do not just rerun
    except Exception:
        return 1   # infrastructure / code problem
    return 0


if __name__ == "__main__":
    sys.exit(main())
