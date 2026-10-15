"""admin-open / admin-close stopping at their plan gate ends `confirm`, not `fail` (a pause for `deploy`)."""

from __future__ import annotations

import pytest
from demo_ui.ops.runner import PLAN_GATE
from demo_ui.ops.transcript import END

pytestmark = pytest.mark.unit


def test_the_gate_line_is_recognised_and_nothing_else() -> None:
    line = (
        "admin-open: the plan destroys 3 resource(s) (changing demo_admin_cidrs replaces DEMO_ADMIN_BEARER and "
        "rolls the mock task): read artifacts/terraform-plan.txt, then `make deploy`"
    )
    assert PLAN_GATE.match(line)
    assert PLAN_GATE.match("admin-close: the plan destroys 1 resource(s) ...")
    assert not PLAN_GATE.match("make: *** [mk/aws.mk:82: admin-open] Error 3")
    assert not PLAN_GATE.match("deploy: the plan destroys 3 resource(s)")


def test_a_confirm_end_line_reads_back() -> None:
    m = END.match("== 2026-10-15T15:30:00Z end confirm exit 2 79.3 s")
    assert m and m["state"] == "confirm"
