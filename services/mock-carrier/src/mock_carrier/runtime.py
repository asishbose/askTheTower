"""The mock's runtime: settings + specs + clock + state + webhook sender, and the operations that
change state (scenario load, clock moves, line events, faults). Routers and the admin API call
these; nothing else mutates state."""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx

from mock_carrier.clock import Clock, iso
from mock_carrier.oauth import ClientRegistry, SimulationConflict
from mock_carrier.scenarios import load_scenario
from mock_carrier.settings import Settings
from mock_carrier.specs import SpecSet
from mock_carrier.state import EVENT_NAMES, FAULT_KINDS, Line, State, Subscription, line_ref
from mock_carrier.webhooks import WebhookSender

SIM_SWAP_SUBS = "sim-swap-subscriptions"
REACH_SUBS = "device-reachability-status-subscriptions"

T_SWAPPED = "org.camaraproject.sim-swap-subscriptions.v0.swapped"
T_SIM_ENDED = "org.camaraproject.sim-swap-subscriptions.v0.subscription-ended"
_R = "org.camaraproject.device-reachability-status-subscriptions.v0."
T_REACH_DATA = _R + "reachability-data"
T_REACH_SMS = _R + "reachability-sms"
T_REACH_DISCONNECTED = _R + "reachability-disconnected"
T_REACH_ENDED = _R + "subscription-ended"

ENDED_TYPE = {SIM_SWAP_SUBS: T_SIM_ENDED, REACH_SUBS: T_REACH_ENDED}

log = logging.getLogger("mock_carrier")


class AdminError(ValueError):
    """Bad admin request (unknown line, event, fault kind, scenario)."""


@dataclass
class FiredEvent:
    at: datetime
    line: str
    event: str

    def dump(self) -> dict[str, Any]:
        return {"at": iso(self.at), "line": line_ref(self.line), "event": self.event}


class Runtime:
    def __init__(self, settings: Settings, *, sink_transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.specs = SpecSet(
            settings.specs_dir,
            base_url=settings.base_url,
            discovery_url=settings.base_url.rstrip("/") + "/oauth2/.well-known/openid-configuration",
        )
        self.clients = ClientRegistry.from_file(settings.clients_file)
        self.state = State()
        self.clock = Clock(datetime.fromisoformat("2026-01-01T00:00:00+00:00"))
        self.sender = WebhookSender(self, transport=sink_transport)
        self._lock = asyncio.Lock()
        self._jitter = random.Random() if settings.jitter_ms > 0 else None  # noqa: S311 — latency jitter, not crypto
        self.load(settings.scenario)
        self._check_assumed_client_id()

    def _check_assumed_client_id(self) -> None:
        """08 §3: an unknown `MOCK_ASSUME_CLIENT_ID` fails at startup, not at bind time."""
        if not self.settings.assume_mobile_data:
            return
        if self.state.line_for_client_id(self.settings.assume_client_id) is None:
            raise ValueError(
                f"MOCK_ASSUME_CLIENT_ID={self.settings.assume_client_id!r} matches no line's "
                "mobile_data_client_ids in the loaded scenario"
            )
        log.warning("SIMULATION: MOCK_ASSUME_MOBILE_DATA=1 (client id %s)", self.settings.assume_client_id)

    def attributed_client_id(self, header_value: str | None) -> str | None:
        """The client id the 'network' attributes an authorize request to (08 §3).

        Raises `SimulationConflict` when the assumed-mobile-data flag is on and the request also
        carries `X-Mock-Client-Id`: one simulation at a time."""
        if self.settings.assume_mobile_data:
            if header_value:
                raise SimulationConflict("one simulation at a time")
            return self.settings.assume_client_id
        return header_value or None

    # --- scenarios -------------------------------------------------------------------------------
    def load(self, name: str, variant: str | None = None) -> None:
        sc = load_scenario(self.settings.scenarios_dir, name, variant)
        self.state = State(scenario=sc.name, variant=sc.variant, lines=sc.lines, timeline=sc.timeline)
        self.clock = Clock(sc.clock)

    def now(self) -> datetime:
        return self.clock.now()

    def line(self, msisdn: str) -> Line | None:
        return self.state.lines.get(msisdn)

    # --- clock -----------------------------------------------------------------------------------
    async def set_clock(
        self, *, now: datetime | None = None, advance_s: float | None = None
    ) -> list[FiredEvent]:
        async with self._lock:
            if now is not None:
                self.clock.set(now)
            if advance_s:
                if advance_s < 0:
                    raise AdminError("advance_s must be >= 0")
                self.clock.advance(advance_s)
            return await self._catch_up()

    async def _catch_up(self) -> list[FiredEvent]:
        """Fire due timeline events in order, then expire subscriptions."""
        fired: list[FiredEvent] = []
        now = self.clock.now()
        for ev in self.state.timeline:
            if ev.fired or ev.at > now:
                continue
            ev.fired = True
            await self._apply_event(ev.line, ev.event, at=ev.at, connectivity=ev.connectivity)
            fired.append(FiredEvent(at=ev.at, line=ev.line, event=ev.event))
        for sub in sorted(self.state.subscriptions.values(), key=lambda s: s.id):
            if sub.is_active() and sub.expires_at is not None and sub.expires_at <= now:
                await self.end_subscription(sub, "SUBSCRIPTION_EXPIRED", status="EXPIRED")
        return fired

    # --- line events -----------------------------------------------------------------------------
    async def fire(self, msisdn: str, event: str, *, connectivity: list[str] | None = None) -> FiredEvent:
        if event not in EVENT_NAMES:
            raise AdminError(f"unknown event {event!r}; expected one of {EVENT_NAMES}")
        if msisdn not in self.state.lines:
            raise AdminError("unknown line")
        async with self._lock:
            now = self.clock.now()
            await self._apply_event(msisdn, event, at=now, connectivity=connectivity)
            return FiredEvent(at=now, line=msisdn, event=event)

    async def _apply_event(
        self, msisdn: str, event: str, *, at: datetime, connectivity: list[str] | None
    ) -> None:
        line = self.state.lines[msisdn]
        if event == "sim_swap":
            line.sim_change_at = at
            await self._fan_out(SIM_SWAP_SUBS, msisdn, T_SWAPPED)
        elif event == "cf_set":
            line.call_forwarding = "unconditional"
        elif event == "cf_clear":
            line.call_forwarding = "none"
        elif event == "reachable":
            line.reachable = True
            line.connectivity = list(connectivity) if connectivity else ["DATA"]
            line.last_status_at = at
            await self._fan_out(
                REACH_SUBS, msisdn, T_REACH_DATA if "DATA" in line.connectivity else T_REACH_SMS
            )
        elif event == "unreachable":
            line.reachable = False
            line.connectivity = []
            line.last_status_at = at
            await self._fan_out(REACH_SUBS, msisdn, T_REACH_DISCONNECTED)

    def reachability_type(self, line: Line) -> str:
        if not line.reachable:
            return T_REACH_DISCONNECTED
        return T_REACH_DATA if "DATA" in line.connectivity else T_REACH_SMS

    async def _fan_out(self, api: str, msisdn: str, event_type: str) -> None:
        subs = [
            s
            for s in sorted(self.state.subscriptions.values(), key=lambda s: s.id)
            if s.api == api and s.msisdn == msisdn and s.is_active() and event_type in s.types
        ]
        for sub in subs:
            await self.notify(sub, event_type)

    async def notify(self, sub: Subscription, event_type: str) -> None:
        """Send one event for `sub` and apply `subscriptionMaxEvents`."""
        await self.sender.send(sub, event_type, {"subscriptionId": sub.id})
        sub.events_sent += 1
        if sub.max_events is not None and sub.events_sent >= sub.max_events:
            await self.end_subscription(sub, "MAX_EVENTS_REACHED", status="EXPIRED")

    async def end_subscription(self, sub: Subscription, reason: str, *, status: str) -> None:
        sub.status = status
        await self.sender.send(
            sub, ENDED_TYPE[sub.api], {"subscriptionId": sub.id, "terminationReason": reason}
        )

    # --- faults and latency ----------------------------------------------------------------------
    def add_fault(self, kind: str, n: int) -> None:
        if kind not in FAULT_KINDS:
            raise AdminError(f"unknown fault kind {kind!r}; expected one of {FAULT_KINDS}")
        if n < 1 or n > 1000:
            raise AdminError("n must be 1..1000")
        self.state.faults.extend([kind] * n)

    def take_fault(self) -> str | None:
        return self.state.faults.pop(0) if self.state.faults else None

    async def jitter(self) -> None:
        if self._jitter is not None:
            await asyncio.sleep(self._jitter.uniform(0, self.settings.jitter_ms) / 1000.0)

    # --- subscriptions helpers -------------------------------------------------------------------
    def default_expiry(self) -> datetime:
        return self.clock.now() + timedelta(days=30)
