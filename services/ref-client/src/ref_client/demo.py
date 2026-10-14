"""`ref-client demo`: the showcase order steps 4–7 (testing-and-showcase §4) from the terminal — the three moments
and the transplant story — with the mock admin calls and clock advances between utterances issued by this
script, not by hand.

The script talks to the mock carrier's `/_admin/*` (a simulation aid, 08 §3) and, for the revoke in moment 3
and the transplant story's watch settings (06 §11.4), to the binding page's local admin. It never talks to the store or the carrier APIs; the agent only calls Tower.
Lines are addressed by who holds them and fired by the mock's opaque `ref` (G2): the script reads
`/_admin/state?view=refs` (the line whose simulated client id is `phone-asish` / `phone-mom` in
`scenarios/demo.yaml`), so it never handles a number. `MOCK_ADMIN_TOKEN`, when set, goes on every mock admin call
(G1).

`run_demo(..., reset=True, on_step=None)` (G4, doc 11 §5): `on_step` gets one `StepReport` per step — what the demo
UI shows as narration with a pass/fail badge; `reset=False` skips the scenario reload (the caller has reset).

An `Advance` with `expect` waits until Alerts' sent list (`GET /internal/sent?after=<n>`, 06 §3.1) shows that text,
read from the cursor at the start of the story (09 §4). Without a reachable sent list the wait is skipped and the
report says so.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx

from ref_client.agent import Agent
from ref_client.transcript import ToolCall, Transcript, Turn, write

Who = Literal["asish", "mom"]
DEFAULT_MOCK_URL = "http://localhost:8443"  # mk/vars.mk MOCK_URL
DEFAULT_BINDING_URL = "http://localhost:8081"  # mk/vars.mk BINDING_URL
# After a clock jump Alerts acts on (06 §11.4), wait this long before the next step on ENV=local. Alerts' local
# scheduler wakes every ALERTS_TICK_S / ALERTS_CLOCK_SCALE (compose: 1 s) and runs a poll or tick on the first wake
# after a jump; two wakes plus the poll's carrier calls fit in 2.5 s. Elsewhere Alerts runs on its own schedule
# (EventBridge, CronJobs), which no wait here can match.
ALERTS_SETTLE_S_LOCAL = 2.5
DEFAULT_ALERTS_URL = "http://localhost:8082"  # mk/vars.mk ALERTS_URL
# The sent-list wait after an `Advance` with `expect` (09 §4, human decision 2026-10-11): poll every 0.2 s, give up
# after 10 s. Locally Alerts acts on a jump at its next scheduler wake (≤ 1 s) plus the poll's carrier calls.
SENT_POLL_S = 0.2
SENT_WAIT_S = 10.0


# --- the script -------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Say:
    text: str
    expect: ToolCall
    codes: tuple[str, ...]  # what the story expects Tower to answer (the golden files say the same)


@dataclass(frozen=True)
class ExpectSent:
    """A text Alerts must have sent after the step: its sent-list `role` and recipient `user_id` (06 §3.1)."""

    role: str
    user_id: str

    def label(self) -> str:
        return f"{self.role} text to {self.user_id}"


@dataclass(frozen=True)
class Advance:
    minutes: int
    why: str
    alerts: bool = (
        False  # Alerts acts on this jump: give it time to see it before the next step changes the line
    )
    expect: ExpectSent | None = None  # don't go on until Alerts' sent list shows this text (09 §4)


@dataclass(frozen=True)
class Fire:
    who: Who
    event: str
    why: str


@dataclass(frozen=True)
class Grant:
    action: Literal["revoke", "grant"]
    why: str


@dataclass(frozen=True)
class Settings:
    """The line-holder saves their watch settings on the binding page (04 §9) — here via its local admin."""

    why: str


@dataclass(frozen=True)
class Note:
    text: str


Step = Say | Advance | Fire | Grant | Settings | Note


@dataclass(frozen=True)
class Story:
    name: str
    title: str
    steps: tuple[Step, ...]
    user: str = "user-asish"


def _c(name: str, **args: Any) -> ToolCall:
    return ToolCall(name, args)


STORIES: tuple[Story, ...] = (
    Story(
        "moment-1",
        "Moment 1 — my phone just lost signal",
        (
            Say("Is my line OK?", _c("line_is_ok", line="self"), ("OK",)),
            Advance(12, "mock clock +12 min; the demo timeline moves Asish's SIM to another device"),
            Say(
                "My phone just lost signal. Is my line OK?",
                _c("line_is_ok", line="self"),
                ("SIM_SWAPPED_RECENT",),
            ),
        ),
    ),
    Story(
        "moment-2",
        "Moment 2 — is anything forwarding my calls?",
        (
            Advance(8, "mock clock +8 min; the demo timeline sets call forwarding on Asish's line (cf_set)"),
            Say(
                "Is anything forwarding my calls?",
                _c("line_is_ok", line="self"),
                ("SIM_SWAPPED_RECENT", "CALL_FORWARDING_SET"),
            ),
        ),
    ),
    Story(
        "moment-3",
        "Moment 3 — Mom's line, a watch, a revoke",
        (
            Say("Is Mom's line OK?", _c("line_is_ok", line="mom"), ("OK",)),
            Say("Watch Mom's line for me.", _c("watch_line", line="mom", enable=True), ("OK",)),
            Fire("mom", "sim_swap", "mock: SIM swap on Mom's line"),
            Note("Alerts texts the watcher now — the phone buzzes (SMS log locally; make showcase-alerts)."),
            Grant("revoke", "Mom revokes Asish's watch grant (binding page)"),
            Fire("mom", "sim_swap", "mock: SIM swap on Mom's line again"),
            Note("No text this time: Alerts re-reads the grant and audits SUPPRESSED_REVOKED."),
            Say("Is Mom's line OK?", _c("line_is_ok", line="mom"), ("NO_CONSENT",)),
            Grant("grant", "demo reset: Mom grants the watch again"),
        ),
    ),
    Story(
        "transplant",
        "Transplant closing story — the line must stay reachable",
        (
            Settings(
                "binding page: Asish saves watch settings — must stay reachable; text the partner, "
                "then the neighbour"
            ),
            Say("Turn on alerts for my line.", _c("watch_line", line="self", enable=True), ("OK",)),
            Fire("asish", "unreachable", "mock: Asish's phone drops off the network"),
            Advance(
                20,
                "mock clock +20 min dark",
                alerts=True,
                expect=ExpectSent("escalation[0]", "user-partner"),
            ),
            Note(
                "Alerts' transplant rule: 20 min dark → the partner's text (SMS log: to=chain:user-partner). "
                "Asish's own copy is withheld: his line was SIM-swapped at +12 min."
            ),
            Say("Is my phone on?", _c("is_reachable", line="self"), ("UNREACHABLE",)),
            Advance(
                15,
                "mock clock +15 min, no acknowledgement",
                alerts=True,
                expect=ExpectSent("escalation[1]", "user-neighbour"),
            ),
            Note("15 more minutes with no reply → the neighbour's text (SMS log: to=chain:user-neighbour)."),
            Fire("asish", "reachable", "mock: Asish's phone is back on the network"),
            Say("Is my phone reachable now?", _c("is_reachable", line="self"), ("OK",)),
        ),
    ),
)
STORY_NAMES = tuple(s.name for s in STORIES)


# --- the controls between utterances ---------------------------------------------------------------------
class GrantControl(Protocol):
    async def set_mom_grant(self, action: Literal["revoke", "grant"]) -> None: ...


class SettingsControl(Protocol):
    async def save_transplant(self) -> None: ...

    async def reset(self) -> None: ...


class SentListUnavailable(RuntimeError):
    """The sent list cannot be read here (off, no bearer, not reachable). The message never carries a value."""


class SentList(Protocol):
    """Alerts' sent list, `GET /internal/sent?after=<n>` (06 §3.1): entries with `n` above the cursor, ascending.
    Raises `SentListUnavailable` when it cannot be read. Entries carry no number; only `n`, `role` and `user_id`
    are read here."""

    async def after(self, n: int) -> Sequence[Mapping[str, object]]: ...


TRANSPLANT_SETTINGS: dict[str, Any] = {
    "owner_user_id": "user-asish",
    "profile": "transplant",
    "contacts": ["user-partner", "user-neighbour"],
}


class BindingAdminSettings:
    """`POST {BINDING_URL}/_admin/watch-settings` (local admin, 04 §9.2): the same save the page's form makes.
    `reset` deletes Asish's own Watch so a re-run starts clean."""

    def __init__(self, base_url: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.url = base_url.rstrip("/") + "/_admin/watch-settings"
        self._http = httpx.AsyncClient(transport=transport, timeout=10.0)

    async def _post(self, body: dict[str, Any]) -> None:
        r = await self._http.post(self.url, json=body)
        if r.status_code == 404:
            raise DemoError(
                f"{self.url} answered 404: the binding page's local admin is needed for the transplant story "
                "(TOWER_ENV=local, BIND_ADMIN=1)."
            )
        r.raise_for_status()

    async def save_transplant(self) -> None:
        await self._post(TRANSPLANT_SETTINGS)

    async def reset(self) -> None:
        await self._post({"owner_user_id": "user-asish", "profile": "self", "contacts": [], "reset": True})

    async def aclose(self) -> None:
        await self._http.aclose()


class BindingAdminGrants:
    """Compose/AWS: `POST {BINDING_URL}/_admin/grants` on the binding page (local admin, `BIND_ADMIN=1`).
    Body: `{owner_user_id, grantee_user_id, grant, alias, action}`. The endpoint is wired by prompt 12.
    On ENV=aws: `BINDING_ADMIN_URL` with `DEMO_ADMIN_BEARER` (bearer mode, 04 §8) and the Cognito subs."""

    def __init__(
        self,
        base_url: str,
        *,
        bearer: str | None = None,
        owner: str = "user-mom",
        grantee: str = "user-asish",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = base_url.rstrip("/") + "/_admin/grants"
        self.owner, self.grantee = owner, grantee
        headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
        self._http = httpx.AsyncClient(transport=transport, timeout=10.0, headers=headers)

    async def set_mom_grant(self, action: Literal["revoke", "grant"]) -> None:
        body = {
            "owner_user_id": self.owner,
            "grantee_user_id": self.grantee,
            "grant": "watch",
            "alias": "mom",
            "action": action,
        }
        r = await self._http.post(self.url, json=body)
        if r.status_code == 404:
            raise DemoError(
                f"{self.url} answered 404: the binding page's local grant admin is needed for moment 3 "
                "(TOWER_ENV=local, BIND_ADMIN=1)."
            )
        r.raise_for_status()

    async def aclose(self) -> None:
        await self._http.aclose()


class AlertsReset:
    """ENV=aws's `SettingsControl`: the binding page's watch-settings admin is 404 in bearer mode (the Transplant
    story stays local, 04 §8), so a re-run starts clean the way the demo UI's AWS Reset does it: Alerts drops the
    Watches and its bookkeeping for Asish's and Mom's lines (`POST {ALERTS_URL}/internal/reset`, 06 §3.2)."""

    def __init__(
        self,
        base_url: str,
        bearer: str,
        user_ids: Sequence[str],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.user_ids = list(user_ids)
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {bearer}"},
            transport=transport,
            timeout=10.0,
        )

    async def reset(self) -> None:
        r = await self._http.post("/internal/reset", json={"user_ids": self.user_ids})
        r.raise_for_status()

    async def save_transplant(self) -> None:
        raise DemoError("the transplant story stays local (04 §8): run it with ENV=local")

    async def aclose(self) -> None:
        await self._http.aclose()


class AlertsSentList:
    """`GET {ALERTS_URL}/internal/sent?after=<n>` with Alerts' admin bearer (06 §3.1: `INTERNAL_BEARER` locally,
    `DEMO_ADMIN_BEARER` on ENV=aws, decision 1). Any failure is `SentListUnavailable`: the wait is skipped."""

    def __init__(
        self, base_url: str, bearer: str, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {bearer}"},
            transport=transport,
            timeout=5.0,
        )

    async def after(self, n: int) -> list[dict[str, object]]:
        try:
            r = await self._http.get("/internal/sent", params={"after": n})
        except httpx.HTTPError as e:
            raise SentListUnavailable(f"Alerts' sent list not reachable ({type(e).__name__})") from None
        if r.status_code == 404:
            raise SentListUnavailable("Alerts' sent list is off here (404)")
        if r.status_code >= 300:
            raise SentListUnavailable(f"Alerts' sent list answered {r.status_code}")
        body: dict[str, Any] = r.json()
        return [dict(e) for e in body.get("sent", [])]

    async def aclose(self) -> None:
        await self._http.aclose()


def alerts_sent_list(env: str, environ: Mapping[str, str]) -> AlertsSentList | None:
    """`make demo`'s sent list: `ALERTS_URL` with `DEMO_ADMIN_BEARER` on ENV=aws, else `ALERTS_INTERNAL_BEARER`
    (mk/vars.mk exports it locally from deploy/compose/.env). None without a bearer: the waits are skipped."""
    bearer = environ.get("DEMO_ADMIN_BEARER" if env == "aws" else "ALERTS_INTERNAL_BEARER", "")
    if not bearer:
        return None
    return AlertsSentList(environ.get("ALERTS_URL") or DEFAULT_ALERTS_URL, bearer)


@dataclass(frozen=True)
class SentCheck:
    """One sent-list wait: `seen` (the text is there), `missing` (not within the ceiling) or `skipped` (no list)."""

    status: Literal["seen", "missing", "skipped"]
    text: str


@dataclass
class SentWatch:
    """Alerts' sent list for one story, read only by `after=<cursor>` from the value at the story's start, so a
    text from an earlier story never satisfies a wait. Each entry satisfies one wait."""

    sent: SentList | None
    sleep: Callable[[float], Awaitable[None]]
    monotonic: Callable[[], float]
    poll_s: float = SENT_POLL_S
    ceiling_s: float = SENT_WAIT_S
    cursor: int = 0
    off: str = ""
    _used: set[int] = field(default_factory=set)

    async def start(self) -> None:
        """The cursor: the highest `n` now in the list. Sets `off` when there is no list to read."""
        if self.sent is None:
            self.off = "no sent list here (ALERTS_URL and an admin bearer are needed)"
            return
        try:
            while (batch := _entries(await self.sent.after(self.cursor))) and max(
                e.n for e in batch
            ) > self.cursor:
                self.cursor = max(e.n for e in batch)  # the list pages at 200 entries
        except SentListUnavailable as e:
            self.off = str(e)

    async def wait_for(self, want: ExpectSent) -> SentCheck:
        """Poll every `poll_s` until a new entry matches `want`; `missing` after `ceiling_s`, never a hang."""
        if self.off or self.sent is None:
            return SentCheck("skipped", f"wait for the {want.label()} skipped: {self.off}")
        started = self.monotonic()
        while True:
            try:
                batch = _entries(await self.sent.after(self.cursor))
            except SentListUnavailable as e:
                return SentCheck("skipped", f"wait for the {want.label()} skipped: {e}")
            hit = next((e for e in batch if e.matches(want) and e.n not in self._used), None)
            waited = self.monotonic() - started
            if hit is not None:
                self._used.add(hit.n)
                return SentCheck("seen", f"{want.label()} in Alerts' sent list after {waited:.1f} s")
            if waited >= self.ceiling_s:
                return SentCheck(
                    "missing", f"no {want.label()} in Alerts' sent list within {self.ceiling_s:g} s"
                )
            await self.sleep(self.poll_s)


@dataclass(frozen=True)
class _Entry:
    n: int
    role: str
    user_id: str

    def matches(self, want: ExpectSent) -> bool:
        return self.role == want.role and self.user_id == want.user_id


def _entries(raw: Sequence[Mapping[str, object]]) -> list[_Entry]:
    """Only `n`, `role` and `user_id` of each entry; the body is never kept."""
    return [_Entry(int(str(e["n"])), str(e.get("role", "")), str(e.get("user_id", ""))) for e in raw]


class DemoError(RuntimeError):
    pass


@dataclass
class Control:
    """Mock admin + grant and watch-settings control. `on_reset` / `on_advance` let an in-process run move Tower's clock with the
    mock's (in compose Tower reads the mock clock itself via `TOWER_CLOCK_URL`). `sent` is Alerts' sent list for
    the `expect` waits (None: skipped); `sleep` and `monotonic` are injectable so tests never sleep."""

    mock: httpx.AsyncClient
    grants: GrantControl
    settings: SettingsControl
    on_reset: Callable[[], Awaitable[None]] | None = None
    on_advance: Callable[[float], Awaitable[None]] | None = None
    alerts_settle_s: float = 0.0
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    sent: SentList | None = None
    monotonic: Callable[[], float] = time.monotonic
    _lines: dict[str, str] = field(default_factory=dict)

    async def watch_sent(self) -> SentWatch:
        """A `SentWatch` with its cursor at the sent list's current end (09 §4)."""
        watch = SentWatch(self.sent, self.sleep, self.monotonic)
        await watch.start()
        return watch

    async def reset(self) -> None:
        r = await self.mock.post("/_admin/scenarios/load", json={"name": "demo"})
        r.raise_for_status()
        await self.find_lines()
        await self.settings.reset()
        if self.on_reset:
            await self.on_reset()

    async def find_lines(self) -> None:
        """Each holder's line `ref` from `/_admin/state?view=refs` (no number in it, G2)."""
        r = await self.mock.get("/_admin/state", params={"view": "refs"})
        r.raise_for_status()
        lines: dict[str, Any] = r.json()["lines"]
        for who in ("asish", "mom"):  # demo.yaml: the simulated client id on each line names its holder
            found = [
                ref for ref, line in lines.items() if f"phone-{who}" in line.get("mobile_data_client_ids", [])
            ]
            if len(found) != 1:
                raise DemoError(f"the mock's demo scenario has no single line for phone-{who}")
            self._lines[who] = found[0]

    async def advance(self, minutes: int, *, settle: bool = False) -> list[str]:
        """Move the mock clock. `settle`: Alerts acts on this jump, so wait `alerts_settle_s` for its scheduler."""
        r = await self.mock.post("/_admin/clock", json={"advance_s": minutes * 60})
        r.raise_for_status()
        if self.on_advance:
            await self.on_advance(minutes * 60)
        if settle and self.alerts_settle_s > 0:
            await self.sleep(self.alerts_settle_s)
        return [str(f.get("event", "")) for f in r.json().get("fired", [])]

    async def fire(self, who: Who, event: str) -> None:
        r = await self.mock.post(f"/_admin/lines/{self._lines[who]}/events", json={"event": event})
        r.raise_for_status()


def mock_admin_headers(token: str | None = None) -> dict[str, str]:
    """`Authorization: Bearer $MOCK_ADMIN_TOKEN` when the token is set (G1); the mock's reads need none."""
    token = token if token is not None else os.environ.get("MOCK_ADMIN_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


def alerts_settle_s(env: str, environ: Mapping[str, str]) -> float:
    """`DEMO_ALERTS_SETTLE_S`, else `ALERTS_SETTLE_S_LOCAL` on ENV=local and 0 elsewhere."""
    raw = environ.get("DEMO_ALERTS_SETTLE_S")
    value = float(raw) if raw else (ALERTS_SETTLE_S_LOCAL if env == "local" else 0.0)
    if value < 0:
        raise ValueError("DEMO_ALERTS_SETTLE_S must be >= 0")
    return value


def http_control(
    mock_url: str | None = None,
    binding_url: str | None = None,
    *,
    alerts_settle_s: float = 0.0,
    sent: SentList | None = None,
) -> Control:
    mock = httpx.AsyncClient(
        base_url=mock_url or os.environ.get("MOCK_URL") or DEFAULT_MOCK_URL,
        timeout=10.0,
        headers=mock_admin_headers(),
    )
    binding = binding_url or os.environ.get("BINDING_URL") or DEFAULT_BINDING_URL
    return Control(
        mock=mock,
        grants=BindingAdminGrants(binding),
        settings=BindingAdminSettings(binding),
        alerts_settle_s=alerts_settle_s,
        sent=sent,
    )


AWS_ADMIN_KEYS = ("MOCK_URL", "BINDING_ADMIN_URL", "ALERTS_URL", "DEMO_ADMIN_BEARER")
AWS_STORIES = tuple(n for n in STORY_NAMES if n != "transplant")  # the transplant story stays local (04 §8)


def aws_control(
    environ: Mapping[str, str],
    *,
    asish: str,
    mom: str,
    alerts_settle_s: float = 0.0,
    sent: SentList | None = None,
) -> Control:
    """ENV=aws (decision 1, 2026-10-11): every admin call goes through the demo admin path with the one bearer
    `DEMO_ADMIN_BEARER` — the mock's `MOCK_URL` (`…/carrier-admin`), the binding page's `BINDING_ADMIN_URL` and
    Alerts' `ALERTS_URL` — and names the users by their Cognito subs. `MOCK_ADMIN_TOKEN` (the compose token) is
    not used here. Raises `DemoError` when the path is closed."""
    missing = [k for k in AWS_ADMIN_KEYS if not environ.get(k)]
    if missing:
        raise DemoError(
            f"ENV=aws needs the demo admin path open: {', '.join(missing)} empty in deploy/.env.aws — "
            "run make admin-open (Ops: Open the admin path), then make outputs ENV=aws"
        )
    bearer = environ["DEMO_ADMIN_BEARER"]
    mock = httpx.AsyncClient(base_url=environ["MOCK_URL"], timeout=15.0, headers=mock_admin_headers(bearer))
    return Control(
        mock=mock,
        grants=BindingAdminGrants(environ["BINDING_ADMIN_URL"], bearer=bearer, owner=mom, grantee=asish),
        settings=AlertsReset(environ["ALERTS_URL"], bearer, (asish, mom)),
        alerts_settle_s=alerts_settle_s,
        sent=sent,
    )


# --- running it -------------------------------------------------------------------------------------------
@dataclass
class DemoRun:
    transcripts: list[Transcript] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)


StepKind = Literal["say", "advance", "fire", "grant", "settings", "note"]


@dataclass(frozen=True)
class StepReport:
    """One step as it ran (G4). `expected`/`actual` are set for a `say` only, and `ok` compares its reason codes
    with the story's. An `advance` with `expect` sets `ok` from the sent-list wait (False: the text never came;
    None: skipped) and `sent` says which. `replay` marks a story run only to reach this one's timeline."""

    story: str
    step_id: str  # "<story>#<n>", n from 1
    kind: StepKind
    narration: str
    elapsed_ms: int
    expected: tuple[str, ...] = ()
    actual: tuple[str, ...] = ()
    ok: bool | None = None
    replay: bool = False
    fired: tuple[str, ...] = ()
    turn: Turn | None = None
    sent: str = ""  # the sent-list wait's outcome, for an `advance` with `expect`


OnStep = Callable[[StepReport], Awaitable[None] | None]


async def run_demo(
    agent_for: Callable[[str], Awaitable[Agent]],
    control: Control,
    *,
    env: str = "local",
    stories: tuple[str, ...] = STORY_NAMES,
    out_dir: Path | None = None,
    echo: Callable[[str], None] = print,
    pause: Callable[[Story], None] | None = None,
    reset: bool = True,
    on_step: OnStep | None = None,
    replay: tuple[str, ...] = (),
) -> DemoRun:
    """Run the stories in order on one mock timeline. `agent_for(user_id)` gives an agent connected to Tower
    as that user. Prints each spoken line; writes `<out_dir>/<story>.json` when `out_dir` is set.

    `reset=False`: the caller has already reset the stack (the demo UI's Reset is `make seed`); only the line
    refs are looked up. `on_step` is called after every step with its `StepReport`; stories named in `replay`
    are marked so (the UI collapses them)."""
    run = DemoRun()
    if reset:
        await control.reset()
    else:
        await control.find_lines()
    selected = [s for s in STORIES if s.name in stories]
    if any(isinstance(step, Grant) for s in selected for step in s.steps):
        await control.grants.set_mom_grant("grant")  # a re-run starts from the seeded grant
    for story in STORIES:
        if story.name not in stories:
            continue
        if pause is not None:
            pause(story)  # `make showcase`: "press enter for step 4: moment 1"
        agent = await agent_for(story.user)
        t = Transcript(story.name, story.title, story.user, agent.name, agent.model_id, env)
        echo(f"\n=== {story.title} ===")
        watch = await control.watch_sent() if _expects(story) else None
        for n, step in enumerate(story.steps, start=1):
            started = time.perf_counter()
            report = await _step(step, agent, control, t, run, echo, watch)
            if on_step is not None:
                elapsed = int((time.perf_counter() - started) * 1000)
                done = on_step(
                    StepReport(
                        story=story.name,
                        step_id=f"{story.name}#{n}",
                        elapsed_ms=elapsed,
                        replay=story.name in replay,
                        **report,
                    )
                )
                if inspect.isawaitable(done):
                    await done
        run.transcripts.append(t)
        if out_dir is not None:
            echo(f"    transcript → {write(t, out_dir)}")
    return run


def _expects(story: Story) -> bool:
    return any(isinstance(s, Advance) and s.expect is not None for s in story.steps)


async def _advance(
    step: Advance, control: Control, t: Transcript, run: DemoRun, echo: Any, watch: SentWatch | None
) -> dict[str, Any]:
    """The clock jump, then (with `expect`) the sent-list wait. The wait is not a transcript step."""
    fired = await control.advance(step.minutes, settle=step.alerts)
    text = step.why + (f" (fired: {', '.join(fired)})" if fired else "")
    t.control(text)
    echo(f"  [mock] {text}")
    report: dict[str, Any] = {"kind": "advance", "narration": text, "fired": tuple(fired)}
    if step.expect is None or watch is None:
        return report
    check = await watch.wait_for(step.expect)
    report["sent"] = check.text
    if check.status == "seen":
        echo(f"  [alerts] {check.text}")
        report["ok"] = True
    elif check.status == "skipped":
        echo(f"  [alerts] {check.text}")
    else:
        echo(f"  [alerts] MISSING: {check.text}")
        run.mismatches.append(f"{t.story}: {step.why!r} → {check.text}")
        report["ok"] = False
        report["narration"] = f"{text} — MISSING: {check.text}"
    return report


async def _step(
    step: Step,
    agent: Agent,
    control: Control,
    t: Transcript,
    run: DemoRun,
    echo: Any,
    watch: SentWatch | None = None,
) -> dict[str, Any]:
    """Run one step; returns the `StepReport` fields that depend on the step."""
    if isinstance(step, Advance):
        return await _advance(step, control, t, run, echo, watch)
    if isinstance(step, Fire):
        await control.fire(step.who, step.event)
        t.control(step.why)
        echo(f"  [mock] {step.why}")
        return {"kind": "fire", "narration": step.why}
    if isinstance(step, Grant):
        await control.grants.set_mom_grant(step.action)
        t.control(step.why)
        echo(f"  [consent] {step.why}")
        return {"kind": "grant", "narration": step.why}
    if isinstance(step, Settings):
        await control.settings.save_transplant()
        t.control(step.why)
        echo(f"  [consent] {step.why}")
        return {"kind": "settings", "narration": step.why}
    if isinstance(step, Note):
        t.note(step.text)
        echo(f"  ({step.text})")
        return {"kind": "note", "narration": step.text}
    turn: Turn = await agent.ask(step.text, expect=step.expect)
    t.turn(turn)
    calls = ", ".join(f"{c.name}({_args(c.args)})" for c in turn.tool_calls) or "no tool"
    echo(f"  you> {step.text}")
    echo(f"       → {calls}  {turn.reason_codes}")
    echo(f"  ref> {turn.spoken}")
    actual = tuple(turn.reason_codes)
    if actual != step.codes:
        run.mismatches.append(f"{t.story}: {step.text!r} → {turn.reason_codes}, expected {list(step.codes)}")
    return {
        "kind": "say",
        "narration": step.text,
        "expected": step.codes,
        "actual": actual,
        "ok": actual == step.codes,
        "turn": turn,
    }


def _args(args: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in args.items())
