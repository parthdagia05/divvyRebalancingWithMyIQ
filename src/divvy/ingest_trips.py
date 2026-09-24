"""Retrieval mode 1: monthly trip archives (ZIP of CSV) from the public Divvy bucket.

Completeness is proven, not assumed. For every month we check:
  1. the downloaded byte count equals the server's Content-Length,
  2. the ZIP passes a CRC integrity test,
  3. the archive contains exactly one trip CSV,
  4. the CSV row count is recorded so later stages can reconcile against it.

Raw inputs are preserved untouched: the ZIP is kept as downloaded and the CSV is
extracted verbatim next to it. A manifest (sha256, size, ETag, row count) is
written to data/raw/trips/manifest.json and committed, so any rerun can prove it
processed byte-identical inputs.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests

from .config import Config, yyyymm

log = logging.getLogger("divvy.ingest_trips")


class RetrievalError(RuntimeError):
    """Raised when a source cannot be retrieved completely. Stops the run."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_manifest(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def _save_manifest(path: Path, manifest: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def _head(url: str, cfg: Config) -> dict:
    resp = requests.head(url, timeout=cfg["http"]["timeout_seconds"])
    if resp.status_code == 404:
        raise RetrievalError(f"{url} does not exist (404) - month not published yet?")
    resp.raise_for_status()
    return {"bytes": int(resp.headers["Content-Length"]),
            "etag": resp.headers.get("ETag", "").strip('"')}


def _download(url: str, dest: Path, expected_bytes: int, cfg: Config) -> None:
    """Stream to a .part file and only rename once the byte count matches."""
    http = cfg["http"]
    part = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(1, http["max_retries"] + 1):
        try:
            with requests.get(url, stream=True, timeout=http["timeout_seconds"]) as resp:
                resp.raise_for_status()
                with open(part, "wb") as fh:
                    for chunk in resp.iter_content(1 << 20):
                        fh.write(chunk)
            got = part.stat().st_size
            if got != expected_bytes:
                raise RetrievalError(f"truncated download: {got} of {expected_bytes} bytes")
            part.replace(dest)
            return
        except (requests.RequestException, RetrievalError) as exc:
            wait = http["backoff_seconds"] * attempt
            log.warning("download attempt %d/%d failed for %s: %s (retry in %ss)",
                        attempt, http["max_retries"], url, exc, wait)
            time.sleep(wait)
    part.unlink(missing_ok=True)
    raise RetrievalError(f"could not download {url} after {http['max_retries']} attempts")


def _extract_and_count(zip_path: Path, out_dir: Path) -> tuple[str, int]:
    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        if bad is not None:
            raise RetrievalError(f"{zip_path.name}: CRC failure in member {bad}")
        csvs = [n for n in zf.namelist()
                if n.endswith(".csv") and not n.startswith("__MACOSX")]
        if len(csvs) != 1:
            raise RetrievalError(f"{zip_path.name}: expected 1 CSV, found {csvs}")
        member = csvs[0]
        target = out_dir / Path(member).name
        with zf.open(member) as src, open(target, "wb") as dst:
            for chunk in iter(lambda: src.read(1 << 20), b""):
                dst.write(chunk)
    with open(target, "rb") as fh:
        rows = sum(1 for _ in fh) - 1  # minus header
    return target.name, rows


def ingest_month(month: str, cfg: Config) -> dict:
    raw_dir = cfg.path("raw_trips")
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = raw_dir / "manifest.json"
    manifest = _load_manifest(manifest_path)

    url = cfg["sources"]["trips_url_template"].format(yyyymm=yyyymm(month))
    zip_path = raw_dir / Path(url).name
    remote = _head(url, cfg)
    known = manifest.get(month)

    # Rerun behaviour: if the local archive matches both the server and our
    # manifest, skip the download entirely. If the server copy changed (Divvy
    # occasionally republishes a month), re-fetch and record the new checksum.
    if (zip_path.exists() and known
            and zip_path.stat().st_size == remote["bytes"]
            and known.get("etag") == remote["etag"]
            and known.get("sha256") == _sha256(zip_path)):
        log.info("%s: raw archive unchanged (sha256 %s...), skipping download",
                 month, known["sha256"][:12])
        csv_path = raw_dir / known["csv_file"]
        if not csv_path.exists():
            _extract_and_count(zip_path, raw_dir)
        return known

    if zip_path.exists() and zip_path.stat().st_size == remote["bytes"]:
        log.info("%s: archive present with matching size, verifying instead of re-downloading",
                 month)
    else:
        log.info("%s: downloading %s (%.1f MB)", month, url, remote["bytes"] / 1e6)
        _download(url, zip_path, remote["bytes"], cfg)

    csv_name, rows = _extract_and_count(zip_path, raw_dir)
    entry = {
        "month": month,
        "url": url,
        "zip_file": zip_path.name,
        "csv_file": csv_name,
        "bytes": remote["bytes"],
        "etag": remote["etag"],
        "sha256": _sha256(zip_path),
        "csv_rows": rows,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if known and known.get("sha256") != entry["sha256"]:
        log.warning("%s: source archive CHANGED since last run (%s... -> %s...)",
                    month, known.get("sha256", "")[:12], entry["sha256"][:12])
    manifest[month] = entry
    _save_manifest(manifest_path, manifest)
    log.info("%s: retrieved %s rows from %s", month, f"{rows:,}", csv_name)
    return entry


def ingest_trips(cfg: Config) -> list[dict]:
    return [ingest_month(m, cfg) for m in cfg.months]
