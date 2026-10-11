"""08 §3 `MOCK_CLOCK` (decision 4, 2026-10-11): on AWS the mock's clock starts at wall time, offset 0, on every
scenario load (startup, `make seed-aws`, the demo UI's Reset); `POST /_admin/clock` moves it; locally the scenario
clock (2026-10-05T14:00Z) is unchanged. The wall is injected: no sleeps, no real clock read."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from mock_carrier.app import create_app
from mock_carrier.clock import WallClock, iso, parse_iso
from mock_carrier.settings import Settings, clock_mode
from mock_carrier.state import line_ref
from mock_carrier.testing import ASISH, BASE, Sink, cc_token

DATE = "/sim-swap/v2/retrieve-date"
CHECK = "/sim-swap/v2/check"
WALL_START = datetime(2026, 10, 11, 18, 30, 0, tzinfo=UTC)


class FakeWall:
    """A wall clock the test moves by hand."""

    def __init__(self, start: datetime) -> None:
        self.at = start

    def __call__(self) -> datetime:
        return self.at

    def tick(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)


def wall_client(settings: Settings, sink: Sink, wall: Callable[[], datetime]) -> httpx.AsyncClient:
    app = create_app(replace(settings, clock="wall"), sink_transport=sink.transport, wall=wall)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


async def latest_sim_change(c: httpx.AsyncClient, h: dict[str, str]) -> datetime:
    r = await c.post(DATE, json={"phoneNumber": ASISH}, headers=h)
    assert r.status_code == 200, r.text
    return parse_iso(r.json()["latestSimChange"])


@pytest.mark.integration
async def test_after_reset_in_wall_mode_a_sim_swap_is_within_60_s_of_wall_time(
    settings: Settings, sink: Sink
) -> None:
    wall = FakeWall(WALL_START)
    async with wall_client(settings, sink, wall) as c:
        assert parse_iso((await c.get("/_admin/clock")).json()["clock"]) == WALL_START  # startup load
        wall.tick(3 * 3600)  # hours pass on Fargate; the clock ticks with the wall
        r = await c.post("/_admin/scenarios/load", json={"name": "demo"})  # the Reset / seed-aws path
        assert r.status_code == 200 and parse_iso(r.json()["clock"]) == wall()
        wall.tick(600)  # ten real minutes after Reset, the presenter fires Moment 1
        h = await cc_token(c)
        r = await c.post(f"/_admin/lines/{line_ref(ASISH)}/events", json={"event": "sim_swap"})
        assert r.status_code == 200
        changed = await latest_sim_change(c, h)
        assert abs(changed - wall()) <= timedelta(seconds=60)
        assert (await c.post(CHECK, json={"phoneNumber": ASISH, "maxAge": 1}, headers=h)).json() == {
            "swapped": True
        }


@pytest.mark.integration
async def test_wall_mode_clock_moves_with_post_clock_and_reset_returns_it_to_wall(
    settings: Settings, sink: Sink
) -> None:
    wall = FakeWall(WALL_START)
    async with wall_client(settings, sink, wall) as c:
        r = await c.post("/_admin/clock", json={"advance_s": 720})
        assert parse_iso(r.json()["clock"]) == WALL_START + timedelta(seconds=720)
        wall.tick(30)  # still ticking, from the moved point
        assert parse_iso((await c.get("/_admin/clock")).json()["clock"]) == WALL_START + timedelta(
            seconds=750
        )
        await c.post("/_admin/clock", json={"now": "2027-01-01T09:00:00Z"})
        assert (await c.get("/_admin/clock")).json()["clock"] == "2027-01-01T09:00:00.000Z"
        await c.post("/_admin/scenarios/load", json={"name": "demo"})
        assert parse_iso((await c.get("/_admin/clock")).json()["clock"]) == wall()  # offset back to 0


@pytest.mark.integration
async def test_wall_mode_timeline_counts_from_the_load(settings: Settings, sink: Sink) -> None:
    """The demo timeline's `+00:12:00` sim swap is 12 minutes after the load, not after 2026-10-05."""
    wall = FakeWall(WALL_START)
    async with wall_client(settings, sink, wall) as c:
        h = await cc_token(c)
        before = await latest_sim_change(c, h)
        assert before == parse_iso("2026-09-01T10:00:00Z")  # absolute times in the file stay as written
        fired = (await c.post("/_admin/clock", json={"advance_s": 12 * 60})).json()["fired"]
        assert [e["event"] for e in fired] == ["sim_swap"]
        assert fired[0]["at"] == iso(WALL_START + timedelta(minutes=12))


@pytest.mark.integration
async def test_scenario_mode_is_unchanged(client: httpx.AsyncClient) -> None:
    """Local (default `MOCK_CLOCK`): the scenario clock, whatever the wall says."""
    r = await client.post("/_admin/scenarios/load", json={"name": "demo"})
    assert r.json()["clock"] == "2026-10-05T14:00:00.000Z"
    assert (await client.get("/_admin/clock")).json()["clock"] == "2026-10-05T14:00:00.000Z"


@pytest.mark.integration
async def test_scenario_mode_never_reads_the_wall(settings: Settings, sink: Sink) -> None:
    def no_wall() -> datetime:
        raise AssertionError("MOCK_CLOCK=scenario must not read the wall clock")

    app = create_app(settings, sink_transport=sink.transport, wall=no_wall)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
        await c.post("/_admin/scenarios/load", json={"name": "demo"})
        await c.post("/_admin/clock", json={"advance_s": 60})
        assert (await c.get("/_admin/clock")).json()["clock"] == "2026-10-05T14:01:00.000Z"


@pytest.mark.unit
def test_clock_mode_setting() -> None:
    assert clock_mode("") == "scenario" and clock_mode("scenario") == "scenario"
    assert clock_mode("WALL") == "wall" and clock_mode(" wall ") == "wall"
    with pytest.raises(ValueError, match="MOCK_CLOCK"):
        clock_mode("system")
    assert Settings().clock == "scenario"


@pytest.mark.unit
def test_wall_clock_unit() -> None:
    wall = FakeWall(WALL_START)
    c = WallClock(wall)
    assert c.now() == WALL_START and c.offset == timedelta(0)
    assert c.advance(60) == WALL_START + timedelta(seconds=60)
    c.set(WALL_START - timedelta(days=1))
    assert c.now() == WALL_START - timedelta(days=1)
    with pytest.raises(ValueError):
        c.set(datetime(2026, 1, 1))  # noqa: DTZ001 — the naive datetime is the point of the test
