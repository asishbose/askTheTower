"""D18 (code-vs-docs.md): asking about a line the caller holds no grant for is `NO_CONSENT`, not `NOT_BOUND`.

04 §5: an alias with no grant at all — unknown, another grantee's alias, or anything that is not an alias —
resolves to `grant="none"` with no `line_id`. The engine answers `NO_CONSENT`; `next_step` is `ask_consent`, so
no bind token is minted (the caller's own line is not what was asked about). With no `line_id` there is no line
to audit, and nothing is asked of the carrier. Every tool, bound caller or not.
"""

from __future__ import annotations

from typing import Any

import pytest
from mock_carrier.testing import ASISH
from tower_consent import tables

pytestmark = pytest.mark.integration

NO_CONSENT_SUMMARY = "Bob hasn't shared that with you."


def count(stack: Any, table: tables.Table) -> int:
    return len(stack.store.scan_all(table))


async def ask(stack: Any, tool: str, user: str, line: str, enable: bool | None = None) -> Any:
    if tool == "line_is_ok":
        return await stack.line_is_ok(user, line)
    if tool == "is_reachable":
        return await stack.is_reachable(user, line)
    return await stack.watch_line(user, line, enable)


CALLS = [
    ("line_is_ok", None),
    ("is_reachable", None),
    ("watch_line", True),
    ("watch_line", False),
    ("watch_line", None),
]


@pytest.mark.parametrize(("tool", "enable"), CALLS)
@pytest.mark.parametrize("user", ["user-asish", "user-nobody"])  # a bound caller, and one with no line at all
async def test_unknown_alias_is_no_consent_without_a_bind_token(
    stack: Any, tool: str, enable: bool | None, user: str
) -> None:
    tokens, audit, watches = (count(stack, t) for t in (tables.BIND_TOKENS, tables.AUDIT, tables.WATCHES))
    before = await stack.calls()

    r = await ask(stack, tool, user, "bob", enable)

    assert r.reason_codes == ["NO_CONSENT"]
    assert r.summary == NO_CONSENT_SUMMARY
    assert r.next_step.kind == "ask_consent" and r.next_step.url is None
    assert r.facts.model_dump(exclude_none=True, exclude_defaults=True) == {"line": "bob"}
    assert count(stack, tables.BIND_TOKENS) == tokens  # no bind token minted
    assert count(stack, tables.AUDIT) == audit  # no line_id, so no audit row
    assert count(stack, tables.WATCHES) == watches  # watch_line wrote nothing
    assert await stack.calls() == before  # nothing asked of the carrier


async def test_another_grantees_alias_is_no_consent(stack: Any) -> None:
    """Asish holds "mom"; Mom asking about "mom" is asking about a line she has no grant for."""
    tokens = count(stack, tables.BIND_TOKENS)
    r = await stack.line_is_ok("user-mom", "mom")
    assert r.reason_codes == ["NO_CONSENT"] and r.next_step.kind == "ask_consent"
    assert count(stack, tables.BIND_TOKENS) == tokens


async def test_number_shaped_line_is_no_consent_and_not_echoed(stack: Any) -> None:
    tokens = count(stack, tables.BIND_TOKENS)
    r = await stack.line_is_ok("user-asish", ASISH)
    assert r.reason_codes == ["NO_CONSENT"] and r.next_step.kind == "ask_consent"
    assert r.facts.line == "unknown"
    assert ASISH[1:] not in r.model_dump_json()
    assert count(stack, tables.BIND_TOKENS) == tokens


async def test_self_without_a_line_still_gets_the_bind_link(stack: Any) -> None:
    """The one NOT_BOUND path left: the caller's own line, unbound (04 §1)."""
    r = await stack.line_is_ok("user-nobody", "self")
    assert r.reason_codes == ["NOT_BOUND"] and r.next_step.kind == "bind_line"
    assert r.next_step.url is not None
