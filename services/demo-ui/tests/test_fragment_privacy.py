"""Doc 11 §8.3, §11 "Privacy" over the prompt-21 markup: every one of the twelve fragments, rendered through
`views.render` with a demo number in every free string it shows, carries no number, in text or in the new
`data-*` and `title` attributes `stage.js` reads.

The wire sweeps (`test_real_backends`, `test_macros_make_demo`, `test_served`) reach ten fragments; `said.html`
(free text needs Bedrock) and `unavailable.html` (a failing neighbour) are reached only here. The last test fails
when a template is added without a case below.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from demo_ui import views
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


def _rendered() -> dict[str, str]:
    out = {name: views.render(name, **ctx) for name, ctx in CASES.items()}
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
    assert set(_rendered()) == {p.name for p in TEMPLATES.glob("*.html")}
