"""The three tool descriptions — copied verbatim from `docs/architecture/components/01-alexa-surface.md` §2.

They are the voice UX: Alexa+'s model picks a tool from these words. This module is the single source for
FastMCP registration (`server.py`) and for the reference client's corpus test (prompt 11).
`tests/test_descriptions.py` parses the YAML block in the doc and fails if code and doc drift.
"""

from __future__ import annotations

from typing import Final

LINE_IS_OK: Final = (
    "Check whether the user's phone line, or a line they have been granted "
    "access to, has been SIM-swapped recently or has call forwarding set. "
    "Use when the user asks if their line/phone/number is OK, safe, swapped, "
    "hijacked, forwarded, or why their phone lost signal. Returns facts only."
)

IS_REACHABLE: Final = (
    "Whether a phone line is currently attached to the mobile network. "
    "Use when the user asks if a phone is on, reachable, or has signal. "
    "Does not return location."
)

WATCH_LINE: Final = (
    "Turn alerts on or off for a line the user holds or has been granted. "
    "When on, the user is texted if the line is SIM-swapped or forwarded."
)

DESCRIPTIONS: Final[dict[str, str]] = {
    "line_is_ok": LINE_IS_OK,
    "is_reachable": IS_REACHABLE,
    "watch_line": WATCH_LINE,
}

# Argument descriptions, as written under `args:` in 01 §2.
ARGS: Final[dict[str, dict[str, str]]] = {
    "line_is_ok": {"line": '"self" | alias (e.g. "mom"); default "self"'},
    "is_reachable": {"line": '"self" | alias'},
    "watch_line": {"line": '"self" | alias', "enable": "boolean | null"},
}
