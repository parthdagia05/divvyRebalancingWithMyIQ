"""Retrieval mode 2: the live GBFS JSON API (station inventory and station status).

GBFS is a *current-state* API: it only ever returns "now". Unlike the trip
archives it cannot be re-downloaded later, so every response is preserved
verbatim (gzipped) under data/snapshots/gbfs/<snapshot_ts>/ and committed.

Completeness checks per snapshot:
  1. every requested feed is listed in the gbfs.json discovery document,
  2. each feed's `last_updated` is fresher than gbfs.max_feed_age_seconds,
  3. station_status and station_information describe the same station ids
     (a mismatch means we caught the feeds mid-refresh; the snapshot is retried).
"""
from __future__ import annotations

import gzip
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from .config import Config
from .ingest_trips import RetrievalError

log = logging.getLogger("divvy.ingest_gbfs")


def _get_json(url: str, cfg: Config) -> tuple[bytes, dict]:
    http = cfg["http"]
    for attempt in range(1, http["max_retries"] + 1):
        try:
            resp = requests.get(url, timeout=http["timeout_seconds"])
            resp.raise_for_status()
            return resp.content, resp.json()
        except (requests.RequestException, ValueError) as exc:
            wait = http["backoff_seconds"] * attempt
            log.warning("GET %s failed (%d/%d): %s", url, attempt, http["max_retries"], exc)
            time.sleep(wait)
    raise RetrievalError(f"could not fetch {url}")


def discover_feeds(cfg: Config) -> dict[str, str]:
    _, doc = _get_json(cfg["sources"]["gbfs_discovery_url"], cfg)
    feeds = doc["data"][cfg["sources"]["gbfs_language"]]["feeds"]
    urls = {f["name"]: f["url"] for f in feeds}
    missing = set(cfg["sources"]["gbfs_feeds"]) - set(urls)
    if missing:
        raise RetrievalError(f"GBFS discovery is missing feeds: {sorted(missing)}")
    return urls


def take_snapshot(cfg: Config, feed_urls: dict[str, str] | None = None) -> Path:
    feed_urls = feed_urls or discover_feeds(cfg)
    max_age = cfg["gbfs"]["max_feed_age_seconds"]

    for attempt in range(1, cfg["http"]["max_retries"] + 1):
        fetched_at = datetime.now(timezone.utc)
        payloads, docs = {}, {}
        for name in cfg["sources"]["gbfs_feeds"]:
            payloads[name], docs[name] = _get_json(feed_urls[name], cfg)
            age = fetched_at.timestamp() - docs[name]["last_updated"]
            if age > max_age:
                raise RetrievalError(f"{name} is stale: last_updated {age:.0f}s ago")

        info_ids = {s["station_id"] for s in docs["station_information"]["data"]["stations"]}
        status_ids = {s["station_id"] for s in docs["station_status"]["data"]["stations"]}
        if info_ids == status_ids:
            break
        log.warning("station_information/status id sets differ (%d vs %d, %d unmatched); "
                    "feeds mid-refresh, retrying", len(info_ids), len(status_ids),
                    len(info_ids ^ status_ids))
        time.sleep(cfg["http"]["backoff_seconds"])
    else:
        raise RetrievalError("station_information and station_status never agreed on station ids")

    snap_dir = cfg.path("gbfs_snapshots") / fetched_at.strftime("%Y%m%dT%H%M%SZ")
    snap_dir.mkdir(parents=True, exist_ok=True)
    for name, raw in payloads.items():
        with gzip.open(snap_dir / f"{name}.json.gz", "wb") as fh:
            fh.write(raw)  # verbatim bytes, exactly as served
    meta = {
        "fetched_at": fetched_at.isoformat(timespec="seconds"),
        "feeds": {n: {"url": feed_urls[n], "last_updated": docs[n]["last_updated"],
                      "ttl": docs[n].get("ttl"), "bytes": len(payloads[n])}
                  for n in payloads},
        "station_count": len(info_ids),
    }
    (snap_dir / "_meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    log.info("GBFS snapshot %s: %d stations", snap_dir.name, len(info_ids))
    return snap_dir


def poll_snapshots(cfg: Config, count: int, interval_seconds: int) -> list[Path]:
    """Take `count` snapshots `interval_seconds` apart to observe empty/full docks over time."""
    feed_urls = discover_feeds(cfg)
    taken = []
    for i in range(count):
        taken.append(take_snapshot(cfg, feed_urls))
        if i < count - 1:
            time.sleep(interval_seconds)
    return taken


def load_snapshot(snap_dir: Path, feed: str) -> dict:
    with gzip.open(snap_dir / f"{feed}.json.gz", "rb") as fh:
        return json.loads(fh.read())


def list_snapshots(cfg: Config) -> list[Path]:
    root = cfg.path("gbfs_snapshots")
    return sorted(p for p in root.iterdir() if (p / "_meta.json").exists()) if root.exists() else []
