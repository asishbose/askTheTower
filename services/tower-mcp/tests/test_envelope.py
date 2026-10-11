"""Every tool × every reason code it can return → a valid `ToolResult`: summary non-empty, facts are
booleans / timestamps / closed enums only (plus the caller's own alias), and refusals carry no carrier facts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

import pytest
from botocore.exceptions import EndpointConnectionError
from mock_carrier.testing import ASISH, MOM
from tower_consent import LastState, revoke, tables, validate_alias
from tower_mcp.schemas import ToolResult
from tower_mcp.seed import seed_watch
from tower_policy import ReasonCode

from .conftest import MOCK_START, Stack

pytestmark = pytest.mark.integration

ENUMS = {
    "none",
    "unconditional",
    "conditional",
    "carrier",
    "watch",
    "DATA",
    "SMS",
    "NONE",
    "UNKNOWN",
    "sms",
    "reachability",
    "ok",
    "changed",
    "refused",
    "suppressed",
    "user-asish",
    "user-mom",
    # watch_line facts.profile, a closed enum (02 §2, 06 §11.1)
    "self",
    "transplant",
    "care",
}
INT_PARENTS = {"by_actor", "outcomes"}


def assert_facts_shape(value: Any, parent: str = "") -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            if parent in INT_PARENTS:
                assert isinstance(v, int) and not isinstance(v, bool), (k, v)
                continue
            assert_facts_shape(v, k)
    elif isinstance(value, list):
        for v in value:
            assert_facts_shape(v, parent)
    elif value is None or isinstance(value, bool):
        return
    elif isinstance(value, str):
        if parent in ("line", "alias"):
            assert value in ("self", "unknown") or validate_alias(value)
            return
        if value in ENUMS:
            return
        datetime.fromisoformat(value)  # anything else must be a timestamp
    else:
        raise AssertionError(f"{parent}: {value!r} is not a boolean, timestamp or enum")


def check(r: ToolResult, code: str) -> None:
    assert r.reason_codes[0] == code, r
    wire = ToolResult.model_validate(r.to_wire()).to_wire()
    assert wire["summary"].strip()
    assert wire["checked_at"].endswith(("Z", "+00:00"))
    assert_facts_shape(wire["facts"])
    if code in {"NOT_BOUND", "NO_CONSENT", "SERVICE_UNAVAILABLE"}:
        assert set(r.facts.model_dump(exclude_defaults=True)) == {"line"}
    expected_kind = {
        "NOT_BOUND": "bind_line",
        "NO_CONSENT": "ask_consent",
        "SIM_SWAPPED_RECENT": "call_carrier",
        "CALL_FORWARDING_SET": "call_carrier",
    }.get(code, "none")
    assert r.next_step.kind == expected_kind
    if expected_kind != "bind_line":
        assert r.next_step.url is None


Setup = Callable[[Stack], Awaitable[None]]


async def nothing(_: Stack) -> None:
    return None


async def swap(s: Stack) -> None:
    await s.event(ASISH, "sim_swap")
    await s.event(MOM, "sim_swap")


async def forward(s: Stack) -> None:
    await s.event(ASISH, "cf_set")
    await s.event(MOM, "cf_set")


async def unreachable(s: Stack) -> None:
    await s.event(MOM, "unreachable")
    await s.event(ASISH, "unreachable")


async def revoked(s: Stack) -> None:
    revoke(s.store, s.seed.mom_line, "user-asish", "watch", revoked_by="user-mom", now=MOCK_START)


async def unbound(s: Stack) -> None:
    """Asish's own line is not bound: the one NOT_BOUND path (04 §5; an unknown alias is NO_CONSENT, D18)."""
    s.store.delete(tables.LINES, {"line_id": s.seed.asish_line})


async def stale(s: Stack) -> None:
    for line in (s.seed.asish_line, s.seed.mom_line):
        seed_watch(
            s.store,
            line,
            "user-asish",
            LastState(cf_status="none", reachable=True, at=MOCK_START - timedelta(minutes=20)),
        )
    await s.fault("timeout", 3)


async def carrier_error(s: Stack) -> None:
    await s.fault("500", 3)


async def store_down(s: Stack) -> None:
    def fail(**_: Any) -> None:
        raise EndpointConnectionError(endpoint_url="http://dynamodb.invalid")

    s.inject("Query", fail)


CASES: list[tuple[str, str, dict[str, Any], Setup]] = []
for line in ("self", "mom"):
    CASES += [
        ("line_is_ok", "OK", {"line": line}, nothing),
        ("line_is_ok", "SIM_SWAPPED_RECENT", {"line": line}, swap),
        ("line_is_ok", "CALL_FORWARDING_SET", {"line": line}, forward),
        ("line_is_ok", "STALE_DATA", {"line": line}, stale),
        ("line_is_ok", "CARRIER_ERROR", {"line": line}, carrier_error),
        ("line_is_ok", "SERVICE_UNAVAILABLE", {"line": line}, store_down),
        ("is_reachable", "OK", {"line": line}, nothing),
        ("is_reachable", "UNREACHABLE", {"line": line}, unreachable),
        ("is_reachable", "STALE_DATA", {"line": line}, stale),
        ("is_reachable", "CARRIER_ERROR", {"line": line}, carrier_error),
        ("is_reachable", "SERVICE_UNAVAILABLE", {"line": line}, store_down),
        ("watch_line", "OK", {"line": line, "enable": True}, nothing),
        ("watch_line", "OK", {"line": line, "enable": False}, nothing),
        ("watch_line", "OK", {"line": line, "enable": None}, nothing),
        ("watch_line", "SERVICE_UNAVAILABLE", {"line": line}, store_down),
    ]
for tool in ("line_is_ok", "is_reachable", "watch_line"):
    CASES += [
        (tool, "NOT_BOUND", {"line": "self"}, unbound),
        (tool, "NO_CONSENT", {"line": "mom"}, revoked),
        (tool, "NO_CONSENT", {"line": "bob"}, nothing),  # D18: no grant under that alias
    ]


@pytest.mark.parametrize(
    ("tool", "code", "args", "setup"), CASES, ids=[f"{t}-{c}-{a}" for t, c, a, _ in CASES]
)
async def test_every_code_is_a_valid_envelope(
    stack: Stack, tool: str, code: str, args: dict[str, Any], setup: Setup
) -> None:
    await setup(stack)
    r = await getattr(stack, tool)("user-asish", **args)
    check(r, code)


def test_every_engine_code_is_covered() -> None:
    covered = {code for _, code, _, _ in CASES}
    engine = {c.value for c in ReasonCode} - {
        "SUPPRESSED_REVOKED",
        "ALERT_FAILED",
        "ACK_IGNORED_SWAPPED_LINE",
    }
    assert covered == engine
