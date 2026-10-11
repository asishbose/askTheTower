"""`next_step` (02 §3): what Alexa+ can offer after the summary.

| Reason codes | next_step |
|---|---|
| `NOT_BOUND` | `bind_line` + `url` = `BINDING_BASE_URL/bind/<single-use token>` (a fresh 10-min token for this user) |
| `NO_CONSENT` | `ask_consent` (the line-holder grants on their own binding page; nothing to link) |
| `SIM_SWAPPED_RECENT`, `CALL_FORWARDING_SET` | `call_carrier` + `carrier_support_number` (config; the carrier's public line) |
| anything else | `none` |
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import anyio
from tower_consent import create_bind_token, ensure_user
from tower_policy import ReasonCode

from tower_mcp.deps import Deps
from tower_mcp.schemas import NextStep

CALL_CARRIER_CODES = frozenset({ReasonCode.SIM_SWAPPED_RECENT, ReasonCode.CALL_FORWARDING_SET})


def binding_url(base_url: str, token: str) -> str:
    return f"{base_url.rstrip('/')}/bind/{token}"


async def build_next_step(deps: Deps, user_id: str, codes: Sequence[ReasonCode], now: datetime) -> NextStep:
    """May write (a bind token, the Users row); a store failure propagates → SERVICE_UNAVAILABLE."""
    if ReasonCode.NOT_BOUND in codes:
        wall = (
            await deps.wall_now()
        )  # the token's 10-min TTL runs on real time, even when Tower follows the mock clock

        def mint() -> str:
            ensure_user(deps.store, user_id, now=wall)
            return create_bind_token(deps.store, user_id, now=wall).token

        token = await anyio.to_thread.run_sync(mint)
        return NextStep(kind="bind_line", url=binding_url(deps.settings.binding_base_url, token))
    if ReasonCode.NO_CONSENT in codes:
        return NextStep(kind="ask_consent")
    if CALL_CARRIER_CODES.intersection(codes):
        return NextStep(kind="call_carrier", carrier_support_number=deps.settings.carrier_support_number)
    return NextStep(kind="none")
