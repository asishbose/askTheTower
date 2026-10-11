"""Where Alerts gets `now`. Packages take `now` as an argument; this service is the one that reads a clock.

- `SystemClock` — wall-clock UTC (AWS, and local runs against a sandbox).
- `MockCarrierClock` — `GET <mock>/_admin/clock`, so the windows (20 min, 4 h, 15 min) follow the mock's
  scripted clock in the demo. `ALERTS_CLOCK_SCALE` does not touch time; it only shortens how often the local
  scheduler wakes up (see README).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

import httpx


class Clock(Protocol):
    async def now(self) -> datetime: ...


class SystemClock:
    async def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Tests: a settable clock."""

    def __init__(self, at: datetime) -> None:
        self.at = at

    async def now(self) -> datetime:
        return self.at

    def set(self, at: datetime) -> None:
        self.at = at


class MockCarrierClock:
    def __init__(self, base_url: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), transport=transport, timeout=5.0)

    async def now(self) -> datetime:
        r = await self._http.get("/_admin/clock")
        r.raise_for_status()
        return datetime.fromisoformat(str(r.json()["clock"]).replace("Z", "+00:00")).astimezone(UTC)

    async def aclose(self) -> None:
        await self._http.aclose()
