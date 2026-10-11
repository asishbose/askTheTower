"""The controllable clock. Every timestamp the mock emits derives from `Clock.now()`.

This module is the only place in the service allowed to touch the wall clock, and it does so only
in `wall_now()`. Two clocks share one shape (`MockClock`): `Clock`, which moves only when told to (the scenario
clock, `MOCK_CLOCK=scenario`, the default), and `WallClock`, wall time plus an offset that starts at 0 on every
scenario load (`MOCK_CLOCK=wall`, the Fargate task; 08 §3, decision 4 of 2026-10-11). `wall_now()` is read by the
runtime only for `MOCK_CLOCK=wall` and for `clock: now` in a scenario; tests inject their own wall.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol


def parse_iso(value: str) -> datetime:
    """RFC 3339 → aware UTC datetime. Accepts a trailing Z. Raises ValueError."""
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError(f"timestamp must carry a time zone: {value!r}")
    return dt.astimezone(UTC)


def iso(dt: datetime) -> str:
    """Aware datetime → RFC 3339 with millisecond precision and a Z suffix."""
    return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_offset(value: str) -> timedelta:
    """`+HH:MM:SS` or `+HH:MM` (relative to the scenario clock). Raises ValueError."""
    text = value.strip()
    if not text.startswith("+"):
        raise ValueError(f"offset must start with '+': {value!r}")
    parts = text[1:].split(":")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        raise ValueError(f"offset must be +HH:MM[:SS]: {value!r}")
    nums = [int(p) for p in parts] + [0] * (3 - len(parts))
    return timedelta(hours=nums[0], minutes=nums[1], seconds=nums[2])


class MockClock(Protocol):
    def now(self) -> datetime: ...

    def set(self, now: datetime) -> None: ...

    def advance(self, seconds: float) -> datetime: ...


class Clock:
    """A clock that moves only when told to."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("clock start must be timezone-aware")
        self._now = start.astimezone(UTC)

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        if now.tzinfo is None:
            raise ValueError("clock time must be timezone-aware")
        self._now = now.astimezone(UTC)

    def advance(self, seconds: float) -> datetime:
        self._now = self._now + timedelta(seconds=seconds)
        return self._now

    @staticmethod
    def wall_now() -> datetime:
        """The one wall-clock read in the service. Used only for `clock: now` in a scenario file."""
        return datetime.now(tz=UTC)


class WallClock:
    """Wall time plus an offset (`MOCK_CLOCK=wall`). Starts at offset 0; `set` and `advance` move the offset, so
    `POST /_admin/clock` still jumps it while it keeps ticking with the wall in between."""

    def __init__(self, wall: Callable[[], datetime]) -> None:
        self._wall = wall
        self._offset = timedelta(0)

    @property
    def offset(self) -> timedelta:
        return self._offset

    def now(self) -> datetime:
        return (self._wall() + self._offset).astimezone(UTC)

    def set(self, now: datetime) -> None:
        if now.tzinfo is None:
            raise ValueError("clock time must be timezone-aware")
        self._offset = now.astimezone(UTC) - self._wall()

    def advance(self, seconds: float) -> datetime:
        self._offset += timedelta(seconds=seconds)
        return self.now()
