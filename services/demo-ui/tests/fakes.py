"""Fakes for the demo-ui tests: the mock's `/_admin`, the binding page's admin and Alerts' sent list as one
`httpx.MockTransport` each, recording every request; and a fake agent that answers from the stories."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
from ref_client.demo import STORIES
from ref_client.transcript import ToolCall, Turn
from tower_policy.codes import ReasonCode
from tower_policy.phrasing import TEMPLATES

ASISH_REF = "line:1111aaaa2222bbbb"
MOM_REF = "line:3333cccc4444dddd"
STATE = {
    "scenario": "demo",
    "clock": "2026-10-05T14:00:00Z",
    "faults": [],
    "lines": {
        ASISH_REF: {"ref": ASISH_REF, "sim_change_at": "2026-09-01T10:00:00Z", "call_forwarding": "none",
                    "reachable": True, "connectivity": ["DATA"], "mobile_data_client_ids": ["phone-asish"]},
        MOM_REF: {"ref": MOM_REF, "sim_change_at": "2026-08-12T09:30:00Z", "call_forwarding": "none",
                  "reachable": True, "connectivity": ["DATA"], "mobile_data_client_ids": ["phone-mom"]},
    },
    "subscriptions": {"sub-0001": {"api": "sim-swap-subscriptions", "line": MOM_REF, "status": "ACTIVE",
                                   "eventsSent": 1}},
}  # fmt: skip


@dataclass
class Recorder:
    requests: list[httpx.Request] = field(default_factory=list)
    status: int = 200
    sent: list[dict[str, Any]] = field(default_factory=list)
    grants: list[dict[str, Any]] = field(default_factory=list)

    def bodies(self) -> list[str]:
        return [f"{r.method} {r.url} {r.content.decode()}" for r in self.requests]

    def mock(self) -> httpx.MockTransport:
        def handle(r: httpx.Request) -> httpx.Response:
            self.requests.append(r)
            if self.status != 200:
                return httpx.Response(self.status)
            if r.url.path == "/_admin/state":
                return httpx.Response(200, json=STATE)
            if r.url.path == "/_admin/clock":
                return httpx.Response(200, json={"clock": "x", "fired": [{"event": "sim_swap"}]})
            return httpx.Response(200, json={"ok": True})

        return httpx.MockTransport(handle)

    def binding(self) -> httpx.MockTransport:
        def handle(r: httpx.Request) -> httpx.Response:
            self.requests.append(r)
            if self.status != 200:
                return httpx.Response(self.status)
            if r.url.path == "/_admin/bind-tokens":
                return httpx.Response(200, json={"url": "http://localhost:8081/bind/tok"})
            if r.url.path == "/_admin/tables":
                return httpx.Response(200, json={"Grants": self.grants})
            if r.url.path == "/_admin/resolve":
                return httpx.Response(200, json={"bound": True, "grant": "watch", "line_id": "x"})
            return httpx.Response(200, json={"changed": True})

        return httpx.MockTransport(handle)

    def alerts(self) -> httpx.MockTransport:
        def handle(r: httpx.Request) -> httpx.Response:
            self.requests.append(r)
            if self.status != 200:
                return httpx.Response(self.status)
            after = int(r.url.params.get("after", "0"))
            return httpx.Response(200, json={"sent": [e for e in self.sent if e["n"] > after]})

        return httpx.MockTransport(handle)


def expected_codes(utterance: str, occurrence: int = 0) -> tuple[str, ...]:
    """The codes the stories expect for the `occurrence`-th time `utterance` is said (moment 3 asks "Is Mom's line
    OK?" twice: `OK`, then `NO_CONSENT` after the revoke)."""
    said = [s.codes for st in STORIES for s in st.steps if getattr(s, "text", None) == utterance]
    return said[min(occurrence, len(said) - 1)]


def summary_for(codes: list[str]) -> str:
    """What Tower would say for `codes`: its own voice templates (03 §4), with a fixed time and alias, so a fake
    reply never reads "as it was" under a `SIM_SWAPPED_RECENT` chip (the scripted agent reads `summary` verbatim)."""
    parts = [TEMPLATES[ReasonCode(c)]["voice"].format(time="2:13 today", Name="Mom") for c in codes]
    return " ".join(p for p in parts if p)


class StoryAgent:
    """Answers each utterance with the codes the story expects (or `wrong` for one utterance), and the summary
    Tower phrases for those codes."""

    name = "scripted"
    model_id: str | None = None

    def __init__(self, wrong: str | None = None, next_step: dict[str, Any] | None = None) -> None:
        self.wrong = wrong
        self.next_step = next_step
        self.asked: dict[str, int] = {}

    async def ask(self, utterance: str, *, expect: ToolCall | None = None) -> Turn:
        n = self.asked[utterance] = self.asked.get(utterance, -1) + 1
        codes = list(expected_codes(utterance, n)) if utterance != self.wrong else ["CARRIER_ERROR"]
        result: dict[str, Any] = {"summary": summary_for(codes), "reason_codes": codes}
        if self.next_step:
            result["next_step"] = self.next_step
        return Turn(utterance, [expect] if expect else [], result, result["summary"], [result])


def dump(requests: list[httpx.Request]) -> str:
    return json.dumps([f"{r.method} {r.url} {r.content.decode()}" for r in requests])
