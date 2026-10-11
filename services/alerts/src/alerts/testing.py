"""Test and showcase helpers: a fake carrier, a seeded world, and the in-process wiring of the mock carrier
with the Alerts app (the mock's webhooks are delivered to Alerts over `httpx.ASGITransport`). Not used by the
running service. The numbers are the 555-01xx fiction of `scenarios/demo.yaml`; they live in memory only.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from camara_client import CarrierError, CFResult, LineRef, ReachResult, SimSwapResult, SubscriptionKind
from tower_audit import AuditRecord
from tower_consent import (
    EscalationStep,
    LocalLineIdHasher,
    LocalMsisdnCipher,
    Store,
    Watch,
    bind_line,
    ensure_user,
    grant,
    set_alert_phone,
    upsert_watch,
)
from tower_consent import tables as T
from tower_consent.models import Profile
from tower_policy import ReasonCode

from alerts.clock import Clock, FixedClock
from alerts.config import Settings
from alerts.context import AlertsService
from alerts.send_backends import LogSender

ASISH = "+16135550101"
MOM = "+16135550102"
NEIGHBOUR = "+16135550103"
MOM_BACKUP = "+16135550104"
PARTNER = "+16135550105"
LINE_KEY = b"k" * 32
MSISDN_KEY = b"m" * 32
T0 = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)  # 10:00 in Toronto: daytime
BEARER = "test-internal-bearer"


@dataclass
class FakeLine:
    sim_change_at: datetime | None = None
    call_forwarding: str = "none"
    reachable: bool = True
    connectivity: tuple[str, ...] = ("DATA",)
    last_status_time: datetime | None = None


class FakeCarrier:
    """`CarrierClient` with settable facts and injectable failures. Knows time through the shared clock."""

    def __init__(self, clock: FixedClock) -> None:
        self.clock = clock
        self.lines: dict[str, FakeLine] = {}
        self.fail: dict[str, int] = {}  # call name or "*" → remaining failures (-1 = forever)
        self.calls: list[str] = []
        self.subscriptions: dict[str, tuple[str, str, str]] = {}
        self._n = 0

    def _maybe_fail(self, name: str) -> None:
        self.calls.append(name)
        for key in (name, "*"):
            left = self.fail.get(key, 0)
            if left:
                self.fail[key] = left - 1 if left > 0 else -1
                raise CarrierError(ReasonCode.CARRIER_ERROR, True, status=503, code="UNAVAILABLE")

    def _line(self, ref: LineRef) -> FakeLine:
        if ref.e164 not in self.lines:
            raise CarrierError(ReasonCode.NOT_BOUND, False, status=404, code="IDENTIFIER_NOT_FOUND")
        return self.lines[ref.e164]

    async def sim_swap_check(self, line: LineRef, max_age_h: int) -> SimSwapResult:
        self._maybe_fail("sim_swap_check")
        ln = self._line(line)
        at = ln.sim_change_at
        return SimSwapResult(swapped=at is not None and self.clock.at - at <= timedelta(hours=max_age_h))

    async def sim_swap_date(self, line: LineRef) -> datetime | None:
        self._maybe_fail("sim_swap_date")
        return self._line(line).sim_change_at

    async def call_forwarding(self, line: LineRef) -> CFResult:
        self._maybe_fail("call_forwarding")
        return CFResult(status=self._line(line).call_forwarding)  # type: ignore[arg-type,unused-ignore]

    async def number_verify(self, auth_code: str, *, redirect_uri: str, e164: str | None = None) -> Any:
        raise NotImplementedError

    async def reachability(self, line: LineRef) -> ReachResult:
        self._maybe_fail("reachability")
        ln = self._line(line)
        return ReachResult(
            reachable=ln.reachable,
            connectivity=ln.connectivity if ln.reachable else (),  # type: ignore[arg-type,unused-ignore]
            last_status_time=ln.last_status_time,
        )

    async def subscribe(
        self,
        kind: SubscriptionKind,
        line: LineRef,
        sink_url: str,
        ttl: timedelta,
        *,
        now: datetime,
        sink_token: str | None = None,
    ) -> str:
        self._maybe_fail("subscribe")
        self._n += 1
        sid = f"fake/sub-{self._n}"
        self.subscriptions[sid] = (kind, line.line_id, sink_url)
        return sid

    async def unsubscribe(self, subscription_id: str) -> None:
        self.calls.append("unsubscribe")
        self.subscriptions.pop(subscription_id, None)

    async def aclose(self) -> None:
        return None

    # --- scripting -------------------------------------------------------------------------------------
    def set_reachable(self, e164: str, reachable: bool) -> None:
        ln = self.lines[e164]
        ln.reachable = reachable
        ln.last_status_time = self.clock.at

    def swap(self, e164: str) -> None:
        self.lines[e164].sim_change_at = self.clock.at


@dataclass
class World:
    """Users, lines, grants and watches for the tests and the showcase.

    - Mom (`user-mom`) owns MOM; her backup phone (MOM_BACKUP) is registered only when `mom_backup=True`.
    - Asish (`user-asish`) owns ASISH and watches Mom's line under the alias "mom".
    - The neighbour (`user-neighbour`) has an alert phone (NEIGHBOUR) and no line; so does the partner
      (`user-partner`, PARTNER) — the transplant story watches Asish's line.
    """

    svc: AlertsService
    store: Store
    hasher: LocalLineIdHasher
    cipher: LocalMsisdnCipher
    now: datetime
    lines: dict[str, str] = field(default_factory=dict)  # e164 → line_id

    def line_id(self, e164: str) -> str:
        lid: str = self.hasher.line_id(e164)
        return lid

    def bind(self, user_id: str, e164: str) -> str:
        ensure_user(self.store, user_id, now=self.now)
        line = bind_line(self.store, self.hasher, self.cipher, user_id, e164, "auth_code", now=self.now)
        lid: str = line.line_id
        self.lines[e164] = lid
        return lid

    def user(self, user_id: str, alert_phone: str | None = None, backup_phone: str | None = None) -> None:
        ensure_user(self.store, user_id, now=self.now)
        if alert_phone:
            set_alert_phone(self.store, self.cipher, user_id, alert_phone)
        if backup_phone:
            self.store.update(
                T.USERS,
                {"user_id": user_id},
                "SET backup_phone_enc = :b",
                values={":b": self.cipher.encrypt(backup_phone)},
            )

    def grant_watch(self, line_e164: str, owner: str, grantee: str, alias: str) -> None:
        grant(self.store, self.lines[line_e164], grantee, "watch", alias, granted_by=owner, now=self.now)

    def watch(
        self,
        line_e164: str,
        watcher: str,
        profile: Profile,
        escalation: list[tuple[str, bool]] | None = None,
    ) -> Watch:
        w = Watch(
            line_id=self.lines[line_e164],
            watcher_user_id=watcher,
            profile=profile,
            escalation=[EscalationStep(user_id=u, requires_ack=a) for u, a in (escalation or [])],
        )
        return upsert_watch(self.store, w)

    def standard(self, *, mom_backup: bool = False) -> None:
        self.user("user-mom", backup_phone=MOM_BACKUP if mom_backup else None)
        self.bind("user-mom", MOM)
        self.user("user-asish")
        self.bind("user-asish", ASISH)
        self.user("user-neighbour", alert_phone=NEIGHBOUR)
        self.user("user-partner", alert_phone=PARTNER)
        self.grant_watch(MOM, "user-mom", "user-asish", "mom")


def make_service(
    store: Store,
    carrier: Any,
    clock: Clock,
    *,
    sender: LogSender | None = None,
    settings: Settings | None = None,
) -> AlertsService:
    return AlertsService(
        store=store,
        hasher=LocalLineIdHasher(LINE_KEY),
        cipher=LocalMsisdnCipher(MSISDN_KEY),
        carrier=carrier,
        sender=sender or LogSender(),
        clock=clock,
        settings=settings or Settings(internal_bearer=BEARER, hooks_base_url="https://alerts.test"),
    )


class LazyASGI:
    """An ASGI app whose target is set later — the mock needs Alerts' transport before Alerts exists."""

    def __init__(self) -> None:
        self.app: Callable[..., Awaitable[None]] | None = None

    async def __call__(self, scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
        assert self.app is not None, "LazyASGI target not set"
        await self.app(scope, receive, send)


def asgi_transport(app: Any) -> httpx.ASGITransport:
    return httpx.ASGITransport(app=app)


def audit_rows(store: Store, line_id: str) -> list[AuditRecord]:
    """Every audit row of a line, oldest first (the chain head item excluded)."""
    items = store.query(T.AUDIT, "line_id = :l", {":l": line_id})
    return [AuditRecord.from_item(i) for i in items if i["ts_seq"] != "~head"]


def row_summary(rows: list[AuditRecord]) -> list[tuple[str, str, tuple[str, ...], str | None]]:
    """(trigger, outcome, codes, message_ref) per row — what the tests compare."""
    return [(r.trigger, r.outcome, tuple(c.value for c in r.reason_codes), r.message_ref) for r in rows]
