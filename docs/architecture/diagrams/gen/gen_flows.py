#!/usr/bin/env python3
"""Generate 09-refusal-paths.drawio and 10-watch-profiles.drawio (one page each). Never hand-edit the XML.

Same palette and mxGraph helpers as gen_deploy.py (08). Every edge is an absolute polyline so check.py can verify
that no edge runs through a box and no label sits on a box. Every file:line on these pages was read in the code
on 2026-10-09; docs/architecture/code-vs-docs.md lists where the code and the design docs disagree (D-numbers).

    uv run python docs/architecture/diagrams/gen/gen_flows.py            # write both + check
    uv run python docs/architecture/diagrams/gen/gen_flows.py --check    # fail if a file is stale
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check as checker  # noqa: E402
from gen_deploy import BOX, EDGE, ROOT, Page, esc  # noqa: E402

OUT09 = HERE.parent / "09-refusal-paths.drawio"
OUT10 = HERE.parent / "10-watch-profiles.drawio"

BOX.setdefault("exit", ("#FBEAEA", "#C57B7B"))  # the refusal-note pink of 02-04
BOX.setdefault("axis", ("#0E1726", "#0E1726"))
EDGE.setdefault("refuse", ("#C57B7B", 2))


def header(p: Page, cid: str, x: float, y: float, w: float, h: float, label: str, amber: bool = False) -> None:
    """The dark lifeline head of 02-04 (amber for the person's phone)."""
    fill, font = ("#E8A33D", "#0E1726") if amber else ("#0E1726", "#FFFFFF")
    style = (
        f"rounded=1;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={fill};fontSize=11;fontColor={font};"
        "align=center;verticalAlign=middle;dashed=0;fontStyle=1;spacing=4;"
    )
    p._vertex(cid, x, y, w, h, label, style)


def titles(p: Page, title: str, sub: str, sub2: str = "") -> None:
    p.text("t_main", 30, 14, p.w - 60, 30, esc(title), size=19, color="#1B2B45", bold=True)
    p.text("t_sub", 30, 46, p.w - 60, 20, esc(sub), size=11)
    if sub2:
        p.text("t_sub2", 30, 66, p.w - 60, 20, esc(sub2), size=11)


# =====================================================================================================================
# 09 — the five refusal exits of a tool call
# =====================================================================================================================

LANE = [
    ("1 · identity → user_id", "auth.py Authenticator.authenticate; no identity → HTTP 401, not an envelope"),
    ("2 · consent.resolve(user_id, line)", "one Query (resolve.py:47); store down → SERVICE_UNAVAILABLE (common.py:88)"),
    ("3 · consent gate", "policy's own first rules (engine.py:27) via common.consent_gate (common.py:109)"),
    ("4 · facts", "Watch.last_state if ≤ FRESH 10 min, else SIM swap ‖ call forwarding, 300 ms each, no retry"),
    ("5 · evaluate_line(facts, consent, now)", "pure; carrier error is checked before staleness (engine.py:35)"),
    ("6 · audit.append — blocks the answer", "chain-head GetItem + TransactWriteItems (writer.py:77)"),
    ("7 · ToolResult → Alexa+", "summary · facts · reason_codes · next_step · checked_at"),
]

# kind per row: p = passed, x = exit, s = not reached, n = note (no row); then title, body
BLOCKS: list[dict[str, object]] = [
    {
        "code": "NOT_BOUND",
        "rows": [
            ("p", "user_id", "the caller is known"),
            ("p", "no line", "no Line for \"self\" → bound=False (resolve.py:62); an alias is never NOT_BOUND (D18)"),
            ("x", "EXIT · NOT_BOUND", "engine.py:28 — before any fact, so nothing about the line leaks"),
            ("s", "not reached", "no carrier call"),
            ("s", "not reached", "the gate is the policy"),
            ("n", "no audit row", "no line_id to key it on (line_is_ok.py:133) — the one unaudited answer (D3)"),
            ("p", "next_step bind_line + URL", "build_next_step (next_step.py:30) mints a 10-min bind token, writes Users + BindTokens"),
        ],
        "jump": (2, 6),
        "says": "“I need to connect your line first — I'll send you a link.”",
        "note": "Nothing texts the link today: the URL only reaches Alexa+ in next_step (D1).",
    },
    {
        "code": "NO_CONSENT",
        "rows": [
            ("p", "user_id", "the caller is known"),
            ("p", "no grant / revoked / out of scope", "alias with no grant (resolve.py:90, D18) or only revoked ones (resolve.py:89), or the grant does not cover this tool (common.py:102)"),
            ("x", "EXIT · NO_CONSENT", "engine.py:30 — grant none or revoked_at set"),
            ("s", "not reached", "no carrier call"),
            ("s", "not reached", "the gate is the policy"),
            ("p", "audit row", "refused · NO_CONSENT on that line (common.py:150 audited); an alias with no grant has no line, so no row"),
            ("p", "next_step ask_consent", "no link: the line-holder grants on their own page"),
        ],
        "jump": (2, 5),
        "says": "“Mom hasn't shared that with you.”",
        "note": "{Name} is the alias the caller used; without one, “That person”.",
    },
    {
        "code": "STALE_DATA",
        "rows": [
            ("p", "user_id", "the caller is known"),
            ("p", "owner or active grant", "one read"),
            ("p", "consent holds", "the line may be checked"),
            ("p", "carrier times out", "a call > 300 ms (timeouts.py:17, errors.py:124) or breaker open (errors.py:138) and the live answer would be refused; a Watch state exists → use it (line_is_ok.py)"),
            ("x", "EXIT · STALE_DATA", "that state is older than STALE 10 min (engine.py:45)"),
            ("p", "audit row", "refused · STALE_DATA · source=watch"),
            ("p", "last-known facts", "stale=true, as_of = the Watch's time; next_step none"),
        ],
        "jump": None,
        "says": "“I can't reach your carrier right now. The last I heard was at 2:14 today.”",
        "note": "Neutral on purpose: the last state may show a swap or forwarding (D5, fixed 2026-10-09).",
    },
    {
        "code": "CARRIER_ERROR",
        "rows": [
            ("p", "user_id", "the caller is known"),
            ("p", "owner or active grant", "one read"),
            ("p", "consent holds", "the line may be checked"),
            ("p", "carrier fails, nothing to fall back on", "5xx (503 → errors.py:49), or breaker open after 5 × 5xx for 60 s (breaker.py:42), and no Watch state"),
            ("x", "EXIT · CARRIER_ERROR", "any fact unknown and no known alarm (engine.py:52), checked before staleness"),
            ("p", "audit row", "refused · CARRIER_ERROR · source=carrier"),
            ("p", "no facts", "next_step none; no carrier text ever reaches the result"),
        ],
        "jump": None,
        "says": "“I can't reach your carrier right now. Try again in a minute.”",
        "note": "One failed call is enough to refuse, unless the other fact alarms (D4, fixed 2026-10-09).",
    },
    {
        "code": "SERVICE_UNAVAILABLE",
        "rows": [
            ("p", "user_id", "the caller is known"),
            ("p", "consent read", "a store error here is the same exit (common.py:88)"),
            ("p", "consent holds", "or a consent refusal, which also needs its audit row"),
            ("p", "facts", "stored state or carrier"),
            ("p", "outcome decided", "ok, changed or a refusal"),
            ("x", "EXIT · audit write fails", "append raises AuditWriteFailed (writer.py:100); release() never calls respond (writer.py:110)"),
            ("p", "decided answer dropped", "server.invoke → errors.refusal_code (errors.py:44) → common.unavailable (common.py:157); no facts"),
        ],
        "jump": None,
        "says": "“Something on my side isn't available. Try again in a minute.”",
        "note": "No audit, no answer: the outcome is never released (e2e §8 rule 5).",
    },
]


def page_refusals() -> Page:
    lane_x, lane_w = 30, 240
    bx0, bw, bgap = 290, 344, 16
    top, row_h, row_gap = 112, 76, 14
    y_rows = [top + 60 + i * (row_h + row_gap) for i in range(len(LANE))]
    width = bx0 + len(BLOCKS) * bw + (len(BLOCKS) - 1) * bgap + 30
    says_y = y_rows[-1] + row_h + 24
    note_y = says_y + 92
    foot_y = note_y + 70
    p = Page("09_refusal_paths", "09 Refusal Paths", width, foot_y + 130)
    titles(
        p,
        "Refusal paths — the five ways a tool call ends without an answer about the line",
        "line_is_ok shown; is_reachable and watch_line share steps 1–3 and 6–7. Each column is one exit: where it leaves, "
        "the code that decides it, and the sentence Alexa+ is handed. Alexa+ phrases; it never decides.",
        "Pink = the exit. Dashed = not reached. Teal arrows = the path taken; red = straight to the result after a "
        "consent exit. D-numbers point to docs/architecture/code-vs-docs.md.",
    )

    header(p, "lane_h", lane_x, top, lane_w, 46, "Step (02 §2, as built)")
    for i, (t, b) in enumerate(LANE):
        p.box(f"lane{i}", lane_x, y_rows[i], lane_w, row_h, t, [b], "head", size=10, body_size=9)
    p.box("lane_says", lane_x, says_y, lane_w, 80, "What Alexa+ is handed", ["summary, from tower_policy/phrasing.py TEMPLATES (03 §4)"], "teal", size=10)  # fmt: skip
    p.box("lane_note", lane_x, note_y, lane_w, 56, "As built", ["what differs from the docs"], "head", size=10)

    for k, blk in enumerate(BLOCKS):
        x = bx0 + k * (bw + bgap)
        code = str(blk["code"])
        header(p, f"b{k}_h", x, top, bw, 46, f"{k + 1} · {code}")
        rows = blk["rows"]
        assert isinstance(rows, list)
        for i, (kind, t, b) in enumerate(rows):
            style = {"p": "white", "x": "exit", "s": "ghost", "n": "ghost"}[kind]
            p.box(f"b{k}_r{i}", x, y_rows[i], bw, row_h, t, [b], style, dashed=kind in "sn", size=10, body_size=9)
        # the path taken
        jump = blk["jump"]
        last = len(rows) - 1
        exit_row = next(i for i, r in enumerate(rows) if r[0] == "x")
        cx = x + bw / 2
        for i in range(last):
            if jump is not None and i >= exit_row:
                break
            y0, y1 = y_rows[i] + row_h, y_rows[i + 1]
            kind = "refuse" if i >= exit_row else "teal"
            p.edge(f"b{k}_e{i}", f"b{k}_r{i}", f"b{k}_r{i + 1}", [(cx, y0), (cx, y1)], "", kind)
        if jump is not None:
            a, b2 = jump
            assert isinstance(a, int) and isinstance(b2, int)
            gx = x + bw + bgap / 2
            ya, yb = y_rows[a] + row_h / 2, y_rows[b2] + row_h / 2
            p.edge(f"b{k}_j", f"b{k}_r{a}", f"b{k}_r{b2}", [(x + bw, ya), (gx, ya), (gx, yb), (x + bw, yb)], "", "refuse")  # fmt: skip
            if b2 < last:
                y0, y1 = y_rows[b2] + row_h, y_rows[b2 + 1]
                p.edge(f"b{k}_j2", f"b{k}_r{b2}", f"b{k}_r{b2 + 1}", [(cx, y0), (cx, y1)], "", "refuse")
        p.box(f"b{k}_says", x, says_y, bw, 80, "", [str(blk["says"])], "teal", size=11, body_size=11)
        p.box(f"b{k}_note", x, note_y, bw, 56, "", [str(blk["note"])], "white", size=10, body_size=9)

    fw = (width - 60 - 20) / 2
    p.box("foot_never", 30, foot_y, fw, 96, "Never in any of the five", [
        "a phone number, a location, a carrier error string or a stack trace (02 §3, errors.py docstring);",
        "a model's judgement — every exit is a branch in tower_policy or the audit writer (rule 2);",
        "a fact about the line before the consent gate has passed (NOT_BOUND / NO_CONSENT come first).",
    ], "grey", size=11)  # fmt: skip
    p.box("foot_other", 30 + fw + 20, foot_y, fw, 96, "Not drawn", [
        "HTTP 401 at step 1 (no identity) is a transport error, not a refusal envelope;",
        "a bug anywhere (the policy raises) → bare MCP error \"internal error\", never a partial result (server.py:66);",
        "is_reachable never answers from stored state on the happy path — only on a timeout (D6).",
    ], "grey", size=11)  # fmt: skip
    return p


# =====================================================================================================================
# 10 — the watch profiles over a time axis
# =====================================================================================================================


def timeline(
    p: Page,
    pre: str,
    y: float,
    points: list[tuple[str, list[str], str]],
    ticks: list[str],
    windows: list[list[tuple[int | float, int | float, str, list[str], str, bool]]],
    *,
    x0: float = 290,
    bw: float = 240,
    gap: float = 12,
    box_h: float = 112,
    axis_end: float | None = None,
) -> tuple[float, list[float]]:
    """Points above an axis, windows (spanning point centres) below it. Returns (bottom y, centres)."""
    centres = [x0 + i * (bw + gap) + bw / 2 for i in range(len(points))]
    axis_y = y + box_h + 26
    end = axis_end if axis_end is not None else x0 + len(points) * (bw + gap)
    p.box(f"{pre}_axis", x0, axis_y, end - x0, 6, "", None, "axis")
    for i, (t, body, kind) in enumerate(points):
        bx = x0 + i * (bw + gap)
        p.box(f"{pre}_p{i}", bx, y, bw, box_h, t, body, kind, size=10, body_size=9)
        p.edge(f"{pre}_pe{i}", f"{pre}_p{i}", f"{pre}_axis", [(centres[i], y + box_h), (centres[i], axis_y)], "", "amber" if kind == "amber" else "grey")  # fmt: skip
        p._vertex(
            f"{pre}_t{i}", centres[i] - 60, axis_y + 8, 120, 18, f"<b>{esc(ticks[i])}</b>",
            "text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;whiteSpace=wrap;"
            "fontSize=10;fontColor=#1B2B45;spacing=4;",
        )  # fmt: skip
    wy = axis_y + 34
    for r, row in enumerate(windows):
        for j, (a, b, title, body, kind, dashed) in enumerate(row):
            xa = centres[int(a)] if isinstance(a, int) else a
            xb = centres[int(b)] if isinstance(b, int) else b
            p.box(f"{pre}_w{r}_{j}", xa, wy, xb - xa, 48, title, body, kind, dashed=dashed, size=10, body_size=9)
        wy += 58
    return wy, centres


def page_profiles() -> Page:
    W = 2140
    p = Page("10_watch_profiles", "10 Watch Profiles", W, 1700)
    titles(
        p,
        "Watch profiles over time — transplant and care (Alerts, 06 §2–§5; thresholds.yaml)",
        "Alexa+ is not on this page: every message is an SMS from Alerts (rule 5). “Continuously” means every observation "
        "in the window said false; one true resets the clock (windows.py:26). Not to scale; times in the line-holder's zone.",
        "Every evaluation re-reads the grant first (evaluate.py:72) and again just before a send (send.py:173). "
        "The policy says whether; the Watch says to whom (03 §5).",
    )

    # --- transplant -------------------------------------------------------------------------------------------------
    zt_index = len(p.cells)
    p.zone("zt", 30, 100, W - 60, 760, "Transplant — 20 min continuously unreachable, any hour; 5-min polls; escalation with acknowledgement", "amber")  # fmt: skip
    p.box("zt_l1", 50, 140, 220, 112, "Trigger → decision", [
        "carrier CloudEvent (seconds) or the 5-min poll (fallback) → evaluate() → decide() → deliver()",
        "runner.py:72 process_line",
    ], "grey", size=10)  # fmt: skip
    t_points = [
        ("before 0:00 · watch on", [
            "page: line-holder picks transplant + contacts (04 §9) · voice: watch_line(self, true) keeps them · Alerts /internal/watch (internal_api.py:53) subscribes",
            "baseline evaluation: last_state written, never an alert",
        ], "white"),
        ("0:00 · goes dark", [
            "reachability-disconnected event → process_line(trigger=event)",
            "unreachable_since = 0:00 (or the carrier's lastStatusTime, never before our last true)",
        ], "white"),
        ("0:10 · one true", [
            "poll or -data event: reachable = true",
            "unreachable_since cleared — the 20-min clock resets; nothing sent",
        ], "white"),
        ("0:15 · dark again", [
            "new spell: unreachable_since = 0:15",
            "the clock lives in Watches.last_state, so a cold Lambda can't forget it",
        ], "white"),
        ("0:35–0:40 · first SMS", [
            "first evaluation ≥ 20 min dark → UNREACHABLE (windows.py:56); polls every 5 min, so up to 5 min late (D11)",
            "deliver(): re-resolve → rate claim → audit changed → line-holder + escalation[0], by their own alias; no active grant → skipped, next at once (escalation.py:86)",
        ], "amber"),
        ("0:50–0:55 · escalation[1]", [
            "no ack after ESCALATE_NEXT 15 min → tick (every poll + rate(5 min) schedule; escalation.py:193)",
            "walks the snapshot parked with the alert (remaining, 06 §11.3), re-checking each step's grant → audit changed → SMS",
        ], "amber"),
        ("within 6 h · repeat", [
            "same reason for the same line and watcher → audited suppressed · UNREACHABLE, not sent (windows.py:75 + atomic claim in AlertsState)",
        ], "white"),
    ]  # fmt: skip
    t_windows: list[list[tuple[int | float, int | float, str, list[str], str, bool]]] = [
        [
            (1, 2, "10 min dark — reset", ["one true clears the clock"], "grey", True),
            (3, 4, "≥ 20 min continuous", ["UNREACHABLE_ALERT.transplant"], "amber", False),
            (4, 5, "15 min, no ack", ["ESCALATE_NEXT"], "amber", False),
        ],
        [(4, 6, "RATE_LIMIT 6 h — one alert per line, per reason, per watcher", ["repeats go to the audit, not the phone"], "grey", True)],  # fmt: skip
    ]
    wy, tc = timeline(p, "tr", 140, t_points, ["−", "0:00", "0:10", "0:15", "0:35", "0:50", "< 6:35"], t_windows)
    p.text("tr_bh", 50, wy + 2, 900, 22, "<b>Branches inside the 15-min window (as built)</b>", size=11, color="#1B2B45")
    bw4 = (W - 100 - 3 * 14) / 4
    branches = [
        ("Ack accepted", [
            "watcher replies OK or CANCEL (SNS inbound → handler.py → escalation.py:175 handle_reply)",
            "audit ok · OK; chain cleared; escalation[1] is never texted",
        ], "teal"),
        ("ACK_IGNORED_SWAPPED_LINE", [
            "the reply comes from a line SIM-swapped < 24 h ago (ACK_DISTRUST; escalation.py:161; a carrier error counts as swapped)",
            "audited; chain continues; the next SMS adds “A reply from the affected line was ignored.” (templates.py:15) — the watcher's, or on the line-holder's own chain the next contact's",
        ], "exit"),
        ("Revoked before send → SUPPRESSED_REVOKED", [
            "line-holder revokes on the binding page; deliver() (send.py:174) or the tick (escalation.py:118) re-reads the grant",
            "audit suppressed · SUPPRESSED_REVOKED; nothing sent; no rate claim; chain cleared",
        ], "exit"),
        ("Who sets the profile and the chain? (built: D8, D9)", [
            "the line-holder on the binding page /me (04 §9; watch_settings.py:26): profile + up to 3 watch grantees in order on their own Watch; ack on all but the last.",
            "\"watch my line\" keeps them (watch_line.py:121); revoking a contact drops them from the chain (watches.py:83)",
        ], "teal"),
    ]  # fmt: skip
    for i, (t, body, kind) in enumerate(branches):
        p.box(f"tr_b{i}", 50 + i * (bw4 + 14), wy + 28, bw4, 92, t, body, kind, dashed=kind == "ghost", size=10)
    t_bottom = wy + 28 + 92 + 20
    _resize(p, zt_index, "zt", t_bottom - 100)

    # --- care ---------------------------------------------------------------------------------------------------------
    cy = t_bottom + 30
    zc_index = len(p.cells)
    p.zone("zc", 30, cy, W - 60, 600, "Care — 4 h continuously unreachable, released only 08:00–22:00; 30-min polls + 08:00 daily", "slate")  # fmt: skip
    p.box("zc_l1", 50, cy + 40, 220, 112, "Trigger → decision", [
        "events as above; polls rate(30 minutes) all day + cron 08:00 (scheduler/main.tf); the handler applies the window",
        "windows.py:51 is_daytime",
    ], "grey", size=10)  # fmt: skip
    c_points = [
        ("16:00 · watch on", [
            "\"Alexa, watch mom's line\" → watch_line(alias) → profile care (the default for an alias; watch_line.py:62)",
            "subscribe sim-swap + reachability; baseline",
        ], "white"),
        ("18:30 · goes dark", [
            "event or poll: unreachable_since = 18:30",
            "any true from here on would reset the clock",
        ], "white"),
        ("22:30 · 4 h reached, night", [
            "unreachable_due() is false outside 08:00–22:00 (windows.py:70)",
            "held: no SMS, no audit row",
        ], "white"),
        ("22:30–08:00 · polls", [
            "every 30 min: still dark, still held",
            "dark time at night still counts toward the 4 h",
        ], "white"),
        ("08:00 · first SMS", [
            "first daytime evaluation releases UNREACHABLE: SMS to the watcher + line-holder",
            "“Mom's phone has been off the network since yesterday at 6:30 - that's the network, nothing more.”",
        ], "amber"),
        ("08:00 · daily check", [
            "SIM swap + call forwarding for self and care profiles",
            "fraud alerts have no quiet hours: a swap at 03:00 is texted at once",
        ], "white"),
        ("until 14:00 · repeat", [
            "another UNREACHABLE within 6 h → audited suppressed, not sent",
            "once reported, a spell is not re-sent (last_alert_at ≥ unreachable_since)",
        ], "white"),
    ]  # fmt: skip
    x0, bw, gap = 290.0, 240.0, 12.0
    cen = [x0 + i * (bw + gap) + bw / 2 for i in range(len(c_points))]
    x22 = cen[2] - 50
    c_windows: list[list[tuple[int | float, int | float, str, list[str], str, bool]]] = [
        [
            (x0, x22, "daytime 08:00–22:00", ["reachability alerts released"], "teal", False),
            (x22, cen[4], "night 22:00–08:00 — held", ["dark time still counts"], "grey", True),
            (cen[4], x0 + 7 * (bw + gap), "daytime", ["released"], "teal", False),
        ],
        [
            (1, 2, "≥ 4 h continuous", ["UNREACHABLE_ALERT.care"], "amber", False),
            (4, 6, "RATE_LIMIT 6 h", ["per line, per reason, per watcher"], "grey", True),
        ],
    ]
    wy2, _ = timeline(p, "ca", cy + 40, c_points, ["16:00", "18:30", "22:30", "night", "08:00", "08:00", "14:00"], c_windows)
    p.box("ca_self", 50, wy2 + 6, (W - 114) / 2, 80, "self profile (\"watch my line\")", [
        "SIM swap + call forwarding only, daily 08:00 + events; no reachability alert;",
        "Tower's stored-state path reads this Watch when it is ≤ 10 min old (02 §4).",
    ], "grey", size=10)  # fmt: skip
    p.box("ca_sms", 50 + (W - 114) / 2 + 14, wy2 + 6, (W - 114) / 2, 80, "Who is texted, every profile", [
        "escalation chain from step 0, plus the line-holder at Users.alert_phone (default: the bound line);",
        "after a SIM swap of the line: its backup phone instead, never the swapped number (send.py:115).",
    ], "grey", size=10)  # fmt: skip
    c_bottom = wy2 + 6 + 80 + 20
    _resize(p, zc_index, "zc", c_bottom - cy)
    p.h = int(c_bottom + 30)
    return p


def _resize(p: Page, index: int, cid: str, h: float) -> None:
    """Set a zone's height once its content is laid out (the zone is created first so it is drawn behind)."""
    cell = p.cells[index]
    assert cell.get("id") == cid
    g = cell.find("mxGeometry")
    assert g is not None
    g.set("height", f"{h:g}")
    x, y, w, _ = p.geo[cid]
    p.geo[cid] = (x, y, w, h)


def build(page: Page) -> str:
    mx = ET.Element("mxfile", host="app.diagrams.net", agent="Ask the Tower design generator", type="device")
    mx.append(page.element())
    ET.indent(mx, space="  ")
    return ET.tostring(mx, encoding="unicode") + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if a file on disk differs from the generator")
    args = ap.parse_args()
    bad = 0
    for out, make in ((OUT09, page_refusals), (OUT10, page_profiles)):
        text = build(make())
        if args.check:
            if not out.exists() or out.read_text("utf-8") != text:
                print(f"{out.name} is stale: run gen_flows.py")
                bad += 1
        else:
            out.write_text(text, "utf-8")
            print(f"wrote {out.relative_to(ROOT)}")
        issues = checker.check(out)
        for i in issues:
            print("  " + i)
        print(f"{out.name}: check {len(issues)} issue(s)")
        bad += len(issues)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
