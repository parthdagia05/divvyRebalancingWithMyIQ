"""Load pipeline configuration and resolve project paths."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "pipeline.yaml"


@dataclass(frozen=True)
class Config:
    raw: dict

    @property
    def months(self) -> list[str]:
        return list(self.raw["months"])

    def path(self, key: str) -> Path:
        return PROJECT_ROOT / self.raw["paths"][key]

    def __getitem__(self, key: str):
        return self.raw[key]


def load_config(path: Path | str | None = None) -> Config:
    with open(path or DEFAULT_CONFIG) as fh:
        return Config(yaml.safe_load(fh))


def yyyymm(month: str) -> str:
    """'2026-06' -> '202606'."""
    return month.replace("-", "")
