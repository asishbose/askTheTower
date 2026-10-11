"""Breaker per base URL (05 §5): 5 consecutive 5xx → open for 60 s → STALE_DATA immediately, no
carrier call; after 60 s (mocked clock) half-open → one trial; success closes, failure re-opens."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from camara_client import BreakerRegistry, CarrierError, CircuitBreaker, DirectClient, LineRef, SimSwapResult
from camara_client.testing import monotonic_clock
from mock_carrier.testing import ASISH
from tower_policy import ReasonCode

LINE = LineRef("line-asish", ASISH)
Clock = tuple[Callable[[], float], Callable[[float], None]]


@pytest.mark.integration
async def test_opens_after_five_500s_and_recovers(
    make_direct: Callable[..., DirectClient], mock, clock: Clock
) -> None:
    _, advance = clock
    client = make_direct()
    await client.sim_swap_check(LINE, 72)  # token cached
    await mock.fault("500", 5)
    for _ in range(5):
        with pytest.raises(CarrierError) as exc:
            await client.sim_swap_check(LINE, 72)
        assert exc.value.reason_code == ReasonCode.CARRIER_ERROR
    assert client.breaker.state == "open"

    before = await mock.calls()
    with pytest.raises(CarrierError) as exc:
        await client.sim_swap_check(LINE, 72)
    assert (exc.value.reason_code, exc.value.retryable, exc.value.kind) == (
        ReasonCode.STALE_DATA,
        False,
        "breaker_open",
    )
    assert await mock.calls() == before  # the carrier was not called

    advance(59.9)
    with pytest.raises(CarrierError):
        await client.call_forwarding(LINE)
    advance(0.1)
    assert client.breaker.state == "half_open"
    assert await client.sim_swap_check(LINE, 72) == SimSwapResult(swapped=False)
    assert client.breaker.state == "closed"
    await client.aclose()


@pytest.mark.integration
async def test_half_open_failure_reopens(
    make_direct: Callable[..., DirectClient], mock, clock: Clock
) -> None:
    _, advance = clock
    client = make_direct()
    await mock.fault("500", 6)
    for _ in range(5):
        with pytest.raises(CarrierError):
            await client.sim_swap_check(LINE, 72)
    advance(60)
    with pytest.raises(CarrierError) as exc:
        await client.sim_swap_check(LINE, 72)  # the trial gets the 6th 500
    assert exc.value.status == 500
    assert client.breaker.state == "open"
    advance(60)
    assert (await client.sim_swap_check(LINE, 72)).swapped is False
    await client.aclose()


@pytest.mark.integration
async def test_4xx_and_429_do_not_open(make_direct: Callable[..., DirectClient], mock) -> None:
    client = make_direct()
    await mock.fault("500", 4)
    await mock.fault("429", 1)
    await mock.fault("500", 4)
    for _ in range(9):
        with pytest.raises(CarrierError):
            await client.sim_swap_check(LINE, 72)
    assert client.breaker.state == "closed"
    await client.aclose()


@pytest.mark.unit
def test_breaker_unit() -> None:
    clock, advance = monotonic_clock()
    b = CircuitBreaker(clock=clock)
    for _ in range(4):
        b.on_server_error()
    b.on_success()
    for _ in range(4):
        b.on_server_error()
    assert b.state == "closed"
    b.on_server_error()
    assert b.state == "open"
    with pytest.raises(CarrierError):
        b.before_call()
    advance(60)
    b.before_call()  # the one trial
    with pytest.raises(CarrierError):
        b.before_call()  # a second concurrent call while the trial is out
    b.on_no_answer()  # trial timed out
    assert b.state == "open"
    advance(60)
    b.before_call()
    b.on_success()
    assert b.state == "closed"


@pytest.mark.unit
def test_registry_is_per_base_url() -> None:
    clock, _ = monotonic_clock()
    reg = BreakerRegistry(clock=clock)
    a, b = reg.for_url("https://a.example/"), reg.for_url("https://b.example")
    assert a is reg.for_url("https://a.example") and a is not b
    for _ in range(5):
        a.on_server_error()
    assert (a.state, b.state) == ("open", "closed")
