#!/usr/bin/env python3
"""Generate docs/architecture/explainer/ask-the-tower-explainer.drawio (ten pages) — never hand-edit the XML.

The explainer is a narrative for people who will not read the code: a judge, a new engineer, a carrier partner, an
executive. Each page stands alone. It reuses the mxGraph helpers and palette of gen_deploy.py (teal = request path,
amber = proactive path, grey = mock / binding / deferred, dashed = not executed yet) and is checked by check.py
(overlaps, text overflow, edges through boxes, label collisions, bounds).

Facts come from docs/architecture/, artifacts/test-report.md and docs/submission/build-log.md as of AS_OF. Where a
number does not exist yet the page says so instead of inventing one.

    uv run python docs/architecture/diagrams/gen/gen_explainer.py            # write + check
    uv run python docs/architecture/diagrams/gen/gen_explainer.py --check    # fail if the file is stale
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUT = ROOT / "docs" / "architecture" / "explainer" / "ask-the-tower-explainer.drawio"
sys.path.insert(0, str(HERE))
import check as checker  # noqa: E402
from gen_deploy import BOX, FONT, MUTED, Page, esc  # noqa: E402

AS_OF = "9 October 2026"
W, H = 1654, 1080
TEAL, AMBER, GREY, INK = "#2BB3A0", "#E8A33D", "#6B7583", "#0E1726"
NUM = "①②③④⑤⑥⑦⑧⑨⑩"
MAX_BOXES = 12


class XPage(Page):
    """Page with explainer conventions: header, footer, legend, numbered callouts, chips, auto-height text."""

    def __init__(self, n: int, name: str) -> None:
        super().__init__(f"explainer_p{n}", f"{n} {name}", W, H)
        self.n = n
        self.boxes = 0

    # --- auto-sized text ------------------------------------------------------------------------------------------
    @staticmethod
    def need_h(value: str, w: float, size: int, bold: bool = False) -> float:
        style = {"fontSize": str(size), "fontStyle": "1" if bold else "0", "html": "1"}
        _, th = checker.text_extent(value, style, w - 2 * 4 - 4)
        return th + 10

    def para(self, cid: str, x: float, y: float, w: float, value: str, size: int = 13, color: str = FONT,
             bold: bool = False) -> float:  # fmt: skip
        """A wrapped paragraph whose height is computed; returns its bottom y."""
        h = self.need_h(value, w, size, bold)
        self.text(cid, x, y, w, h, value, size=size, color=color, bold=bold, valign="top")
        return y + h

    def callout(self, cid: str, x: float, y: float, w: float, n: int, caption: str, size: int = 14,
                color: str = INK) -> float:  # fmt: skip
        value = f'<b><font color="{color}">{NUM[n - 1]}</font></b> {esc(caption)}'
        return self.para(cid, x, y, w, value, size=size, color=FONT)

    def chip(self, cid: str, x: float, y: float, label: str, kind: str = "teal") -> None:
        fill, stroke = BOX[kind]
        w = len(label) * 0.62 * 11 + 22
        style = (
            f"text;html=1;rounded=1;arcSize=40;fillColor={fill};strokeColor={stroke};align=center;"
            f"verticalAlign=middle;whiteSpace=wrap;fontSize=11;fontStyle=1;fontColor={FONT};spacing=4;"
        )
        self._vertex(cid, x, y, w, 22, esc(label), style)

    # --- boxes are counted: at most 12 per page ----------------------------------------------------------------------
    def box(self, cid: str, x: float, y: float, w: float, h: float, title: str, body: list[str] | None = None,
            kind: str = "grey", dashed: bool = False, align: str = "left", size: int = 16, body_size: int = 14,
            raw: bool = False, thick: bool = False) -> None:  # fmt: skip
        self.boxes += 1
        assert self.boxes <= MAX_BOXES, f"page {self.n}: more than {MAX_BOXES} boxes — split the page"
        super().box(cid, x, y, w, h, title, body, kind, dashed, align, size, body_size, raw)
        if thick:
            c = self.cells[-1]
            c.set("style", c.get("style", "") + "strokeWidth=3;")

    # --- page furniture --------------------------------------------------------------------------------------------
    def header(self, title: str, audience: str, legend: list[tuple[str, str]] | None = None) -> float:
        self.text("hdr_n", 40, 18, 60, 40, f"{self.n}/10", size=14, color=MUTED, bold=True)
        y = self.para("hdr_t", 100, 14, W - 140, esc(title), size=24, color=FONT, bold=True)
        y = self.para("hdr_s", 100, y, W - 140, esc(audience), size=14, color=MUTED)
        if legend:
            parts = [f'<font color="{c}">■</font> {esc(t)}' for c, t in legend]
            y = self.para("hdr_l", 100, y, W - 140, "&nbsp;&nbsp;&nbsp;".join(parts), size=12, color=FONT)
        return y + 6

    def footer(self, nxt: str, detail: str = "") -> None:
        v = f"<b>Read next →</b> {esc(nxt)}"
        if detail:
            v += f'&nbsp;&nbsp;&nbsp;<font color="{MUTED}">detail: {esc(detail)}</font>'
        v += f'&nbsp;&nbsp;&nbsp;<font color="{MUTED}">· true as of {AS_OF}</font>'
        self.text("ftr", 40, H - 50, W - 80, 32, v, size=13, color=FONT)

    # --- edges between box mid-points ------------------------------------------------------------------------------
    def right_to_left(self, cid: str, a: str, b: str, kind: str = "teal", label: str = "", dashed: bool = False,
                      y: float | None = None) -> None:  # fmt: skip
        ax, ay, aw, ah = self.geo[a]
        bx, by, bw, bh = self.geo[b]
        yy = y if y is not None else (max(ay, by) + min(ay + ah, by + bh)) / 2
        at = ((ax + aw + bx) / 2, yy - 12) if label else None
        self.edge(cid, a, b, [(ax + aw, yy), (bx, yy)], label, kind, dashed, at, size=11)

    def top_to_bottom(self, cid: str, a: str, b: str, kind: str = "teal", x: float | None = None,
                      dashed: bool = False, label: str = "") -> None:  # fmt: skip
        ax, ay, aw, ah = self.geo[a]
        bx, by, bw, bh = self.geo[b]
        xx = x if x is not None else (max(ax, bx) + min(ax + aw, bx + bw)) / 2
        at = (xx + 6 + len(label) * 3.2, (ay + ah + by) / 2) if label else None
        self.edge(cid, a, b, [(xx, ay + ah), (xx, by)], label, kind, dashed, at, size=11)


# ======================================================================================================================
# 1 — What it is, in one picture
# ======================================================================================================================


def page1() -> XPage:
    p = XPage(1, "What it is")
    y0 = p.header(
        "Your Echo can ask your carrier whether your line is safe — and only with the line-holder's consent",
        "For everyone — judge, partner, executive, new engineer. Start here; no technical background needed.",
        [(TEAL, "a question and its answer"), (AMBER, "a text to someone you trust"), (GREY, "people and phones")],
    )
    p.para("p1_one", 100, y0 + 4, W - 200,
           "<i>“Your own agent on Alexa+ asking the carrier about your line — from the one device that still works "
           "after your phone goes dead. Consent first.”</i>", size=18, color=FONT)  # fmt: skip

    top = y0 + 60
    p.box("you", 60, top + 20, 250, 150, "You, at home", ["Your phone just went dead.", "You don't know why."], "white")
    p.box("phone", 60, top + 270, 250, 140, "Your phone", ["No signal. Calls and texts", "have stopped."], "grey")
    p.box("echo", 390, top + 20, 270, 150, "Echo with Alexa+", ["On home Wi-Fi, so it still", "works when the phone doesn't."], "teal")  # fmt: skip
    p.box("tower", 740, top, 330, 190, "Tower", [
        "Checks that you may ask about",
        "this line, asks the carrier, and",
        "returns plain yes / no facts",
        "for Alexa+ to say.",
    ], "teal", size=18, thick=True)  # fmt: skip
    p.box("carrier", 1150, top, 444, 190, "Your carrier — standard network APIs", [
        "SIM Swap · Call Forwarding Signal",
        "Device Reachability · Number Verification",
        "(the same CAMARA APIs banks already use)",
    ], "white")  # fmt: skip
    p.box("trusted", 1150, top + 270, 444, 140, "Someone you trust", ["Gets a text message if a line they", "watch, with consent, changes."], "amber")  # fmt: skip

    p.right_to_left("e_ask", "you", "echo", "teal", "you ask")
    p.right_to_left("e_mcp", "echo", "tower", "teal", y=top + 95)
    p.right_to_left("e_car", "tower", "carrier", "teal", y=top + 95)
    p.edge("e_dead", "phone", "you", [(185, top + 270), (185, top + 170)], "went dead", "grey", True, at=(240, top + 220), size=12)  # fmt: skip
    tx, ty, tw, th = p.geo["tower"]
    p.edge("e_sms", "tower", "trusted", [(tx + tw / 2, ty + th), (tx + tw / 2, top + 340), (1150, top + 340)], "a text, when something changes", "amber", at=(tx + tw / 2 + 120, top + 326), size=12)  # fmt: skip

    qy = top + 470
    p.box("qs", 60, qy, 780, 200, "Three questions you can ask", [
        "1.  “Alexa, is my line OK?” — was my SIM moved to another",
        "     device? are my calls being forwarded?",
        "2.  “Is Mom's phone on the network?” — only if Mom shared it.",
        "3.  “Watch Mom's line for me.” — you get a text if her SIM is",
        "     moved or her calls are forwarded.",
    ], "white", size=18, body_size=15)  # fmt: skip
    p.box("rule", 880, qy, 714, 200, "The one rule: consent first", [
        "A line is checked only after its holder has bound it with",
        "one tap on their own phone, and only for the questions they",
        "granted to you. They can take it back any time, and every",
        "check is written to a log they can ask about.",
    ], "amber", size=18, body_size=15, thick=True)  # fmt: skip

    cy = qy + 230
    cw = (W - 120) / 3
    p.callout("c1", 60, cy, cw - 20, 1, "When a SIM is swapped, the phone goes quiet. The Echo on home Wi-Fi is the one device left that can still ask the question.", color=TEAL)  # fmt: skip
    p.callout("c2", 60 + cw, cy, cw - 20, 2, "Tower never guesses and never looks anything up about a person: it asks the carrier yes/no questions and passes back the answers.", color=TEAL)  # fmt: skip
    p.callout("c3", 60 + 2 * cw, cy, cw - 20, 3, "Alexa+ cannot start a conversation, so when something changes the warning goes out as a text message to someone you chose.", color=AMBER)  # fmt: skip
    p.footer("2 · Why the Echo")
    return p


# ======================================================================================================================
# 2 — Why the Echo
# ======================================================================================================================


def page2() -> XPage:
    p = XPage(2, "Why the Echo")
    y0 = p.header(
        "A SIM swap silences your phone first — the Echo on Wi-Fi is the one device still able to ask",
        "For judges, executives and partners: the moment this product exists for.",
        [(GREY, "what happens to you"), (AMBER, "what the attacker gains"), (TEAL, "what Tower adds")],
    )
    ty = y0 + 60
    p.text("tl_lbl", 60, ty - 40, 600, 30, "<b>The SIM-swap moment</b>, left to right", size=16, color=FONT)
    bw, gap = 345, 50
    xs = [60 + i * (bw + gap) for i in range(4)]
    steps = [
        ("t1", "① Your number moves", ["Someone convinces the carrier to", "move your number to their SIM.", "The network logs it the moment", "it happens."], "grey"),  # fmt: skip
        ("t2", "② Your phone goes quiet", ["No signal. It looks exactly like", "bad coverage for the first hour,", "so you wait. Nobody tells you."], "grey"),  # fmt: skip
        ("t3", "③ The codes go to them", ["Bank one-time codes and password", "resets now arrive on the", "attacker's SIM, not yours."], "amber"),  # fmt: skip
        ("t4", "④ Accounts are taken over", ["Money and accounts go while you", "still think it is bad signal. You", "find out from your bank."], "amber"),  # fmt: skip
    ]
    for i, (cid, t, body, kind) in enumerate(steps):
        p.box(cid, xs[i], ty, bw, 190, t, body, kind, size=18, body_size=15)
    for i in range(3):
        p.right_to_left(f"te{i}", f"t{i + 1}", f"t{i + 2}", "grey")

    ey = ty + 270
    p.box("echo", xs[1], ey, bw * 2 + gap, 220, "Meanwhile: the Echo still works", [
        "It is on home Wi-Fi, not on the phone's SIM. So during step ②, you can ask:",
        "<b>You:</b> “My phone just lost signal. Alexa, is my line OK?”",
        "<b>Alexa:</b> “Your SIM was moved to another device at 10:12 today.",
        "If that wasn't you, call your carrier now.”",
    ], "teal", raw=True, thick=True, size=18, body_size=16)  # fmt: skip
    t2x = xs[1] + bw / 2
    p.edge("e_echo", "t2", "echo", [(t2x, ty + 190), (t2x, ey)], "ask here, before ③", "teal", at=(t2x + 80, ty + 230), size=13)  # fmt: skip

    cy = ey + 250
    cw = (W - 120) / 3
    p.callout("c1", 60, cy, cw - 20, 1, "The network logs step ① at once: “was this SIM changed in the last 72 hours?” is a standard question. Banks ask it today; the person whose line it is cannot.", color=TEAL)  # fmt: skip
    p.callout("c2", 60 + cw, cy, cw - 20, 2, "During step ② the Echo is the one device that can still ask. The teal answer is the real sentence the running demo speaks; the time comes from the mock carrier's clock.", color=TEAL)  # fmt: skip
    b3 = p.callout("c3", 60 + 2 * cw, cy, cw - 20, 4, "Tower cannot undo step ④; it shortens the time to notice. Carrier SIM locks and PINs try to prevent step ①. Tower detects it and tells someone: a complement, not a replacement.", color=AMBER)  # fmt: skip

    p.para("src", 60, b3 + 40, W - 120, (
        "<b>Numbers (reported cases, a floor):</b> 971 SIM-swap complaints to the FBI in 2025, $17.4 million in reported "
        "losses (FBI IC3 2025 Internet Crime Report). 205 of the 2024 complaints, $6.3 million, from people aged 60 and over "
        "(FBI IC3 2024). Nearly 3,000 unauthorised SIM swaps filed to the UK National Fraud Database in 2024, up 1,055 % "
        "(Cifas, 7 May 2025). US complaints fell from 2023 to 2025 while UK cases rose. Checked 2026-10-06; sources in "
        "docs/prior-art.md “Numbers” (the deck has no numbers slide)."
    ), size=13, color=MUTED)  # fmt: skip
    p.footer("3 · The three moments")
    return p


# ======================================================================================================================
# 3 — The three moments
# ======================================================================================================================


def page3() -> XPage:
    p = XPage(3, "The three moments")
    y0 = p.header(
        "Three moments, one pattern: you ask, Tower asks the carrier, Alexa+ answers, a phone tells the rest",
        "For judges and executives: the demo, frame by frame. Read each column top to bottom.",
        [(TEAL, "asked and answered by voice"), (AMBER, "a text message"), (GREY, "what the person says / sees")],
    )
    lx, lw = 40, 150
    cx0, cw, gap = 200, 460, 22
    cols = [cx0 + i * (cw + gap) for i in range(3)]
    heads = ["1 · My line — the dead phone", "2 · My calls are being forwarded", "3 · Mom's line, with her consent"]
    for i, h in enumerate(heads):
        p.text(f"h{i}", cols[i], y0, cw, 30, h, size=16, color=FONT, bold=True)
    ry0 = y0 + 40
    rh = [130, 150, 190, 200]
    rgap = 26
    rows = []
    y = ry0
    for h in rh:
        rows.append(y)
        y += h + rgap
    labels = ["What the person says", "What Tower checks", "What Alexa+ answers", "What happens on the phone"]
    for r, lab in enumerate(labels):
        p.text(f"rl{r}", lx, rows[r], lw, rh[r], f"<b>{lab}</b>", size=13, color=MUTED)

    frames = [
        [
            ("“My phone just lost signal. Alexa, is my line OK?”", [], "white"),
            ("Your line is bound to you.", ["Tower asks the carrier: was the SIM changed", "in the last 72 hours? Are calls forwarded?"], "teal"),  # fmt: skip
            ("“Your SIM was moved to another device at 10:12 today. If that wasn't you, call your carrier now.”", [], "teal"),  # fmt: skip
            ("Nothing arrives — the phone has no service.", ["The Echo was the only way to find out", "in time. Next step: call the carrier."], "grey"),  # fmt: skip
        ],
        [
            ("“Is anything forwarding my calls?”", [], "white"),
            ("Same line, same consent.", ["Tower asks the carrier whether", "unconditional forwarding is set."], "teal"),  # fmt: skip
            ("“All your calls have been forwarding since 10:20 today. If you didn't set that, call your carrier.”", ["(In the demo it first repeats moment 1's SIM warning.)"], "teal"),  # fmt: skip
            ("Calls to your number ring another phone.", ["Unconditional forwarding is a classic", "takeover move. Now you know why it is silent."], "grey"),  # fmt: skip
        ],
        [
            ("“Is Mom's line OK?” … “Watch Mom's line for me.”", [], "white"),
            ("Mom bound her line once and granted you “watch”.", ["Same two carrier questions, on her line."], "teal"),  # fmt: skip
            ("“Your line is as it was.” · “Alerts are on for that line. You'll get a text if it's SIM-swapped or forwarded.”", ["(Known issue: the sentence says “your” for Mom.)"], "teal"),  # fmt: skip
            ("Mom's SIM moves → your phone gets a text:", ["“SIM moved to another device at <time> today.", "Not you? Call your carrier now.” If Mom has", "revoked, nothing is sent and the log says why."], "amber"),  # fmt: skip
        ],
    ]
    chips = [("SIM_SWAPPED_RECENT", "teal"), ("CALL_FORWARDING_SET", "teal"), ("OK · then SUPPRESSED_REVOKED", "amber")]
    for c, col in enumerate(frames):
        for r, (title, body, kind) in enumerate(col):
            size = 15 if r in (0, 2) else 14
            p.box(f"f{c}{r}", cols[c], rows[r], cw, rh[r], title, body or None, kind, size=size, body_size=14)
        for r in range(3):
            p.top_to_bottom(f"fe{c}{r}", f"f{c}{r}", f"f{c}{r + 1}", "amber" if (c == 2 and r == 2) else "teal")
        label, kind = chips[c]
        p.chip(f"chip{c}", cols[c] + 10, rows[2] + rh[2] - 30, label, kind)

    cy = rows[3] + rh[3] + 18
    p.para("cap", 200, cy, W - 240, (
        f'<b><font color="{TEAL}">①</font></b> The small chip is the reason code the rule table produced; the sentence is a fixed '
        f'template for that code, never written by a model.&nbsp;&nbsp;<b><font color="{TEAL}">②</font></b> Moment 3 is the product: '
        "a second person is told, because Alexa+ cannot speak first."
    ), size=13)  # fmt: skip
    p.footer("4 · How a question travels", "artifacts/transcripts/moment-1..3.json")
    return p


# ======================================================================================================================
# 4 — How a question travels
# ======================================================================================================================


def page4() -> XPage:
    p = XPage(4, "How a question travels")
    y0 = p.header(
        "A question becomes one database read, two carrier calls and a rule — no model decides anything",
        "For judges and engineers: the request path, hop by hop, with what each step is for.",
        [(TEAL, "request path"), (GREY, "time budget"), (AMBER, "the one guarantee")],
    )
    n = 7
    bw, gap = 190, 32
    x0 = 50
    xs = [x0 + i * (bw + gap) for i in range(n)]
    by = y0 + 20
    hops = [
        ("① Alexa+", ["hears the question,", "picks Tower's tool"]),
        ("② Tower", ["who is asking?", "(linked account)"]),
        ("③ Consent check", ["bound? granted?", "one database read"]),
        ("④ Carrier", ["SIM changed?", "calls forwarded?"]),
        ("⑤ Policy", ["rules in code:", "ok · changed · refuse"]),
        ("⑥ Audit", ["written before", "the answer leaves"]),
        ("⑦ Answer", ["a fixed sentence +", "yes/no facts"]),
    ]
    for i, (t, b) in enumerate(hops):
        p.box(f"h{i}", xs[i], by, bw, 110, t, b, "teal", align="center", size=17, body_size=14, thick=(i == 4))
    for i in range(n - 1):
        p.right_to_left(f"he{i}", f"h{i}", f"h{i + 1}", "teal")

    caps = [
        "Alexa+ picks Tower's tool from its description and sends who is asking and which line (“mine”, “Mom”) — never a phone number.",  # noqa: E501
        "Tower identifies the person from the account they linked. No linked account, no answer.",
        "Is the line bound, and did its holder grant this question to this person? If not, Tower refuses here, before the carrier is asked anything.",  # noqa: E501
        "Two standard carrier calls in parallel, 300 ms limit each. Skipped if a watched line was checked in the last 10 minutes.",  # noqa: E501
        "Deterministic rules turn the yes/no facts into one outcome and a reason code. Every possible decision is listed in a table.",  # noqa: E501
        "The check is added to the line-holder's tamper-evident log. If the log cannot be written, there is no answer.",  # noqa: E501
        "Tower returns a template sentence plus the facts. Alexa+ speaks it, or says it in its own words.",
    ]
    cy = by + 125
    bottoms = [p.para(f"cap{i}", xs[i], cy, bw, esc(c), size=13) for i, c in enumerate(caps)]
    my = max(bottoms) + 25

    p.box("refuse", 50, my, 760, 150, "When the answer is “no” at ③", [
        "“I need to connect your line first — I'll send you a link.”  (not bound)",
        "“Mom hasn't shared that with you.”  (no grant, or revoked)",
        "The carrier is never called, and the refusal is logged too.",
    ], "white", size=17, body_size=14)  # fmt: skip
    p.box("nomodel", 850, my, 754, 150, "No model decides anything here", [
        "Alexa+ only picks the tool and speaks the result.",
        "Between ② and ⑦ there is only code: a lookup, two API calls,",
        "a rule table and a log write. No second AI model.",
    ], "amber", size=17, body_size=14, thick=True)  # fmt: skip

    # --- the 300 ms ruler (design budget, 02 §4) ---------------------------------------------------------------
    ry = my + 185
    p.text("rl_t", 50, ry, 1200, 30, "<b>The time budget</b> — about 300 ms at the 95th percentile against the mock (design target)", size=16, color=FONT)  # fmt: skip
    ry += 40
    scale = 4.6  # px per ms
    segs = [("consent read", 20, "white"), ("two carrier calls, in parallel", 250, "grey"), ("audit", 15, "white")]
    x = 50.0
    for i, (lab, ms, kind) in enumerate(segs):
        w = ms * scale
        p.box(f"rs{i}", x, ry, w, 64, f"{ms} ms", [lab], kind, align="center", size=14, body_size=12)
        x += w + 4
    p.text("rs_pol", x + 6, ry, 220, 64, "+ policy &lt; 1 ms<br>= <b>≈ 300 ms</b>", size=15, color=FONT)
    p.para("rl_m", 50, ry + 80, W - 100, (
        "<b>Measured on a laptop</b> (all containers, local network): 95th percentile 236.7 ms for “is my line OK?” and 311.7 ms for "
        "“is Mom's phone on the network?”; the automated gate fails above 400 ms. "
        "<b>On AWS:</b> — (not measured yet; it runs after the first deploy, prompt 13)."
    ), size=14)  # fmt: skip

    p.para("never", 50, H - 110, W - 100, (
        "<b>Never on this path:</b> a second AI model, a phone number sent toward Alexa+, a location, an answer that was not logged first."
    ), size=14)  # fmt: skip
    p.footer("5 · How an alert travels", "02-request-path.drawio")
    return p


# ======================================================================================================================
# 5 — How an alert travels
# ======================================================================================================================


def page5() -> XPage:
    p = XPage(5, "How an alert travels")
    y0 = p.header(
        "Alexa+ can't speak first, so alerts are texts — same rules, and consent is asked again first",
        "For judges and engineers: the proactive path, from a change at the carrier to a phone that buzzes.",
        [(AMBER, "proactive path"), (GREY, "not on this path"), (TEAL, "shared with the request path")],
    )
    by = y0 + 30
    p.box("ev", 50, by, 250, 110, "① Carrier event", ["“This SIM changed” — pushed", "by the carrier in seconds"], "amber", size=16)  # fmt: skip
    p.box("poll", 50, by + 150, 250, 110, "② Or a scheduled check", ["where the carrier has no", "event feed for that line"], "amber", size=16)  # fmt: skip
    p.box("alerts", 380, by + 60, 240, 140, "③ Alerts", ["fetches the current facts", "from the carrier; compares", "with the last known state"], "amber", size=16)  # fmt: skip
    p.box("policy", 700, by + 60, 240, 140, "④ Same policy", ["the very rules Tower", "uses — the two paths", "cannot disagree"], "teal", size=16, thick=True)  # fmt: skip
    p.box("consent", 1020, by + 60, 240, 140, "⑤ Consent, again", ["still granted? If revoked:", "nothing is sent, the log", "says “suppressed”"], "amber", size=16)  # fmt: skip
    p.box("sms", 1340, by + 60, 260, 140, "⑥ Text message", ["to the consented second", "person (and the holder's", "backup phone); logged first"], "amber", size=16)  # fmt: skip

    ex, ey_, ew, eh = p.geo["ev"]
    ax, ay, aw, ah = p.geo["alerts"]
    p.edge("e1", "ev", "alerts", [(ex + ew, ey_ + 55), (340, ey_ + 55), (340, ay + 40), (ax, ay + 40)], "", "amber")
    px_, py_, pw, ph = p.geo["poll"]
    p.edge("e2", "poll", "alerts", [(px_ + pw, py_ + 55), (340, py_ + 55), (340, ay + 100), (ax, ay + 100)], "", "amber")  # fmt: skip
    p.right_to_left("e3", "alerts", "policy", "amber")
    p.right_to_left("e4", "policy", "consent", "amber", "changed?")
    p.right_to_left("e5", "consent", "sms", "amber", "yes")

    ny = by + 330
    p.box("alexa", 380, ny, 560, 150, "Alexa+ can never speak first — that's why this is an SMS", [
        "An Alexa+ add-on only answers when asked. Alexa+ is not on this path",
        "at all. If Mom later asks her Echo, the request path runs and gives",
        "the same answer from the same rules.",
    ], "grey", dashed=True, size=16, body_size=14)  # fmt: skip
    p.box("swapped", 1020, ny, 580, 150, "Never texted to the line that was just swapped", [
        "After a SIM swap the attacker holds that number. The text goes to",
        "the second person, and replies from the swapped line are ignored",
        "for 24 hours.",
    ], "grey", dashed=True, size=16, body_size=14)  # fmt: skip

    cy = ny + 190
    cw = (W - 140) / 3
    p.callout("c1", 50, cy, cw - 20, 1, "Events first: the carrier's standard subscription pushes a SIM-swap event to Tower's Alerts service within seconds of the change.", color=AMBER)  # fmt: skip
    p.callout("c2", 50 + cw + 10, cy, cw - 20, 2, "Scheduled checks are the fallback: a daily 8 am check for fraud; every 30 or 5 minutes for the care profiles on the roadmap slides.", color=AMBER)  # fmt: skip
    b5 = p.callout("c3", 50 + 2 * (cw + 10), cy, cw - 20, 6, "Only a change sends a text, and the same alert is not repeated within 6 hours. Where a reply is required, no reply in 15 minutes texts the next person.", color=AMBER)  # fmt: skip
    p.para("dem", 50, b5 + 30, W - 100, (
        "<b>In the demo:</b> the carrier is the mock and the text goes to a log on the laptop. A real text needs AWS SMS — deferred until the first AWS deploy."
    ), size=14, color=MUTED)  # fmt: skip
    p.footer("6 · Consent, binding and privacy", "03-proactive-path.drawio")
    return p


# ======================================================================================================================
# 6 — Consent, binding and privacy
# ======================================================================================================================


def page6() -> XPage:
    p = XPage(6, "Consent, binding and privacy")
    y0 = p.header(
        "Nothing is checked until the line-holder taps once on their own phone — and they can take it back",
        "For partners, judges and anyone asking “is this safe?”: how consent works and what Tower never does.",
        [(GREY, "binding and consent"), (TEAL, "what flows"), (AMBER, "never")],
    )
    by = y0 + 20
    p.box("ph", 50, by, 280, 150, "① The phone, Wi-Fi off", ["Alexa+ texts a link. The holder", "opens it on their phone over", "mobile data and taps once."], "grey", size=16)  # fmt: skip
    p.box("nv", 410, by, 300, 150, "② The carrier vouches", ["Number Verification: the carrier", "recognises the phone's data", "session. Nothing is typed."], "grey", size=16)  # fmt: skip
    p.box("gr", 790, by, 380, 150, "③ Grants: per line, per question", ["Mom's line → Asish: “watch” ✓", "Mom's line → Asish: “reachable?” ✗", "Each grant names a person and an alias."], "grey", size=16)  # fmt: skip
    p.box("rv", 1250, by, 350, 150, "④ Revoke on the page", ["One tap on the same page.", "Not by voice, on purpose — never", "a misheard sentence."], "grey", size=16)  # fmt: skip
    p.right_to_left("e1", "ph", "nv", "grey", "one tap")
    p.right_to_left("e2", "nv", "gr", "grey", "bound")
    p.right_to_left("e3", "gr", "rv", "grey", "any time")

    vy = by + 200
    p.box("au", 50, vy, 520, 200, "⑤ An audit the holder can hear", [
        "Every check, allowed or refused, is logged on the line:",
        "who asked, which question, the outcome and why.",
        "“Alexa, who checked my line this week?”",
        "The log is hash-chained, so a changed row is detectable.",
    ], "grey", size=16)  # fmt: skip
    p.box("bio", 620, vy, 980, 200, "⑥ Verification, not retrieval — booleans in, booleans out", [
        "<b>In from the carrier:</b>  SIM changed recently? yes/no + time  ·  calls forwarded? yes/no  ·  phone on the network? yes/no",
        "<b>Out to Alexa+:</b>  ok / changed / refused, a reason code, a fixed sentence, the same yes/no facts",
        "<b>Never:</b>  a phone number, a location, call or message content, a carrier error message",
    ], "teal", raw=True, size=16, body_size=14)  # fmt: skip

    ny = vy + 240
    p.box("never", 50, ny, 1550, 170, "What Tower never does", [
        "✗ Location of any kind — not requested, not returned, not stored.    ✗ Call or message content.",
        "✗ Any question about a line its holder has not bound.    ✗ Anything a model can override — the rules are code.",
        "✗ Speaking unprompted — Alexa+ only answers; alerts are text messages.    ✗ Texting the number that was just swapped.",
    ], "amber", size=18, body_size=15, thick=True)  # fmt: skip

    cy = ny + 190
    p.callout("c1", 50, cy, 760, 1, "Binding needs the phone on mobile data: the Echo is on Wi-Fi and cannot prove a number, so the one tap happens on the phone, once.")  # fmt: skip
    p.callout("c2", 850, cy, 750, 2, "In the demo the carrier is a mock; a stand-in signal plays the part of the mobile-data session. On a real carrier the network does it.")  # fmt: skip
    p.footer("7 · The stack", "04-binding-flow.drawio")
    return p


# ======================================================================================================================
# 7 — The stack
# ======================================================================================================================


def page7() -> XPage:
    p = XPage(7, "The stack")
    y0 = p.header(
        "Alexa+ talks, Tower decides in code, AgentCore hosts it — Bedrock is only the test client",
        "For engineers and the AWS / Alexa+ track judges: the layers, what each is, and what it is built with.",
        [(TEAL, "request path"), (AMBER, "proactive path"), (GREY, "carrier, state, test client"), (INK, "thick border = Amazon Bedrock AgentCore (the track's ask)")],  # fmt: skip
    )
    lx = 50
    lw = 170
    bx = 240
    y = y0 + 10
    rows = [("Surfaces", 110), ("Tower", 110), ("Carrier gateway", 100), ("Carrier", 90), ("Proactive", 100), ("State", 90)]  # fmt: skip
    ys = []
    for name, h in rows:
        ys.append((y, h))
        p.text(f"l_{name}", lx, y, lw, h, f"<b>{esc(name)}</b>", size=16, color=FONT)
        y += h + 30
    (s_y, s_h), (t_y, t_h), (g_y, g_h), (c_y, c_h), (a_y, a_h), (d_y, d_h) = ys

    p.box("alexa", bx, s_y, 560, s_h, "Alexa+ (Alexa+ MCP Toolkit)", [
        "The voice client: picks a tool, speaks the result, never speaks first.",
        "Registration and simulator run: deferred.",
    ], "teal", dashed=True)  # fmt: skip
    p.box("bind", bx + 590, s_y, 520, s_h, "Binding page (FastAPI)", [
        "The one tap on the phone; grants and revoke.",
        "Local container ✓ · AWS Lambda + API Gateway: deferred.",
    ], "grey")  # fmt: skip
    p.box("tower", bx, t_y, 1110, t_h, "Tower MCP server — AgentCore Runtime", [
        "FastMCP over Streamable HTTP, Python 3.12. Three tools. Policy, consent and audit are libraries inside it, so there is no extra",
        "network hop. Local container ✓ · on AgentCore Runtime: written, deferred.",
    ], "teal", thick=True)  # fmt: skip
    p.box("gw", bx, g_y, 800, g_h, "AgentCore Gateway + Identity", [
        "CAMARA OpenAPI files become MCP tools; Identity holds the carrier OAuth credentials.",
        "Locally a direct HTTPS client does the same job ✓ · Gateway path: deferred.",
    ], "teal", dashed=True, thick=True)  # fmt: skip
    p.box("obs", bx + 830, g_y, 280, g_h, "AgentCore Observability", ["traces per tool call", "deferred"], "teal", dashed=True, thick=True)  # fmt: skip
    p.box("mock", bx, c_y, 1110, c_h, "Mock carrier — CAMARA-conformant (FastAPI), the demo default", [
        "Scenario engine and clock. A carrier sandbox is one setting away (no sandbox access yet).",
    ], "grey")  # fmt: skip
    p.box("alerts", bx, a_y, 1110, a_h, "Alerts — Lambda + EventBridge Scheduler + SNS", [
        "Carrier events and scheduled checks → the same policy → SMS. Local: in-process scheduler, SMS to a log ✓ · AWS: deferred.",
    ], "amber")  # fmt: skip
    p.box("state", bx, d_y, 1110, d_h, "State — DynamoDB + KMS", [
        "Users, lines, grants, watches, audit. Lines are keyed by a keyed hash, never the number; the number is stored encrypted.",
    ], "grey")  # fmt: skip
    p.box("bedrock", 1400, s_y, 200, d_y + d_h - s_y, "Amazon Bedrock", [
        "reference client only",
        "",
        "A Strands agent: the",
        "test harness, and the",
        "demo client if Alexa+",
        "access slips.",
        "",
        "Not on the request or",
        "proactive path.",
        "Model runs: deferred.",
    ], "grey", dashed=True, align="center")  # fmt: skip

    ax, ay, aw, ah = p.geo["alexa"]
    p.top_to_bottom("e_at", "alexa", "tower", "teal", x=ax + 200, label="MCP")
    p.top_to_bottom("e_tg", "tower", "gw", "teal", x=ax + 200)
    p.top_to_bottom("e_gm", "gw", "mock", "teal", x=ax + 200, label="CAMARA, OAuth")
    p.top_to_bottom("e_ma", "mock", "alerts", "amber", x=ax + 900, label="events")

    p.para("note", 50, d_y + d_h + 30, W - 100, (
        "<b>Not used, on purpose:</b> AgentCore Memory, Knowledge Bases, fine-tuning, a model for phrasing. "
        "<b>Status:</b> every layer runs locally and is tested; every AWS hosting line is written and statically validated, not yet deployed (page 10)."
    ), size=14)  # fmt: skip
    p.footer("8 · Where it runs", "01-component-map.drawio")
    return p


# ======================================================================================================================
# 8 — Where it runs
# ======================================================================================================================


def page8() -> XPage:
    p = XPage(8, "Where it runs")
    y0 = p.header(
        "Same five containers, three places to run them — only the hosting changes",
        "For engineers and platform teams: one code base on a laptop, on AWS with AgentCore, and on Kubernetes.",
        [(TEAL, "proven (ran, transcripts match)"), (GREY, "deferred — written, not yet run (dashed)"), (AMBER, "shared prerequisite")],  # fmt: skip
    )
    p.box("imgs", 50, y0 + 10, 1550, 70, "Five images, built once from the same code", [
        "tower-mcp · mock-carrier · binding-page · alerts · ref-client  —  Python 3.12, non-root, arm64-ready",
    ], "white", align="center", size=17, body_size=15)  # fmt: skip
    cy = y0 + 130
    cw, gap = 500, 25
    xs = [50 + i * (cw + gap) for i in range(3)]
    hz = 560
    p.zone("z0", xs[0], cy, cw, hz, "Laptop — Docker Compose (the demo default)", "teal", size=15)
    p.zone("z1", xs[1], cy, cw, hz, "AWS — AgentCore + serverless", "grey", dashed=True, size=15)
    p.zone("z2", xs[2], cy, cw, hz, "Kubernetes — kind ✓, EKS deferred", "grey", dashed=True, size=15)
    for i in range(3):
        x = xs[i] + cw - 60
        p.edge(f"ei{i}", "imgs", f"z{i}", [(x, y0 + 80), (x, cy)], "", "grey")
    hosting = [
        ["Tower: container", "Carrier calls: direct client → mock", "Mock carrier: container",
         "Binding page: container", "Alerts: container; SMS → log", "Store: DynamoDB Local"],
        ["Tower: AgentCore Runtime", "Carrier calls: Gateway + Identity", "Mock carrier: Fargate",
         "Binding page: Lambda + API Gateway", "Alerts: Lambda + Scheduler + SNS", "Store: DynamoDB + KMS"],
        ["Tower: Deployment + Ingress", "Carrier calls: direct client → mock pod", "Mock carrier: Deployment",
         "Binding page: Deployment + Ingress", "Alerts: Deployment + CronJobs + SNS", "Store: DynamoDB + KMS (EKS)"],
    ]  # fmt: skip
    status = [
        ("<b>Ran ✓</b> — transcripts match golden 4/4", "teal", False),
        ("<b>Deferred</b> — terraform validate passes; never planned or applied", "grey", True),
        ("<b>kind ✓ 4/4</b> · EKS: not run, deferred", "grey", True),
    ]
    cmds = [
        ["make up", "make seed", "make demo", "make down"],
        ["make ecr-up  (once)", "make push ENV=aws", "make plan → make deploy", "make demo ENV=aws",
         "make down ENV=aws  (keeps ECR)", "make down-all  (after judging)"],
        ["make helm-kind  (kind ✓)", "make deploy-eks", "make demo ENV=eks", "make down-eks"],
    ]  # fmt: skip
    for i in range(3):
        st, kind, dashed = status[i]
        body = [esc(h) for h in hosting[i]] + [st]
        p.box(f"host{i}", xs[i] + 20, cy + 45, cw - 40, 250, "Same code, different hosting", body, kind, dashed=dashed, size=17, body_size=15, raw=True)  # fmt: skip
        p.box(f"cmd{i}", xs[i] + 20, cy + 330, cw - 40, 205, "Make commands", cmds[i], "white", dashed=dashed, size=17, body_size=15)  # fmt: skip
        p.top_to_bottom(f"ec{i}", f"host{i}", f"cmd{i}", "grey")
    ey = cy + hz + 50
    p.box("ecr", xs[1], ey, cw * 2 + gap, 110, "ECR registry — shared prerequisite for AWS and EKS", [
        "Its own Terraform root: make ecr-up once. Survives make down ENV=aws — the images are kept;",
        "only make down-all deletes it. Not created yet — deferred.",
    ], "amber", dashed=True, size=17, body_size=15)  # fmt: skip
    for i in (1, 2):
        x = xs[i] + cw / 2
        p.edge(f"er{i}", "ecr", f"z{i}", [(x, ey), (x, cy + hz)], "images", "amber", True, at=(x + 34, ey - 25), size=12)  # fmt: skip
    p.para("c1", xs[0], ey, cw, (
        f'<b><font color="{TEAL}">①</font></b> The claim: <b>make demo ENV=local|eks|aws</b> prints the same transcripts. '
        "Local and kind already do; AWS and EKS are the deferred half of the proof."
    ), size=14)  # fmt: skip
    p.footer("9 · How we know it works", "08-agentcore-deployment.drawio · deployment-agentcore.md")
    return p


# ======================================================================================================================
# 9 — How we know it works
# ======================================================================================================================


def page9() -> XPage:
    p = XPage(9, "How we know it works")
    y0 = p.header(
        "1,218 tests pass on a laptop and the mock matches the CAMARA specs; cloud numbers come next",
        "For judges and engineers: the evidence, with the real counts from artifacts/test-report.md (run 2026-10-07).",
        [(TEAL, "passed"), (GREY, "deferred / not measured (dashed)"), (AMBER, "gates that fail the build")],
    )
    cx = 420
    py = y0 + 20
    p.box("e2e", cx - 150, py, 300, 110, "End to end — 26 passed", ["0 failed · 3 skipped (EKS only)", "the three moments + chaos"], "teal", align="center", size=16)  # fmt: skip
    p.box("int", cx - 250, py + 125, 500, 120, "Integration — 825 passed", ["0 failed · 1 skipped (AWS latency)", "contract, conformance, privacy, latency; DynamoDB Local"], "teal", align="center", size=16)  # fmt: skip
    p.box("unit", cx - 350, py + 260, 700, 120, "Unit — 367 passed", ["0 failed · 0 skipped", "coverage, unit + integration: libraries 94.8 % · services 90.6 %"], "teal", align="center", size=16)  # fmt: skip

    gx, gw = 860, 740
    p.box("conf", gx, py, gw, 110, "Conformance: mock vs CAMARA specs ✓", [
        "15 operations, 1,036 generated cases (schemathesis), 0 failing.",
        "The mock is spec-conformant; it does not claim real-carrier timing.",
    ], "teal", size=16, body_size=14)  # fmt: skip
    p.box("priv", gx, py + 125, gw, 120, "Privacy greps ✓  (19 tests, zero hits)", [
        "No phone-number-shaped string and no health word in any log, transcript,",
        "answer or SMS; never an alert to the swapped line. make privacy-grep re-runs it.",
    ], "amber", size=16, body_size=14)  # fmt: skip
    p.box("lat", gx, py + 260, gw, 120, "Latency gate: 95th percentile < 400 ms", [
        "Laptop compose: is my line OK? 236.7 ms · is Mom reachable? 311.7 ms ✓",
        "AWS: — (measured in prompt 13, make latency-aws; deferred)",
    ], "amber", size=16, body_size=14)  # fmt: skip

    sy = py + 420
    p.box("same", 50, sy, 1550, 80, "Same transcripts everywhere", [
        "Local compose 4/4 ✓ · kind 4/4 ✓ · EKS — deferred · AWS — deferred · Alexa+ tool-choice corpus on Bedrock — not measured yet",
    ], "grey", dashed=True, size=16, body_size=14)  # fmt: skip
    p.box("show", 50, sy + 110, 1550, 150, "The showcase, in nine steps (the video and the live run)", [
        "① Mock carrier and its clock  →  ② Policy table: every decision  →  ③ Binding: the one tap  →  ④ Moment 1: the dead phone",
        "→  ⑤ Moment 2: forwarding  →  ⑥ Moment 3: Mom's line, the text, revoke  →  ⑦ Transplant story: 20 minutes dark",
        "→  ⑧ Audit: Mom's view, chain verified  →  ⑨ make demo: the same transcripts from a terminal",
    ], "white", size=16, body_size=14)  # fmt: skip
    p.para("cl", 50, sy + 280, 1550, (
        f'<b><font color="{AMBER}">Gates.</font></b> Every gate fails the build: 100 % pass per layer, coverage ≥ 90 % / ≥ 80 %, 0 failing conformance operations, zero privacy hits, p95 under 400 ms. '
        f'<br><b>Not claimed.</b> What the tests deliberately do not claim: real-carrier timing, that Alexa+ always picks the right tool, that “reachable” means the phone will ring.'
    ), size=13)  # fmt: skip
    p.footer("10 · What is real, what is mock, what is next", "06-test-topology.drawio · artifacts/test-report.md")
    return p


# ======================================================================================================================
# 10 — What is real, what is mock, what is next
# ======================================================================================================================


def page10() -> XPage:
    p = XPage(10, "Real, mock, next")
    y0 = p.header(
        f"What is real, what is mock, and what is next — as of {AS_OF}",
        "For everyone, last: an honest status. Dashed = not executed yet. Source: docs/submission/build-log.md.",
        [(TEAL, "real — built and run"), (GREY, "mock — stands in for a carrier or a cloud service"), (INK, "next — dashed, deferred")],  # fmt: skip
    )
    cy = y0 + 10
    cw, gap = 505, 17
    xs = [50 + i * (cw + gap) for i in range(3)]
    hz = 830
    p.zone("zr", xs[0], cy, cw, hz, "Real", "teal", size=16)
    p.zone("zm", xs[1], cy, cw, hz, "Mock", "grey", size=16)
    p.zone("zn", xs[2], cy, cw, hz, "Next", "white", dashed=True, size=16)
    bx = [x + 15 for x in xs]
    bw = cw - 30

    def stack(col: int, items: list[tuple[str, list[str], str, bool, int]]) -> None:
        y = cy + 45
        for i, (t, b, kind, dashed, h) in enumerate(items):
            p.box(f"b{col}{i}", bx[col], y, bw, h, t, b, kind, dashed=dashed, size=18, body_size=15)
            y += h + 14

    stack(0, [
        ("The code", ["Five services, four libraries, three tools.", "Deterministic policy; consent, binding,", "hash-chained audit; alerts with escalation."], "teal", False, 175),  # fmt: skip
        ("The tests", ["1,218 passing: 367 unit · 825 integration ·", "26 end to end. Conformance 1,036 cases,", "0 failing. Coverage 94.8 % / 90.6 %."], "teal", False, 175),  # fmt: skip
        ("Runs end to end", ["Laptop compose and kind (Kubernetes):", "the three moments + transplant story,", "transcripts match golden 4/4."], "teal", False, 175),  # fmt: skip
        ("The AgentCore deployment path", ["Terraform (main + its own ECR root),", "image push, make targets, runbooks.", "Written and validated offline — not yet", "applied: deferred."], "teal", True, 175),  # fmt: skip
    ])  # fmt: skip
    stack(1, [
        ("The carrier", ["A CAMARA-conformant mock with a scenario", "clock. Every network fact in the demo", "comes from it, and the demo says “mock”."], "grey", False, 150),  # fmt: skip
        ("Mobile data", ["The mock recognises the phone by a stand-in", "header; a real carrier recognises the", "phone's data session. Needs a real carrier."], "grey", False, 150),  # fmt: skip
        ("SMS, locally", ["Alerts write the text to a log on the laptop.", "A real text needs AWS SMS (sandbox, verified", "numbers) — deferred with the AWS deploy."], "grey", False, 150),  # fmt: skip
    ])  # fmt: skip
    stack(2, [
        ("Carrier sandbox", ["No sandbox credentials yet (the deck's ask).", "Swapping it in is one setting; real event", "timing and the real one-tap bind need it."], "white", True, 175),  # fmt: skip
        ("Alexa+", ["Registration, account linking (Cognito pool)", "and the simulator run: not done. Toolkit is", "US-only; the team is in Canada."], "white", True, 175),  # fmt: skip
        ("AWS, EKS, video", ["ECR → push → plan → deploy, AWS latency,", "SMS sandbox, cost; the EKS window; the", "demo video and screenshots. All TODO(human)."], "white", True, 175),  # fmt: skip
        ("Roadmap (deck slide 10)", ["Device-swap guard · recycled-number check ·", "passwordless login. Open: spoken sentences", "say “your” for Mom (fixed-person template)."], "white", True, 175),  # fmt: skip
    ])  # fmt: skip
    p.footer("1 · back to the start", "docs/submission/build-log.md · README “Status and limits”")
    return p


PAGES = (page1, page2, page3, page4, page5, page6, page7, page8, page9, page10)


def bounds(xml_path: Path) -> list[str]:
    """Everything inside the page (check.py does not know page size)."""
    out = []
    for name, vs, _ in checker.load(xml_path):
        for v in vs.values():
            if v.x < 0 or v.y < 0 or v.x + v.w > W or v.y + v.h > H:
                out.append(f"{xml_path.name} [{name}]: {v.id} outside the {W}×{H} page")
    return out


def build() -> str:
    mx = ET.Element("mxfile", host="app.diagrams.net", agent="Ask the Tower design generator (explainer)", type="device")
    for f in PAGES:
        mx.append(f().element())
    ET.indent(mx, space="  ")
    return ET.tostring(mx, encoding="unicode") + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if the file on disk differs from the generator")
    args = ap.parse_args()
    text = build()
    if args.check:
        if not OUT.exists() or OUT.read_text("utf-8") != text:
            print(f"{OUT.name} is stale: run gen_explainer.py")
            return 1
    else:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, "utf-8")
        print(f"wrote {OUT.relative_to(ROOT)}")
    issues = checker.check(OUT) + bounds(OUT)
    for i in issues:
        print("  " + i)
    print(f"check: {len(issues)} issue(s)")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
