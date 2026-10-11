"""No audit, no answer (02 §6, e2e-wiring §8.5): a failed append refuses; the row is durable before return."""

from __future__ import annotations

from typing import Any

import pytest
from botocore.exceptions import ClientError
from fastmcp.exceptions import ToolError
from tower_audit.reader import query_rows

from .conftest import Stack

pytestmark = pytest.mark.integration


def _fail_audit(stack: Stack) -> None:
    def fail(**_: Any) -> None:
        raise ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "injected"}}, "TransactWriteItems"
        )

    stack.inject("TransactWriteItems", fail)


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("line_is_ok", {"line": "self"}),
        ("is_reachable", {"line": "mom"}),
        ("watch_line", {"line": "self", "enable": True}),
        ("watch_line", {"line": "mom"}),
    ],
)
async def test_audit_write_failed_refuses(stack: Stack, tool: str, args: dict[str, Any]) -> None:
    _fail_audit(stack)
    r = await getattr(stack, tool)("user-asish", **args)
    assert r.reason_codes == ["SERVICE_UNAVAILABLE"]
    assert r.summary == "Something on my side isn't available. Try again in a minute."
    facts = r.facts.model_dump(exclude_defaults=True)
    assert set(facts) == {"line"}  # nothing the carrier said leaks out unaudited
    assert query_rows(stack.store, stack.seed.asish_line) == []
    assert query_rows(stack.store, stack.seed.mom_line) == []


async def test_refused_consent_is_also_audit_first(stack: Stack) -> None:
    from tower_consent import revoke

    from .conftest import MOCK_START

    revoke(stack.store, stack.seed.mom_line, "user-asish", "watch", revoked_by="user-mom", now=MOCK_START)
    _fail_audit(stack)
    r = await stack.line_is_ok("user-asish", "mom")
    assert r.reason_codes == ["SERVICE_UNAVAILABLE"]


async def test_crash_between_audit_and_return_leaves_the_row_and_no_result(stack: Stack) -> None:
    seen: list[Any] = []

    def crash(row: Any) -> None:
        seen.append(row)
        raise SystemError("process died here")

    stack.deps.after_audit = crash
    with pytest.raises(ToolError, match="^internal error$"):
        await stack.line_is_ok("user-asish")
    rows = query_rows(stack.store, stack.seed.asish_line)
    assert len(rows) == 1 and rows[0] == seen[0]
    assert rows[0].tool == "line_is_ok" and rows[0].outcome == "ok"
    assert stack.results == []  # no result was produced
