"""The aws runner's gate (doc 11 §2 "The gate", T4): G1–G6 in order; the first that fails is the reason, verbatim.

`decide` is the pure decision. `Gate` gathers its inputs on every call (so a flipped condition is seen at the next
POST, without a restart) and caches only G6, `make config-check`, until the root `.env`'s mtime changes or the
presenter presses re-check. The `.env` is never opened here: G5 asks whether it exists, G6's cache key is its mtime.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from demo_ui.ops.envload import dotenv_mtime, dotenv_present
from demo_ui.ops.probes import ProbeResult

G1 = "AWS runs never run in the container: use make ops-ui on the host"
G2 = "AWS runs need the UI on 127.0.0.1 (this one listens on {host})"
G3 = "AWS runs are off: start the UI with make showcase-ui ENV=aws or make ops-ui (or DEMO_UI_AWS_OPS=1 in .env)"
G4_MAKE = "make not found"
G4_CHECKOUT = "checkout not found"
G5 = "root .env not found: cp .env.example .env, then make config-check"
G6 = "make config-check failed: run it in a terminal"
HOST = "127.0.0.1"
FLAG = "1"


@dataclass(frozen=True)
class GateInputs:
    published_loopback: bool
    host: str
    flag: str
    make_found: bool
    checkout_found: bool
    dotenv: bool


def checkout_found(checkout: Path | None) -> bool:
    return checkout is not None and (checkout / "Makefile").is_file() and (checkout / "mk").is_dir()


def decide(inputs: GateInputs, config_check: ProbeResult[None] | None) -> str | None:
    """None when the aws runner may run; else the first failing condition's reason. `config_check` is asked only
    when G1–G5 hold (None there means it has not been asked)."""
    if inputs.published_loopback:
        return G1
    if inputs.host != HOST:
        return G2.format(host=inputs.host)
    if inputs.flag != FLAG:
        return G3
    if not inputs.make_found:
        return G4_MAKE
    if not inputs.checkout_found:
        return G4_CHECKOUT
    if not inputs.dotenv:
        return G5
    if config_check is None or config_check.ok:
        return None
    return f"{G6} ({config_check.reason})" if config_check.reason else G6


class Gate:
    def __init__(
        self,
        *,
        published_loopback: bool,
        host: str,
        flag: str,
        checkout: Path | None,
        which: Callable[[str], str | None],
        config_check: Callable[[], Awaitable[ProbeResult[None]]],
    ) -> None:
        self.published_loopback = published_loopback
        self.host = host
        self.flag = flag
        self.checkout = checkout
        self.which = which
        self.config_check = config_check
        self._cached: tuple[float | None, ProbeResult[None]] | None = None
        self._lock = asyncio.Lock()
        self.last: str | None = G3  # the last decision, for `/healthz` (never runs a process)

    def inputs(self) -> GateInputs:
        return GateInputs(
            published_loopback=self.published_loopback,
            host=self.host,
            flag=self.flag,
            make_found=bool(self.which("make")),
            checkout_found=checkout_found(self.checkout),
            dotenv=dotenv_present(self.checkout),
        )

    async def reason(self, *, fresh: bool = False) -> str | None:
        """G1–G6 now. G6 runs `make config-check` only when G1–G5 hold and its cached answer is stale."""
        inputs = self.inputs()
        early = decide(inputs, None)
        if early is not None:
            self.last = early
            return early
        async with self._lock:
            stamp = dotenv_mtime(self.checkout)
            if fresh or self._cached is None or self._cached[0] != stamp:
                self._cached = (stamp, await self.config_check())
            self.last = decide(inputs, self._cached[1])
        return self.last
