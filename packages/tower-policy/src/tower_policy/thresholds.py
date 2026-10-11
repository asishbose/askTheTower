"""Thresholds are data (`thresholds.yaml`), not code.

`load_thresholds(path)` reads and validates a YAML file; `parse_thresholds(text)` is the pure part.
The engine refuses a file that widens `SWAP_WINDOW` past the SIM Swap API's `maxAge` maximum
(2400 h) or sets `STALE` above one hour (03 §3).
"""

import re
from datetime import timedelta
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

SWAP_WINDOW_MAX = timedelta(hours=2400)  # CAMARA sim-swap `maxAge` maximum
STALE_MAX = timedelta(hours=1)

DEFAULT_THRESHOLDS_PATH: Path = Path(__file__).with_name("thresholds.yaml")

_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(s|sec|m|min|h|hr|d)\s*$", re.I)
_UNIT_SECONDS = {"s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600, "hr": 3600, "d": 86400}


class ThresholdError(ValueError):
    """Raised when a thresholds file is missing, malformed, or outside the allowed bounds."""


def parse_duration(value: Any) -> timedelta:
    """'72h' | '10m' | '30s' | '2d' | 90 (seconds) | timedelta → timedelta."""
    if isinstance(value, timedelta):
        return value
    if isinstance(value, bool):
        raise ThresholdError(f"not a duration: {value!r}")
    if isinstance(value, int | float):
        return timedelta(seconds=float(value))
    if isinstance(value, str):
        m = _DURATION.match(value)
        if m:
            return timedelta(seconds=float(m.group(1)) * _UNIT_SECONDS[m.group(2).lower()])
    raise ThresholdError(f"not a duration: {value!r} (use e.g. '72h', '10m', '30s')")


class UnreachableAlert(BaseModel):
    """Per watch-profile windows applied by Alerts (06 §2); not consulted by the engine."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    transplant: timedelta
    care: timedelta

    @field_validator("transplant", "care", mode="before")
    @classmethod
    def _dur(cls, v: Any) -> timedelta:
        return parse_duration(v)


class Thresholds(BaseModel):
    """Every threshold in `thresholds.yaml`, parsed to `timedelta` and bounds-checked."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    SWAP_WINDOW: timedelta
    STALE: timedelta
    FRESH: timedelta
    UNREACHABLE_ALERT: UnreachableAlert
    ESCALATE_NEXT: timedelta
    RATE_LIMIT: timedelta
    ACK_DISTRUST: timedelta

    @field_validator(
        "SWAP_WINDOW", "STALE", "FRESH", "ESCALATE_NEXT", "RATE_LIMIT", "ACK_DISTRUST", mode="before"
    )
    @classmethod
    def _dur(cls, v: Any) -> timedelta:
        d = parse_duration(v)
        if d <= timedelta(0):
            raise ThresholdError(f"duration must be positive, got {v!r}")
        return d

    @field_validator("SWAP_WINDOW")
    @classmethod
    def _swap_window_bounded(cls, v: timedelta) -> timedelta:
        if v > SWAP_WINDOW_MAX:
            raise ThresholdError(f"SWAP_WINDOW {v} exceeds the SIM Swap API maximum of {SWAP_WINDOW_MAX}")
        return v

    @field_validator("STALE")
    @classmethod
    def _stale_bounded(cls, v: timedelta) -> timedelta:
        if v > STALE_MAX:
            raise ThresholdError(f"STALE {v} exceeds the maximum of {STALE_MAX}")
        return v


def parse_thresholds(text: str) -> Thresholds:
    """Pure: YAML text → validated `Thresholds`. Raises `ThresholdError` on any problem."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ThresholdError(f"thresholds YAML is malformed: {e}") from e
    if not isinstance(data, dict):
        raise ThresholdError("thresholds YAML must be a mapping of NAME: duration")
    try:
        return Thresholds.model_validate(data)
    except ValidationError as e:
        raise ThresholdError(str(e)) from e


def load_thresholds(path: str | Path | None = None) -> Thresholds:
    """Read and validate a thresholds file. `None` → the packaged `thresholds.yaml`.

    This is the one function in the package that touches the filesystem; callers load once and
    pass the `Thresholds` object to the engine.
    """
    p = DEFAULT_THRESHOLDS_PATH if path is None else Path(path)
    if not p.is_file():
        raise ThresholdError(f"thresholds file not found: {p}")
    return parse_thresholds(p.read_text(encoding="utf-8"))


@cache
def default_thresholds() -> Thresholds:
    """The packaged thresholds, loaded once per process."""
    return load_thresholds(None)
