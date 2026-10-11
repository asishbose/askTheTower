"""Doc 11 §2 stepper: "replayed steps fold into one node", in Chromium against the served app. A behaviour test
(no pixels), so it stays in `integration` while the screenshot tests in `test_visual.py` run `nightly`."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from . import test_served as served_mod
from .test_visual import _run_moment_1, camera

pytestmark = pytest.mark.integration

serve = served_mod.serve  # the served-app fixture, reused as is
Served = served_mod.Served
needs_browser = served_mod.needs_browser
__all__ = ["camera", "serve"]  # fixtures, imported so pytest finds them here


@needs_browser
def test_a_second_run_in_the_same_page_folds_its_replayed_steps(
    serve: Callable[..., Served], camera: Any
) -> None:
    """Doc 11 §2 stepper: "replayed steps fold into one node". Moment 1, then Moment 2 in the same page: Moment 2
    replays Moment 1's three steps, whose articles reuse the ids of the first run's (`#log` keeps both), so the
    stepper must read `replay` from the new frame, not from attributes htmx carries over from the old element."""
    from playwright.sync_api import expect

    served = serve()
    page = camera.page()
    page.goto(served.url)
    _run_moment_1(page)
    page.get_by_role("button", name="Moment 2").click()
    page.wait_for_function("() => document.body.classList.contains('running')", timeout=20_000)
    page.wait_for_function("() => !document.body.classList.contains('running')", timeout=20_000)
    expect(page.locator("#log article.step.replay")).to_have_count(3)  # the frames are right
    nodes = page.locator("#stepper li").evaluate_all(
        "ls => ls.map((l) => l.querySelector('.label').textContent)"
    )
    assert nodes[0] == "replayed 3 earlier steps", nodes
    assert not [n for n in nodes if n.startswith("moment-1#")], nodes
    page.context.close()
