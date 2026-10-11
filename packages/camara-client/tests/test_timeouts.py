"""Profiles: request = 300 ms, no retry; proactive = 5 s, one retry (05 §5, 06 §8). Injected via the
mock's `/_admin/faults` (`timeout` sleeps 0.5 s, `500` answers at once)."""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest
from camara_client import (
    PROFILES,
    CarrierError,
    DirectClient,
    GatewayClient,
    LineRef,
    SimSwapResult,
    profile_for,
)
from mock_carrier.testing import ASISH
from tower_policy import ReasonCode

LINE = LineRef("line-asish", ASISH)


@pytest.mark.unit
def test_profiles() -> None:
    assert profile_for("request") == (0.3, 0)
    assert profile_for("proactive") == (5.0, 1)
    assert set(PROFILES) == {"request", "proactive"}
    with pytest.raises(ValueError):
        profile_for("batch")


@pytest.mark.parametrize("kind", ["direct", "gateway"])
@pytest.mark.integration
async def test_request_profile_times_out_within_350ms(
    kind: str, make_direct: Callable[..., DirectClient], make_gateway: Callable[..., GatewayClient], mock
) -> None:
    client = make_direct() if kind == "direct" else make_gateway()
    assert await client.sim_swap_check(LINE, 72) == SimSwapResult(swapped=False)  # token cached, warm
    before = await mock.calls()
    await mock.fault("timeout", 1)
    start = time.perf_counter()
    with pytest.raises(CarrierError) as exc:
        await client.sim_swap_check(LINE, 72)
    elapsed = time.perf_counter() - start
    assert elapsed < 0.350, f"request-profile call took {elapsed * 1000:.0f} ms"
    assert (exc.value.reason_code, exc.value.kind) == (ReasonCode.STALE_DATA, "timeout")
    assert await mock.calls() == before + 1  # no retry on the request path, ever
    await client.aclose()


@pytest.mark.integration
async def test_request_profile_cold_token_still_bounded(
    make_direct: Callable[..., DirectClient], mock
) -> None:
    client = make_direct()
    await mock.fault("timeout", 1)
    start = time.perf_counter()
    with pytest.raises(CarrierError):
        await client.call_forwarding(LINE)
    assert time.perf_counter() - start < 0.350
    await client.aclose()


@pytest.mark.integration
async def test_request_profile_never_retries_a_500(make_direct: Callable[..., DirectClient], mock) -> None:
    client = make_direct()
    await client.sim_swap_check(LINE, 72)
    before = await mock.calls()
    await mock.fault("500", 1)
    with pytest.raises(CarrierError) as exc:
        await client.sim_swap_check(LINE, 72)
    assert exc.value.reason_code == ReasonCode.CARRIER_ERROR and exc.value.retryable
    assert await mock.calls() == before + 1
    await client.aclose()


@pytest.mark.integration
async def test_proactive_retries_once_then_errors(make_direct: Callable[..., DirectClient], mock) -> None:
    client = make_direct(profile="proactive", client_id="alerts")
    await client.sim_swap_check(LINE, 72)
    before = await mock.calls()
    await mock.fault("500", 2)
    with pytest.raises(CarrierError) as exc:
        await client.sim_swap_check(LINE, 72)
    assert exc.value.reason_code == ReasonCode.CARRIER_ERROR and exc.value.status == 500
    assert await mock.calls() == before + 2  # one try + one retry
    await client.aclose()


@pytest.mark.integration
async def test_proactive_retry_recovers(make_direct: Callable[..., DirectClient], mock) -> None:
    client = make_direct(profile="proactive", client_id="alerts")
    await mock.fault("500", 1)
    assert await client.sim_swap_check(LINE, 72) == SimSwapResult(swapped=False)
    await mock.fault("429", 1)
    assert (await client.reachability(LINE)).reachable is True
    await client.aclose()


@pytest.mark.integration
async def test_proactive_tolerates_a_slow_carrier(make_direct: Callable[..., DirectClient], mock) -> None:
    client = make_direct(profile="proactive", client_id="alerts")
    await mock.fault("timeout", 1)  # 0.5 s: over the request budget, well inside 5 s
    assert await client.sim_swap_check(LINE, 72) == SimSwapResult(swapped=False)
    await client.aclose()


@pytest.mark.integration
async def test_429_on_request_path_is_stale_data(make_direct: Callable[..., DirectClient], mock) -> None:
    client = make_direct()
    await mock.fault("429", 1)
    with pytest.raises(CarrierError) as exc:
        await client.call_forwarding(LINE)
    assert exc.value.reason_code == ReasonCode.STALE_DATA
    await client.aclose()
