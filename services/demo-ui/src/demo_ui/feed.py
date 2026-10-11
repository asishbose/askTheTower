"""The live feed (doc 11 §4): one poller per process, Server-Sent Events to every open page, no DynamoDB Streams.

Each `Source` renders its pane fragment; the poller pushes a fragment only when it differs from the last tick's,
and only while at least one page is open. A page that connects gets the current fragments at once. The macro
runner and `/say` publish `turn` / `status` / `qr` frames through the same fan-out.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from tower_audit import list_for_line
from tower_consent import Store, list_lines

from demo_ui import views
from demo_ui.clients import AlertsSent, BindingAdmin, MockAdmin, Unavailable, holder_of

log = logging.getLogger("demo_ui.feed")

OWNERS = (("user-asish", "Asish"), ("user-mom", "Mom"))
AUDIT_ROWS = 50
SMS_ROWS = 50
LINES_TTL_S = 30.0


@dataclass(frozen=True)
class Frame:
    event: str
    html: str

    def sse(self) -> str:
        data = "\n".join(f"data: {line}" for line in self.html.splitlines() or [""])
        return f"event: {self.event}\n{data}\n\n"


class Source(Protocol):
    event: str

    async def render(self) -> str: ...


def short(line_id: str) -> str:
    return line_id[:8]


def short_ref(ref: str | None) -> str:
    """`line:` + 8 hex: enough to tell lines apart, and too short to ever look like a number to the filter."""
    return (ref or "")[: len("line:") + 8]


class AuditSource:
    """Asish's log plus Mom's log, each read as its owner (07 §4): `list_lines` → `list_for_line`."""

    event = "audit"

    def __init__(self, store: Store, *, monotonic: Callable[[], float] = time.monotonic) -> None:
        self.store = store
        self.monotonic = monotonic
        self._lines: list[tuple[str, str, str]] = []  # (owner_id, label, line_id)
        self._lines_at = float("-inf")

    def _owned_lines(self) -> list[tuple[str, str, str]]:
        if self.monotonic() - self._lines_at > LINES_TTL_S:
            self._lines = [
                (owner, label, ln.line_id) for owner, label in OWNERS for ln in list_lines(self.store, owner)
            ]
            self._lines_at = self.monotonic()
        return self._lines

    def rows(self) -> list[dict[str, Any]]:
        out: list[tuple[Any, dict[str, Any]]] = []
        for owner, label, line_id in self._owned_lines():
            for rec in list_for_line(self.store, line_id, owner):
                out.append(
                    (
                        rec.ts,
                        {
                            "ts": rec.ts.strftime("%H:%M:%S"),
                            "log": label,
                            "line": short(line_id),
                            "actor": rec.actor_user_id,
                            "tool": rec.tool,
                            "trigger": rec.trigger,
                            "outcome": rec.outcome,
                            "codes": [c.value for c in rec.reason_codes],
                            "message_ref": rec.message_ref,
                        },
                    )
                )
        out.sort(key=lambda p: p[0], reverse=True)
        return [row for _, row in out[:AUDIT_ROWS]]

    async def render(self) -> str:
        try:
            rows = await asyncio.to_thread(self.rows)
        except Exception as e:  # noqa: BLE001 - any store failure pauses the feed with a banner (§9)
            log.warning("audit feed paused: %s", type(e).__name__)
            return views.render("unavailable.html", reason=f"feed paused: DynamoDB {type(e).__name__}")
        return views.render("audit.html", rows=rows)


class SmsSource:
    """The SMS Alerts sent (G3), newest first; asks only for entries after the last one seen."""

    event = "sms"

    def __init__(self, alerts: AlertsSent | None, off_reason: str = "") -> None:
        self.alerts = alerts
        self.off_reason = off_reason
        self.entries: list[dict[str, Any]] = []
        self.last_n = 0

    async def render(self) -> str:
        if self.alerts is None:
            return views.render("unavailable.html", reason=self.off_reason)
        try:
            new = await self.alerts.after(self.last_n)
        except Unavailable as e:
            return views.render("unavailable.html", reason=f"SMS list unavailable: {e}")
        for entry in new:
            self.last_n = max(self.last_n, int(entry.get("n", 0)))
            self.entries.insert(0, entry)
        del self.entries[SMS_ROWS:]
        return views.render("sms.html", entries=self.entries)


def carrier_view(state: dict[str, Any]) -> dict[str, Any]:
    lines = []
    for ref, line in sorted(state.get("lines", {}).items()):
        lines.append(
            {
                "holder": (holder_of(line) or "?").capitalize(),
                "ref": short_ref(ref),
                "sim_change_at": line.get("sim_change_at"),
                "call_forwarding": line.get("call_forwarding"),
                "reachable": line.get("reachable"),
                "connectivity": ", ".join(line.get("connectivity", [])),
            }
        )
    subs = [
        {
            "id": sid,
            "api": s.get("api"),
            "line": short_ref(s.get("line")),
            "status": s.get("status"),
            "events": s.get("eventsSent", 0),
        }
        for sid, s in sorted(state.get("subscriptions", {}).items())
    ]
    return {
        "clock": state.get("clock"),
        "faults": state.get("faults", []),
        "lines": lines,
        "subscriptions": subs,
    }


class CarrierSource:
    event = "carrier"

    def __init__(self, mock: MockAdmin | None, off_reason: str = "") -> None:
        self.mock = mock
        self.off_reason = off_reason

    async def render(self) -> str:
        if self.mock is None:
            return views.render("unavailable.html", reason=self.off_reason)
        try:
            state = await self.mock.state()
        except Unavailable as e:
            return views.render("unavailable.html", reason=str(e))
        return views.render("carrier.html", **carrier_view(state))


class GrantsSource:
    event = "grants"

    def __init__(self, binding: BindingAdmin | None, off_reason: str = "") -> None:
        self.binding = binding
        self.off_reason = off_reason

    async def render(self) -> str:
        if self.binding is None:
            return views.render("unavailable.html", reason=self.off_reason)
        try:
            rows = await self.binding.grants()
            resolved = await self.binding.resolve("user-asish", "mom")
        except Unavailable as e:
            return views.render("unavailable.html", reason=f"{e}: the QR code from next_step still works")
        grants = [
            {
                "line": short(str(g.get("line_id", ""))),
                "grantee": g.get("grantee_user_id"),
                "grant": g.get("grant"),
                "alias": g.get("alias"),
                "revoked_at": g.get("revoked_at"),
            }
            for g in rows
        ]
        return views.render("grants.html", grants=grants, resolve=resolved)


class Feed:
    def __init__(self, sources: Sequence[Source], poll_s: float) -> None:
        self.sources = list(sources)
        self.poll_s = poll_s
        self.last: dict[str, str] = {}
        self.subscribers: set[asyncio.Queue[Frame]] = set()
        self.ticks = 0
        self._poke = asyncio.Event()

    def subscribe(self) -> asyncio.Queue[Frame]:
        q: asyncio.Queue[Frame] = asyncio.Queue()
        for event, html in self.last.items():
            q.put_nowait(Frame(event, html))
        self.subscribers.add(q)
        self.poke()
        return q

    def unsubscribe(self, q: asyncio.Queue[Frame]) -> None:
        self.subscribers.discard(q)

    def publish(self, event: str, html: str) -> None:
        frame = Frame(event, html)
        for q in list(self.subscribers):
            q.put_nowait(frame)

    def poke(self) -> None:
        """Tick soon: after a control call the panes show its effect without waiting for the interval."""
        self._poke.set()

    async def tick(self) -> int:
        self.ticks += 1
        changed = 0
        for source in self.sources:
            html = await source.render()
            if self.last.get(source.event) != html:
                self.last[source.event] = html
                self.publish(source.event, html)
                changed += 1
        return changed

    async def run(self) -> None:
        while True:
            if self.subscribers:
                try:
                    await self.tick()
                except Exception:  # noqa: BLE001 - a failed tick must not stop the feed; next tick retries
                    log.exception("feed tick failed")
            self._poke.clear()
            try:
                await asyncio.wait_for(self._poke.wait(), timeout=self.poll_s)
            except TimeoutError:
                pass
