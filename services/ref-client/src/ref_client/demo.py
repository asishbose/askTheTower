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
"""

from __future__ import annotations

import asyncio
import inspect
import os
import time
from collections.abc import Awaitable, Callable, Mapping
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


# --- the script -------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Say:
    text: str
    expect: ToolCall
    codes: tuple[str, ...]  # what the story expects Tower to answer (the golden files say the same)


@dataclass(frozen=True)
class Advance:
    minutes: int
    why: str
    alerts: bool = (
        False  # Alerts acts on this jump: give it time to see it before the next step changes the line
    )


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
            Advance(20, "mock clock +20 min dark", alerts=True),
            Note(
                "Alerts' transplant rule: 20 min dark → the partner's text (SMS log: to=chain:user-partner). "
                "Asish's own copy is withheld: his line was SIM-swapped at +12 min."
            ),
            Say("Is my phone on?", _c("is_reachable", line="self"), ("UNREACHABLE",)),
            Advance(15, "mock clock +15 min, no acknowledgement", alerts=True),
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
    Body: `{owner_user_id, grantee_user_id, grant, alias, action}`. The endpoint is wired by prompt 12."""

    def __init__(self, base_url: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.url = base_url.rstrip("/") + "/_admin/grants"
        self._http = httpx.AsyncClient(transport=transport, timeout=10.0)

    async def set_mom_grant(self, action: Literal["revoke", "grant"]) -> None:
        body = {
            "owner_user_id": "user-mom",
            "grantee_user_id": "user-asish",
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


class DemoError(RuntimeError):
    pass


@dataclass
class Control:
    """Mock admin + grant and watch-settings control. `on_reset` / `on_advance` let an in-process run move Tower's clock with the
    mock's (in compose Tower reads the mock clock itself via `TOWER_CLOCK_URL`)."""

    mock: httpx.AsyncClient
    grants: GrantControl
    settings: SettingsControl
    on_reset: Callable[[], Awaitable[None]] | None = None
    on_advance: Callable[[float], Awaitable[None]] | None = None
    alerts_settle_s: float = 0.0
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    _lines: dict[str, str] = field(default_factory=dict)

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
    mock_url: str | None = None, binding_url: str | None = None, *, alerts_settle_s: float = 0.0
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
    )


# --- running it -------------------------------------------------------------------------------------------
@dataclass
class DemoRun:
    transcripts: list[Transcript] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)


StepKind = Literal["say", "advance", "fire", "grant", "settings", "note"]


@dataclass(frozen=True)
class StepReport:
    """One step as it ran (G4). `expected`/`actual`/`ok` are set for a `say` only; `ok` compares reason codes
    with the story's, nothing else. `replay` marks a story run only to reach this one's timeline."""

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
        for n, step in enumerate(story.steps, start=1):
            started = time.perf_counter()
            report = await _step(step, agent, control, t, run, echo)
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


async def _step(
    step: Step, agent: Agent, control: Control, t: Transcript, run: DemoRun, echo: Any
) -> dict[str, Any]:
    """Run one step; returns the `StepReport` fields that depend on the step."""
    if isinstance(step, Advance):
        fired = await control.advance(step.minutes, settle=step.alerts)
        text = step.why + (f" (fired: {', '.join(fired)})" if fired else "")
        t.control(text)
        echo(f"  [mock] {text}")
        return {"kind": "advance", "narration": text, "fired": tuple(fired)}
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
