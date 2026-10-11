"""In-memory carrier state: lines, subscriptions, faults, delivery log, OAuth codes. Loaded from a
scenario; dumped by `GET /_admin/state`. No wall-clock, no randomness — a dump is reproducible."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

from mock_carrier.clock import iso

CallForwarding = Literal["none", "unconditional", "conditional"]
EventName = Literal["sim_swap", "cf_set", "cf_clear", "reachable", "unreachable"]
EVENT_NAMES: tuple[str, ...] = ("sim_swap", "cf_set", "cf_clear", "reachable", "unreachable")
FAULT_KINDS: tuple[str, ...] = ("timeout", "500", "429")


def line_ref(msisdn: str) -> str:
    """Opaque, stable reference to a line for tokens and logs — never the number itself."""
    return "line:" + hashlib.sha256(msisdn.encode()).hexdigest()[:16]


@dataclass
class Line:
    msisdn: str
    sim_change_at: datetime | None
    call_forwarding: CallForwarding | list[str]
    reachable: bool
    connectivity: list[str]
    mobile_data_client_ids: list[str]
    last_status_at: datetime

    def forwarding_types(self) -> list[str]:
        if isinstance(self.call_forwarding, list):
            return list(self.call_forwarding)
        return {
            "none": ["inactive"],
            "unconditional": ["unconditional"],
            "conditional": ["conditional_busy", "conditional_not_reachable", "conditional_no_answer"],
        }[self.call_forwarding]

    def unconditional_active(self) -> bool:
        return "unconditional" in self.forwarding_types()

    def dump(self) -> dict[str, Any]:
        return {
            "msisdn": self.msisdn,
            "ref": line_ref(self.msisdn),
            "sim_change_at": iso(self.sim_change_at) if self.sim_change_at else None,
            "call_forwarding": self.call_forwarding,
            "reachable": self.reachable,
            "connectivity": list(self.connectivity),
            "mobile_data_client_ids": list(self.mobile_data_client_ids),
            "last_status_at": iso(self.last_status_at),
        }


@dataclass
class Subscription:
    id: str
    api: str  # sim-swap-subscriptions | device-reachability-status-subscriptions
    client_id: str
    msisdn: str
    sink: str
    protocol: str
    types: list[str]
    config: dict[str, Any]
    protocol_settings: dict[str, Any] | None
    sink_credential: dict[str, Any] | None
    starts_at: datetime
    expires_at: datetime | None
    max_events: int | None
    status: str = "ACTIVE"  # ACTIVE | EXPIRED | DELETED
    events_sent: int = 0

    def is_active(self) -> bool:
        return self.status == "ACTIVE"

    def public(self) -> dict[str, Any]:
        """The CAMARA `Subscription` response body."""
        body: dict[str, Any] = {
            "id": self.id,
            "protocol": self.protocol,
            "sink": self.sink,
            "types": list(self.types),
            "config": self.config,
            "startsAt": iso(self.starts_at),
            "status": self.status,
        }
        if self.expires_at is not None:
            body["expiresAt"] = iso(self.expires_at)
        if self.protocol_settings:
            body["protocolSettings"] = self.protocol_settings
        return body

    def dump(self) -> dict[str, Any]:
        d = self.public()
        d.update(
            {
                "api": self.api,
                "client_id": self.client_id,
                "line": line_ref(self.msisdn),
                "eventsSent": self.events_sent,
            }
        )
        return d


@dataclass
class Delivery:
    id: str
    subscription_id: str
    event_id: str
    type: str
    sink: str
    attempts: int
    delivered: bool
    status_code: int | None
    error: str | None
    at: str


@dataclass
class TimelineEvent:
    at: datetime
    line: str
    event: str
    connectivity: list[str] | None = None
    fired: bool = False

    def dump(self) -> dict[str, Any]:
        return {
            "at": iso(self.at),
            "line": line_ref(self.line),
            "event": self.event,
            "connectivity": self.connectivity,
            "fired": self.fired,
        }


@dataclass
class AuthCode:
    code: str
    client_id: str
    redirect_uri: str
    scope: str
    msisdn: str | None
    attributed_client_id: str | None
    expires_at: datetime
    used: bool = False


@dataclass
class CibaRequest:
    auth_req_id: str
    client_id: str
    scope: str
    msisdn: str | None
    attributed_client_id: str | None
    expires_at: datetime


@dataclass
class State:
    scenario: str = ""
    variant: str | None = None
    lines: dict[str, Line] = field(default_factory=dict)
    timeline: list[TimelineEvent] = field(default_factory=list)
    subscriptions: dict[str, Subscription] = field(default_factory=dict)
    deliveries: list[Delivery] = field(default_factory=list)
    faults: list[str] = field(default_factory=list)  # FIFO of fault kinds, one per CAMARA call
    sink_inbox: list[dict[str, Any]] = field(default_factory=list)  # loopback sink (admin aid)
    auth_codes: dict[str, AuthCode] = field(default_factory=dict)
    ciba_requests: dict[str, CibaRequest] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    calls: int = 0

    def next_id(self, kind: str) -> str:
        n = self.counters.get(kind, 0) + 1
        self.counters[kind] = n
        return f"{kind}-{n:04d}"

    def line_by_ref(self, ref: str) -> Line | None:
        for line in self.lines.values():
            if line_ref(line.msisdn) == ref:
                return line
        return None

    def line_for_client_id(self, client_id: str) -> Line | None:
        for line in self.lines.values():
            if client_id in line.mobile_data_client_ids:
                return line
        return None

    def dump(self, now: datetime) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "variant": self.variant,
            "clock": iso(now),
            "calls": self.calls,
            "lines": {m: line.dump() for m, line in sorted(self.lines.items())},
            "timeline": [e.dump() for e in self.timeline],
            "subscriptions": {k: s.dump() for k, s in sorted(self.subscriptions.items())},
            "deliveries": [asdict(d) for d in self.deliveries],
            "faults": list(self.faults),
            "sink_inbox": list(self.sink_inbox),
            "counters": dict(sorted(self.counters.items())),
        }
