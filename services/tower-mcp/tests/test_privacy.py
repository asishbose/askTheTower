"""Privacy (00 invariants, e2e-wiring §8.2): no subscriber number and no carrier error text in any tool result
or log line. Runs a battery covering every path at DEBUG, then also sweeps every result the suite produced."""

from __future__ import annotations

import json
import logging
from datetime import timedelta

import pytest
from mock_carrier import errors as mock_errors
from mock_carrier.testing import ASISH, MOM
from tower_consent import LastState, revoke
from tower_mcp.seed import seed_watch

from tests.privacy.patterns import E164_STRICT, HEALTH_WORDS, phone_hits

from .conftest import MOCK_START, RESULTS, Stack

pytestmark = pytest.mark.integration

# Library DEBUG chatter (SigV4 signatures, wire dumps of our own DynamoDB items) is not a log we ship; our loggers
# (tower_mcp, camara_client, tower_*) and everything else are swept.
THIRD_PARTY = (
    "botocore",
    "boto3",
    "urllib3",
    "moto",
    "s3transfer",
    "testcontainers",
    "docker",
    "httpcore",
    "httpx",
    "asyncio",
)


def carrier_messages() -> set[str]:
    """Every `message` the mock's CAMARA error envelopes can carry (one class per spec error code)."""
    out: set[str] = set()
    for obj in vars(mock_errors).values():
        if isinstance(obj, type) and issubclass(obj, Exception) and hasattr(obj, "code"):
            try:
                msg = obj().message  # type: ignore[call-arg]
            except Exception:  # noqa: BLE001,S112
                continue
            if isinstance(msg, str) and len(msg) > 12:
                out.add(msg)
    return out


def assert_clean(texts: list[str]) -> None:
    raw_numbers = {ASISH, MOM, ASISH[1:], MOM[1:], ASISH[2:], MOM[2:]}
    messages = carrier_messages()
    assert messages, "expected the mock to define error messages"
    for text in texts:
        assert not phone_hits(text), text
        assert not E164_STRICT.search(text), text
        for n in raw_numbers:
            assert n not in text
        for m in messages:
            assert m not in text, m
        assert "Traceback" not in text


async def test_battery_results_and_logs_are_clean(stack: Stack, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    await stack.line_is_ok("user-asish")
    await stack.line_is_ok("user-asish", "mom")
    await stack.is_reachable("user-asish", "mom")
    await stack.watch_line("user-asish", "mom", True)
    await stack.watch_line("user-mom", "self", None)
    await stack.event(ASISH, "sim_swap")
    await stack.event(ASISH, "cf_set")
    await stack.event(MOM, "unreachable")
    await stack.line_is_ok("user-asish")
    await stack.is_reachable("user-asish", "mom")
    for kind in ("500", "429", "timeout"):
        await stack.fault(kind, 2)
        await stack.line_is_ok("user-asish")
        await stack.fault(kind, 1)
        await stack.is_reachable("user-asish", "mom")
    seed_watch(
        stack.store,
        stack.seed.asish_line,
        "user-asish",
        LastState(cf_status="none", at=MOCK_START - timedelta(minutes=30)),
    )
    await stack.fault("timeout", 2)
    await stack.line_is_ok("user-asish")
    await stack.line_is_ok("user-asish", ASISH)  # a number given as `line` must not come back
    await stack.line_is_ok("user-asish", "bob")
    revoke(stack.store, stack.seed.mom_line, "user-asish", "watch", revoked_by="user-mom", now=MOCK_START)
    await stack.line_is_ok("user-asish", "mom")

    texts = [json.dumps(r) for r in stack.results] + [
        r.getMessage() for r in caplog.records if not r.name.startswith(THIRD_PARTY)
    ]
    assert len(stack.results) >= 15
    assert_clean(texts)
    for r in stack.results:
        assert not HEALTH_WORDS.search(r["summary"]), r["summary"]


def test_every_result_of_the_suite_is_clean() -> None:
    """Results collected by every test that ran before this one (empty when run alone)."""
    assert_clean([json.dumps(r) for r in RESULTS])
