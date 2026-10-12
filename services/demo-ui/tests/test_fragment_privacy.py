"""Doc 11 §8.3, §11 "Privacy" over the prompt-21 markup: every one of the eighteen fragments (twelve, plus the Ops
tab's six from prompts 22 and 23), rendered through `views.render` with a demo number in every free string it
shows, carries no number, in text or in the `data-*` and `title` attributes `stage.js` reads. The Ops header gets a
planted (bare) account id; its fragments get planted keys only where the runner's masking would have run first.

The wire sweeps (`test_real_backends`, `test_macros_make_demo`, `test_served`) reach ten fragments; `said.html`
(free text needs Bedrock) and `unavailable.html` (a failing neighbour) are reached only here. The last test fails
when a template is added without a case below.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from demo_ui import views
from demo_ui.ops.aws_status import AdminBox, Banner, Pipeline, Step, Teardown
from demo_ui.ops.plan import PlanSummary
from ref_client.demo import StepReport
from ref_client.transcript import ToolCall, Turn

from tests.privacy.patterns import HEALTH_WORDS, phone_hits

pytestmark = pytest.mark.unit

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "demo_ui" / "templates"
N = "+16135550101"  # the 555-01xx fiction of scenarios/demo.yaml
G = "(613) 555-0102"
SAY = f"call me on {N} or {G}"


def _turn() -> Turn:
    result = {"summary": SAY, "reason_codes": ["OK"], "next_step": {"kind": f"bind {N}"}}
    return Turn(SAY, [ToolCall("line_is_ok", {"line": N})], result, SAY, [result])


def _report() -> StepReport:
    return StepReport("moment-1", f"moment-1#{N}", "say", SAY, 12, ("OK",), ("OK",), True, False, (), _turn())


CASES: dict[str, dict[str, Any]] = {
    "pill.html": {"mode": "scripted", "env": N},
    "status.html": {"text": SAY, "level": "info"},
    "unavailable.html": {"reason": f"mock: answered 503 for {N} ({G})"},
    "conversation_controls.html": {
        "macros": [("moment-1", f"Moment {N}")],
        "captions": {"moment-1": SAY},
        "carrier": False,
        "off_reason": SAY,
        "personas": ["asish", "mom"],
        "free_text": False,
    },
    "carrier_controls.html": {"enabled": False, "reason": SAY, "events": [], "faults": []},
    "carrier.html": {
        "clock": "2026-10-05T14:00:00Z",
        "faults": [N],
        "lines": [
            {
                "holder": "Asish",
                "ref": N,
                "sim_change_at": G,
                "call_forwarding": N,
                "reachable": True,
                "connectivity": G,
            }
        ],
        "subscriptions": [
            {"id": N, "api": "sim-swap-subscriptions", "line": G, "status": "ACTIVE", "events": 1}
        ],
    },
    "sms.html": {
        "entries": [
            {
                "n": 1,
                "at": "14:13",
                "role": f"watcher {N}",
                "user_id": G,
                "template": "SIM_SWAPPED_RECENT.sms",
                "body": SAY,
            }
        ]
    },
    "audit.html": {
        "rows": [
            {
                "ts": "14:13:00",
                "log": "Mom",
                "line": N,
                "actor": G,
                "tool": "alert",
                "trigger": "event",
                "outcome": "ok",
                "codes": ["OK"],
                "message_ref": N,
            }
        ]
    },
    "grants.html": {
        "grants": [{"line": N, "alias": G, "grant": "watch", "grantee": N, "revoked_at": G}],
        "resolve": {"bound": True, "grant": "watch"},
    },
}


JOB = {"name": f"up {N}", "label": SAY, "state": "fail", "code": 2, "duration": f"{G} s", "note": SAY,
       "plan": {"add": 1, "change": 2, "destroy": 3}}  # fmt: skip
ACCOUNT = "123456789012"  # a bare 12-digit run is a phone-number hit; the header shows it dashed (R7)
STEP = Step(
    4,
    "aws-deploy",
    f"step {N}",
    "fail",
    SAY,
    G,
    datetime(2026, 10, 11, 9, 0, tzinfo=UTC),
    PlanSummary(1, 0, 2),
)


def _row(name: str) -> dict[str, Any]:
    return {"name": name, "label": SAY, "command": f"make push ENV=aws {G}", "why": SAY, "word": G}


OPS_CASES: dict[str, dict[str, Any]] = {  # prompts 22 and 23: the Ops tab's six fragments
    "ops_log.html": {"lines": [SAY, f"Bearer {N}", G]},
    "ops_status.html": {"job": JOB},
    "ops.html": {
        "off": None,
        "groups": [("stack", [{"name": f"up{N}", "label": SAY, "why": G, "cta": True}])],
        "stack_down": [N, G],
        "busy": True,
        "job": JOB,
        "lines": [SAY],
    },
    "ops_checklist.html": {
        "p": Pipeline(
            tuple(Step(n, "aws-plan", f"step {N}", "fail", SAY, G) for n in range(1, 15)),
            AdminBox("warn", SAY, "open", G),
            Teardown("unknown", N),
            Banner(SAY, G),
            5,
        ),
        "rows": [{"s": STEP, "next": True, **_row(f"aws-{N}")}]
        + [{"s": Step(5, "aws-outputs", N, "todo"), "next": False, **_row("aws-outputs")}],
        "admin": [{**_row("aws-admin-open"), "cidr": True}, {**_row("aws-admin-close"), "cidr": False}],
        "restart": SAY,
    },
    "ops_header.html": {
        "gate": None,
        "credentials": f"profile {N}",
        "region": G,
        "facts_reason": SAY,
        "account": f"account unknown: {ACCOUNT} {SAY}",
        "banner": Banner(SAY, G),
    },
    "ops_teardown.html": {
        "t": Teardown("in state", f"{N} repositories"),
        "rows": [_row("aws-down"), _row("aws-down-all")],
    },
}


def _rendered() -> dict[str, str]:
    out = {name: views.render(name, **ctx) for name, ctx in (CASES | OPS_CASES).items()}
    out["ops_off.html"] = views.render("ops.html", **{**OPS_CASES["ops.html"], "off": SAY})
    out["ops_header_off.html"] = views.render(
        "ops_header.html", **{**OPS_CASES["ops_header.html"], "gate": SAY}
    )
    out["turn.html"] = views.step_fragment(_report(), agent="scripted")
    out["said.html"] = views.say_fragment("mom", _turn(), agent="bedrock")
    out["qr.html"] = views.bind_fragment(f"http://localhost:8081/bind/tok?n={N}", "mom")
    return out


def test_no_fragment_carries_a_number_in_text_or_attributes() -> None:
    for name, html in _rendered().items():
        assert phone_hits(html) == [], name
        assert "6135550101" not in html and "555-0102" not in html, name
        for attr in re.findall(
            r'(?:data-[\w-]+|title)="([^"]*)"', html
        ):  # what stage.js reads and hovers show
            assert phone_hits(attr) == [] and "555" not in attr, (name, attr)
        assert not HEALTH_WORDS.search(re.sub(r"<[^>]*>", " ", html)), name


def test_every_template_is_covered_here() -> None:
    names = {p.name for p in TEMPLATES.glob("*.html")}
    assert set(_rendered()) - {"ops_off.html", "ops_header_off.html"} == names and len(names) == 18
