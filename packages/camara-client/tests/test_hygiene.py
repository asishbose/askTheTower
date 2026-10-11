"""Credential and number hygiene (05 §6–7): capture every log record and every exception (str, repr,
formatted traceback) from failing runs through both clients — no client secret, no bearer, no E.164,
no carrier `message` text."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable

import pytest
from camara_client import CarrierError, DirectClient, GatewayClient, LineRef, NumberVerifyResult
from camara_client.testing import FIXTURE_TOKEN
from mock_carrier.testing import ASISH

from tests.privacy.patterns import BEARER, phone_hits

pytestmark = pytest.mark.integration

STRANGER = LineRef("line-stranger", "+16135550177")
LINE = LineRef("line-asish", ASISH)
SECRETS = ("local-dev-tower", "local-dev-alerts", "local-dev-binding", "wrong-secret-value", FIXTURE_TOKEN)
CARRIER_TEXT = (
    "Device identifier not found",
    "Injected fault",
    "Client must authenticate",
    "Request not authenticated",
    "sufficient permissions",
)


def _assert_clean(text: str, where: str) -> None:
    assert not phone_hits(text), f"E.164 in {where}: {phone_hits(text)}"
    assert not BEARER.search(text), f"bearer token in {where}"
    assert "eyJ" not in text, f"JWT in {where}"
    for s in SECRETS:
        assert s not in text, f"secret in {where}"
    for t in CARRIER_TEXT:
        assert t not in text, f"carrier message text in {where}: {t!r}"


async def _failures(client: DirectClient | GatewayClient, mock) -> list[BaseException]:  # type: ignore[no-untyped-def]
    caught: list[BaseException] = []

    async def expect(coro) -> None:  # type: ignore[no-untyped-def]
        try:
            await coro
        except CarrierError as exc:
            caught.append(exc)
        else:
            raise AssertionError("expected a CarrierError")

    await expect(client.sim_swap_check(STRANGER, 72))  # 404
    await expect(client.reachability(STRANGER))
    await mock.fault("500", 1)
    await expect(client.call_forwarding(LINE))
    await mock.fault("timeout", 1)
    await expect(client.sim_swap_date(LINE))
    await mock.fault("429", 1)
    await expect(client.sim_swap_check(LINE, 72))
    await expect(client.number_verify("not-a-code", redirect_uri="http://localhost/cb", e164=ASISH))
    return caught


@pytest.mark.parametrize("kind", ["direct", "gateway", "bad-secret"])
async def test_no_secret_bearer_number_or_carrier_text(
    kind: str,
    make_direct: Callable[..., DirectClient],
    make_gateway: Callable[..., GatewayClient],
    mock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    if kind == "gateway":
        client: DirectClient | GatewayClient = make_gateway()
    elif kind == "bad-secret":
        client = make_direct(secret="wrong-secret-value")
    else:
        client = make_direct()
    errors = await _failures(client, mock)
    assert len(errors) == 6
    for exc in errors:
        _assert_clean(str(exc), "str(exc)")
        _assert_clean(repr(exc), "repr(exc)")
        _assert_clean("".join(traceback.format_exception(exc)), "traceback")
    ours = [r for r in caplog.records if r.name.startswith("camara_client")]
    assert ours, "the client logs failures at debug"
    for record in caplog.records:
        if record.name.startswith("mock_carrier"):
            continue  # the mock's own state is one of the three places a number may live
        _assert_clean(record.getMessage(), f"log {record.name}")
    _assert_clean(repr(client), "repr(client)")
    await client.aclose()


def test_reprs_hide_the_number() -> None:
    assert ASISH not in repr(LINE) and ASISH not in str(LINE)
    assert ASISH not in repr(NumberVerifyResult(verified=True, e164=ASISH))
