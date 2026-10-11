"""`/_admin/*` — the mock's control surface (08 §3). Not CAMARA; a simulation aid, mounted only when
`MOCK_ADMIN=1`. Every change goes through `Runtime`, so the same calls give the same state.

G1 (doc 11 §10): with `MOCK_ADMIN_TOKEN` set, every mutating route (POST, DELETE) needs `Authorization: Bearer`.
The reads stay open: Tower (`TOWER_CLOCK_URL`) and Alerts (`alerts/clock.py`) read `GET /_admin/clock`.
G2: a line is addressed by its E.164 or by its opaque `ref` (`line:<16 hex>`); `GET /_admin/state?view=refs`
is the dump without a number in it."""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import AliasChoices, BaseModel, Field

from mock_carrier.clock import iso, parse_iso
from mock_carrier.runtime import AdminError
from mock_carrier.scenarios import ScenarioError, list_scenarios
from mock_carrier.state import Line

if TYPE_CHECKING:
    from mock_carrier.runtime import Runtime


class LoadScenario(BaseModel):
    name: str = Field(examples=["demo"])
    variant: str | None = Field(default=None, examples=["recovered"])


class ClockChange(BaseModel):
    now: str | None = Field(
        default=None, description="RFC 3339 time to set", examples=["2026-10-05T14:12:00Z"]
    )
    advance_s: float | None = Field(default=None, ge=0, description="seconds to move forward", examples=[720])


class LineEvent(BaseModel):
    event: Literal["sim_swap", "cf_set", "cf_clear", "reachable", "unreachable"] = Field(
        validation_alias=AliasChoices("event", "type"), description="`type` is accepted as an alias (06 §8)"
    )
    connectivity: list[Literal["DATA", "SMS"]] | None = None


class Fault(BaseModel):
    kind: Literal["timeout", "500", "429"]
    n: int = Field(default=1, ge=1, le=1000)


REF_PREFIX = "line:"


def refs_view(dump: dict[str, Any]) -> dict[str, Any]:
    """`/_admin/state` without a number: lines keyed by `ref`, no sink URLs, no deliveries, no sink inbox (G2)."""
    lines = {}
    for line in dump["lines"].values():
        body = {k: v for k, v in line.items() if k != "msisdn"}
        lines[body["ref"]] = body
    subs = {k: {f: v for f, v in s.items() if f != "sink"} for k, s in dump["subscriptions"].items()}
    keep = ("scenario", "variant", "clock", "calls", "timeline", "faults", "counters")
    return {**{k: dump[k] for k in keep}, "lines": lines, "subscriptions": subs}


def router(rt: Runtime) -> APIRouter:
    r = APIRouter(prefix="/_admin", tags=["mock: admin (simulation aid)"])

    def admin_bearer(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = rt.settings.admin_token
        if expected is None:
            return
        if authorization is None or not hmac.compare_digest(authorization, f"Bearer {expected}"):
            raise HTTPException(status_code=401, detail="admin token required (MOCK_ADMIN_TOKEN)")

    mutating = [Depends(admin_bearer)]

    def find_line(key: str) -> Line:
        line = rt.state.line_by_ref(key) if key.startswith(REF_PREFIX) else rt.state.lines.get(key)
        if line is None:
            raise HTTPException(status_code=404, detail="unknown line")
        return line

    def bad(exc: Exception) -> HTTPException:
        return HTTPException(status_code=400, detail=str(exc))

    @r.get("/scenarios", summary="List scenario files")
    async def scenarios() -> dict[str, Any]:
        return {"scenarios": list_scenarios(rt.settings.scenarios_dir), "loaded": rt.state.scenario}

    @r.post("/scenarios/load", summary="Reset all state to a scenario", dependencies=mutating)
    async def load(body: LoadScenario) -> dict[str, Any]:
        try:
            rt.load(body.name, body.variant)
        except ScenarioError as exc:
            raise bad(exc) from exc
        return {"scenario": rt.state.scenario, "variant": rt.state.variant, "clock": iso(rt.now())}

    @r.post("/clock", summary="Set or advance the clock; due timeline events fire", dependencies=mutating)
    async def clock(body: ClockChange) -> dict[str, Any]:
        if body.now is None and body.advance_s is None:
            raise bad(ValueError("give `now` or `advance_s`"))
        try:
            when = parse_iso(body.now) if body.now else None
            fired = await rt.set_clock(now=when, advance_s=body.advance_s)
        except (ValueError, AdminError) as exc:
            raise bad(exc) from exc
        return {"clock": iso(rt.now()), "fired": [f.dump() for f in fired]}

    @r.get("/clock", summary="Current mock time")
    async def get_clock() -> dict[str, Any]:
        return {"clock": iso(rt.now())}

    @r.post(
        "/lines/{line}/events",
        summary="Fire a line event (the line by E.164 or by its opaque ref); subscriptions are notified",
        dependencies=mutating,
    )
    async def line_event(line: str, body: LineEvent) -> dict[str, Any]:
        target = find_line(line)
        try:
            fired = await rt.fire(
                target.msisdn, body.event, connectivity=list(body.connectivity or []) or None
            )
        except AdminError as exc:
            raise bad(exc) from exc
        dumped = target.dump()
        if line.startswith(REF_PREFIX):  # asked by ref: answer without the number (G2)
            dumped.pop("msisdn")
        return {"fired": fired.dump(), "line": dumped}

    @r.post(
        "/faults", summary="Inject timeout / 500 / 429 for the next n CAMARA calls", dependencies=mutating
    )
    async def faults(body: Fault) -> dict[str, Any]:
        try:
            rt.add_fault(body.kind, body.n)
        except AdminError as exc:
            raise bad(exc) from exc
        return {"faults": list(rt.state.faults)}

    @r.delete("/faults", summary="Clear pending faults", dependencies=mutating)
    async def clear_faults() -> dict[str, Any]:
        rt.state.faults.clear()
        return {"faults": []}

    @r.get("/state", summary="Dump all state; `?view=refs`: lines by opaque ref, no number anywhere")
    async def state(view: Annotated[Literal["full", "refs"], Query()] = "full") -> dict[str, Any]:
        dump = rt.state.dump(rt.now())
        return refs_view(dump) if view == "refs" else dump

    @r.get("/sink", summary="CloudEvents delivered to the loopback sink (https://sink.mock.local/...)")
    async def sink() -> dict[str, Any]:
        return {"events": list(rt.state.sink_inbox)}

    return r
