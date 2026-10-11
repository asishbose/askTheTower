"""`make showcase` (testing-and-showcase §4, steps 1–9): each step exits 0 against the running stack, in order.
Runs `scripts/showcase_order.py` one step at a time without pauses (`--fast`: no on-camera pacing)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from tests.e2e.helpers import ROOT, Stack

pytestmark = pytest.mark.e2e

ORDER = ["1", "2", "3", "4–6", "7", "8", "9"]


def test_order_is_section_4() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from showcase_order import steps  # type: ignore[import-not-found]

    plan = steps(pause=False, fast=True)
    assert [s.number for s in plan] == ORDER
    assert [s.label for s in plan][:3] == ["mock", "policy table", "binding"]
    assert plan[-1].make == ("demo",)


@pytest.mark.parametrize("number", ORDER)
def test_showcase_step_exits_0(stack: Stack, number: str) -> None:
    r = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "scripts" / "showcase_order.py"), "--fast", "--only", number],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=900,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert r.returncode == 0, f"step {number} exited {r.returncode}:\n{(r.stdout + r.stderr)[-4000:]}"
    if number == "4–6":
        assert "SMS to=" in r.stdout, "moment 3's phone buzz (Alerts' SMS log line) not shown"
