"""Circuit breaker, one per carrier base URL (05 §5): 5 consecutive 5xx open it for 60 s; while open
every call fails at once with `CarrierError(STALE_DATA, retryable=False)`. After 60 s it is half-open:
one trial call goes through — success (or any non-5xx answer) closes it, a 5xx / timeout / transport
failure re-opens it for another 60 s. Only 5xx answers count toward opening (the doc's rule); a 4xx
or a 429 proves the carrier is up and resets the count.

The clock is injected (`time.monotonic` by default) so tests move time instead of sleeping.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from camara_client.errors import breaker_open

State = Literal["closed", "open", "half_open"]

FAILURE_THRESHOLD = 5
OPEN_FOR_S = 60.0


@dataclass
class CircuitBreaker:
    threshold: int = FAILURE_THRESHOLD
    open_for_s: float = OPEN_FOR_S
    clock: Callable[[], float] = time.monotonic
    failures: int = 0
    opened_at: float | None = None
    trial_in_flight: bool = False

    @property
    def state(self) -> State:
        if self.opened_at is None:
            return "closed"
        if self.clock() - self.opened_at >= self.open_for_s:
            return "half_open"
        return "open"

    def before_call(self) -> None:
        """Raise `CarrierError(STALE_DATA)` if the call must not go out."""
        state = self.state
        if state == "open":
            raise breaker_open()
        if state == "half_open":
            if self.trial_in_flight:
                raise breaker_open()
            self.trial_in_flight = True

    def on_success(self) -> None:
        """The carrier answered (2xx, 4xx or 429)."""
        self.failures = 0
        self.opened_at = None
        self.trial_in_flight = False

    def on_server_error(self) -> None:
        """A 5xx answer."""
        if self.trial_in_flight or self.opened_at is not None:
            self._open()
            return
        self.failures += 1
        if self.failures >= self.threshold:
            self._open()

    def on_no_answer(self) -> None:
        """Timeout or transport failure: does not count toward opening, but fails a half-open trial."""
        if self.trial_in_flight:
            self._open()

    def _open(self) -> None:
        self.opened_at = self.clock()
        self.failures = 0
        self.trial_in_flight = False


@dataclass
class BreakerRegistry:
    """Breakers keyed by base URL; share one registry per process (the default below)."""

    clock: Callable[[], float] = time.monotonic
    threshold: int = FAILURE_THRESHOLD
    open_for_s: float = OPEN_FOR_S
    breakers: dict[str, CircuitBreaker] = field(default_factory=dict)

    def for_url(self, base_url: str) -> CircuitBreaker:
        key = base_url.rstrip("/")
        breaker = self.breakers.get(key)
        if breaker is None:
            breaker = CircuitBreaker(threshold=self.threshold, open_for_s=self.open_for_s, clock=self.clock)
            self.breakers[key] = breaker
        return breaker


DEFAULT_BREAKERS = BreakerRegistry()
