"""DirectClient against the in-process mock carrier: every operation, and the demo scenario's flips
(SIM swap at +12 min, call forwarding at +20 min) observed through the client."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from camara_client import (
    CarrierClient,
    CarrierError,
    CFResult,
    DirectClient,
    LineRef,
    ReachResult,
    SimSwapResult,
)
from camara_client.testing import mock_config
from mock_carrier.testing import ASISH, BASE, MOM, REDIRECT
from tower_policy import ReasonCode

pytestmark = pytest.mark.integration

ASISH_LINE = LineRef("line-asish", ASISH)
MOM_LINE = LineRef("line-mom", MOM)
T0 = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)


async def test_satisfies_protocol(direct: DirectClient) -> None:
    assert isinstance(direct, CarrierClient)


async def test_sim_swap_flip(direct: DirectClient, mock) -> None:
    assert await direct.sim_swap_check(ASISH_LINE, 72) == SimSwapResult(swapped=False)
    assert await direct.sim_swap_date(ASISH_LINE) == datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    assert await direct.sim_swap_check(ASISH_LINE, 24 * 40) == SimSwapResult(swapped=True)
    await mock.advance(12 * 60)  # demo timeline: sim_swap on Asish's line at +00:12
    assert await direct.sim_swap_check(ASISH_LINE, 72) == SimSwapResult(swapped=True)
    assert await direct.sim_swap_date(ASISH_LINE) == T0 + timedelta(minutes=12)
    assert await direct.sim_swap_check(MOM_LINE, 72) == SimSwapResult(swapped=False)


async def test_call_forwarding_flip(direct: DirectClient, mock) -> None:
    assert await direct.call_forwarding(ASISH_LINE) == CFResult(status="none")
    await mock.advance(20 * 60)  # demo timeline: cf_set at +00:20
    assert await direct.call_forwarding(ASISH_LINE) == CFResult(status="unconditional")
    await mock.event(ASISH, "cf_clear")
    assert await direct.call_forwarding(ASISH_LINE) == CFResult(status="none")


async def test_reachability_flip(direct: DirectClient, mock) -> None:
    r = await direct.reachability(MOM_LINE)
    assert r.reachable is True and r.connectivity == ("DATA",)
    assert r.last_status_time is not None and r.last_status_time.tzinfo is not None
    await mock.advance(60)
    await mock.event(MOM, "unreachable")
    assert await direct.reachability(MOM_LINE) == ReachResult(
        reachable=False, connectivity=(), last_status_time=T0 + timedelta(minutes=1)
    )
    await mock.event(MOM, "reachable", connectivity=["SMS"])
    r = await direct.reachability(MOM_LINE)
    assert (r.reachable, r.connectivity) == (True, ("SMS",))


async def test_parallel_checks(direct: DirectClient) -> None:
    swap, cf = await asyncio.gather(direct.sim_swap_check(ASISH_LINE, 72), direct.call_forwarding(ASISH_LINE))
    assert swap == SimSwapResult(swapped=False) and cf == CFResult(status="none")


async def test_unknown_line_is_not_bound(direct: DirectClient) -> None:
    stranger = LineRef("line-x", "+16135550177")
    for call in (
        direct.sim_swap_check(stranger, 72),
        direct.sim_swap_date(stranger),
        direct.call_forwarding(stranger),
        direct.reachability(stranger),
    ):
        with pytest.raises(CarrierError) as exc:
            await call
        assert (exc.value.reason_code, exc.value.status, exc.value.code) == (
            ReasonCode.NOT_BOUND,
            404,
            "IDENTIFIER_NOT_FOUND",
        )


async def test_permission_denied_is_carrier_error(make_direct: Callable[..., DirectClient]) -> None:
    limited = make_direct(client_id="limited")
    assert await limited.sim_swap_check(ASISH_LINE, 72) == SimSwapResult(swapped=False)
    with pytest.raises(CarrierError) as exc:
        await limited.reachability(ASISH_LINE)
    # the token endpoint already refuses the scope (400 invalid_scope); 403 from the API is in the fixtures
    assert exc.value.reason_code == ReasonCode.CARRIER_ERROR
    assert (exc.value.kind, exc.value.code) == ("oauth", "invalid_scope")
    await limited.aclose()


async def test_bad_secret_is_carrier_error(make_direct: Callable[..., DirectClient]) -> None:
    client = make_direct(secret="wrong")
    with pytest.raises(CarrierError) as exc:
        await client.sim_swap_check(ASISH_LINE, 72)
    assert exc.value.reason_code == ReasonCode.CARRIER_ERROR and exc.value.kind == "oauth"
    await client.aclose()


async def test_token_cached_per_scope(direct: DirectClient, mock_transport) -> None:
    seen: list[str] = []
    original = mock_transport.handle_async_request

    async def spy(request):  # type: ignore[no-untyped-def]
        seen.append(request.url.path)
        return await original(request)

    mock_transport.handle_async_request = spy  # type: ignore[method-assign]
    for _ in range(3):
        await direct.sim_swap_check(ASISH_LINE, 72)
    await direct.sim_swap_date(ASISH_LINE)
    assert seen.count("/oauth2/token") == 2  # one per scope (check, retrieve-date), not per call


@pytest.fixture
def binding(mock_transport, breakers) -> DirectClient:  # type: ignore[no-untyped-def]
    return DirectClient(
        mock_config(BASE, client_id="binding-page"),
        secret="local-dev-binding",
        transport=mock_transport,
        breakers=breakers,
    )


async def test_number_verify_over_mobile_data(binding: DirectClient, mock) -> None:
    url = binding.authorization_url(REDIRECT, state="s1")
    code = await mock.auth_code(url, "phone-asish")
    result = await binding.number_verify(code, redirect_uri=REDIRECT, e164=ASISH)
    assert result.verified is True and result.e164 == ASISH
    assert ASISH not in repr(result)

    code = await mock.auth_code(url, "phone-asish")
    result = await binding.number_verify(code, redirect_uri=REDIRECT, e164=MOM)
    assert result.verified is False and result.e164 is None

    code = await mock.auth_code(url, "phone-mom")
    shared = await binding.number_verify(code, redirect_uri=REDIRECT)
    assert shared.verified is True and shared.e164 == MOM


async def test_number_verify_over_wifi_is_not_bound(binding: DirectClient, mock) -> None:
    code = await mock.auth_code(binding.authorization_url(REDIRECT, state="s"), None)
    with pytest.raises(CarrierError) as exc:
        await binding.number_verify(code, redirect_uri=REDIRECT, e164=ASISH)
    assert exc.value.reason_code == ReasonCode.NOT_BOUND
    assert exc.value.code == "NUMBER_VERIFICATION.USER_NOT_AUTHENTICATED_BY_MOBILE_NETWORK"


async def test_number_verify_code_is_single_use(binding: DirectClient, mock) -> None:
    code = await mock.auth_code(binding.authorization_url(REDIRECT, state="s"), "phone-asish")
    await binding.number_verify(code, redirect_uri=REDIRECT, e164=ASISH)
    with pytest.raises(CarrierError) as exc:
        await binding.number_verify(code, redirect_uri=REDIRECT, e164=ASISH)
    assert exc.value.kind == "oauth" and exc.value.reason_code == ReasonCode.CARRIER_ERROR


async def test_sim_swap_subscription_delivers_and_unsubscribes(direct: DirectClient, mock) -> None:
    sink = "https://sink.mock.local/hooks/line-asish-7f3a"
    sub = await direct.subscribe("sim-swap", ASISH_LINE, sink, timedelta(days=1), now=T0)
    assert sub.startswith("sim-swap-subscriptions/")
    await mock.event(ASISH, "sim_swap")
    events = await mock.sink()
    assert [e["type"] for e in events] == ["org.camaraproject.sim-swap-subscriptions.v0.swapped"]
    assert events[0]["data"]["subscriptionId"] == sub.split("/", 1)[1]
    await direct.unsubscribe(sub)
    await direct.unsubscribe(sub)  # already gone → still fine
    with pytest.raises(CarrierError) as exc:  # duplicate while active → 409
        await direct.subscribe("sim-swap", ASISH_LINE, sink, timedelta(days=1), now=T0)
        await direct.subscribe("sim-swap", ASISH_LINE, sink, timedelta(days=1), now=T0)
    assert exc.value.status == 409


async def test_reachability_subscription(direct: DirectClient, mock) -> None:
    sink = "https://sink.mock.local/hooks/line-mom-91c2"
    sub = await direct.subscribe(
        "reachability-disconnected", MOM_LINE, sink, timedelta(hours=6), now=T0, sink_token="sink-secret-xyz"
    )
    assert sub.startswith("device-reachability-status-subscriptions/")
    await mock.event(MOM, "unreachable")
    types = [e["type"] for e in await mock.sink()]
    assert types == [
        "org.camaraproject.device-reachability-status-subscriptions.v0.reachability-disconnected"
    ]
    await direct.unsubscribe(sub)


async def test_subscription_expiry_in_the_past_is_refused(direct: DirectClient) -> None:
    with pytest.raises(CarrierError) as exc:
        await direct.subscribe(
            "sim-swap",
            ASISH_LINE,
            "https://sink.mock.local/h",
            timedelta(hours=1),
            now=T0 - timedelta(days=1),
        )
    assert (exc.value.status, exc.value.code) == (400, "OUT_OF_RANGE")


def test_unsubscribe_rejects_foreign_ids() -> None:
    client = DirectClient(mock_config(BASE), secret="x")
    with pytest.raises(ValueError):
        asyncio.run(client.unsubscribe("not-a-subscription"))
