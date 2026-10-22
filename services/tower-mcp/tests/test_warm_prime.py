"""Startup warm-up on AgentCore: one throwaway Gateway call so the cold-start 400 never reaches a user."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from camara_client import GatewayClient
from camara_client.errors import transport_error
from tower_mcp.deps import PRIME_E164, Deps


class _Gateway(GatewayClient):
    """Records what warm() does; the first sim_swap_check fails like the real cold start."""

    def __init__(self) -> None:  # no config needed: nothing is sent
        self.calls: list[str] = []

    async def check_tools(self) -> list[str]:
        self.calls.append("check_tools")
        return []

    async def sim_swap_check(self, line: Any, max_age_h: int) -> Any:
        self.calls.append(f"sim_swap_check:{line.line_id}:{line.e164}:{max_age_h}")
        raise transport_error()


@pytest.mark.unit
def test_warm_lists_tools_then_primes_with_a_fictional_number_and_swallows_the_failure() -> None:
    gw = _Gateway()
    deps = object.__new__(Deps)
    deps.carrier = gw
    asyncio.run(deps.warm())
    assert gw.calls == ["check_tools", f"sim_swap_check:warm-up:{PRIME_E164}:72"]
    assert PRIME_E164.startswith("+1500555")  # the reserved fictional range: matches no line anywhere
