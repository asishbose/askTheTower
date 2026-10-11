"""HTML fragments for the four panes (Jinja2, autoescaped). Every fragment leaves through `redact.mask`.

Outcome chips render the `reason_codes` Tower returned and nothing else (doc 11 §8.1); the pass/fail badge only
compares them with the story's expected codes.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

import segno
from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup
from ref_client.demo import StepReport
from ref_client.transcript import Turn

from demo_ui.redact import mask

ENV = Environment(loader=PackageLoader("demo_ui", "templates"), autoescape=select_autoescape(["html"]))

# doc 11 §5: what each macro shows and what the code does not do yet (code-vs-docs.md)
CAPTIONS: dict[str, str] = {
    "moment-1": "",
    "moment-2": "",
    "moment-3": "",
    "transplant": (
        "D15 remainder: the line-holder's own copy of an UNREACHABLE alert would read \"That person's "
        'phone…"; in this run it is withheld anyway (his line was SIM-swapped at +12 min).'
    ),
}
D1_CAPTION = "D1: nothing texts this link yet. The QR code stands in for the SMS."


def render(template: str, **ctx: Any) -> str:
    return mask(ENV.get_template(template).render(**ctx))


def qr_svg(url: str) -> Markup:
    """The bind link as an inline SVG QR code, drawn here (the binding page has no QR endpoint)."""
    svg = segno.make(url, error="m").svg_inline(scale=4, dark="#0f172a", light="#ffffff")
    return Markup(svg)  # noqa: S704 - segno's own SVG of a URL we were given; not user HTML


def with_phone(url: str, who: str, local: bool) -> str:
    """Locally the phone's mobile data is simulated by `?as=phone-<who>` (e2e §5); on AWS the link is as is."""
    if not local:
        return url
    parts = urlsplit(url)
    query = f"{parts.query}&as=phone-{who}" if parts.query else f"as=phone-{who}"
    return urlunsplit(parts._replace(query=query))


def turn_view(turn: Turn) -> dict[str, Any]:
    result = turn.result or {}
    next_step = result.get("next_step") or {}
    return {
        "utterance": turn.utterance,
        "calls": [f"{c.name}({', '.join(f'{k}={v}' for k, v in c.args.items())})" for c in turn.tool_calls],
        "codes": list(turn.reason_codes),
        "spoken": turn.spoken,
        "next_step": next_step.get("kind"),
    }


def step_fragment(r: StepReport, *, agent: str) -> str:
    return render("turn.html", r=r, turn=turn_view(r.turn) if r.turn else None, agent=agent)


def say_fragment(who: str, turn: Turn, *, agent: str) -> str:
    return render("said.html", who=who, turn=turn_view(turn), agent=agent)


def bind_fragment(url: str, who: str) -> str:
    return render("qr.html", svg=qr_svg(url), who=who, caption=D1_CAPTION, url=url)
