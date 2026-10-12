"""Doc 11 §2 "Presentation" Layout (1440×900 and up: one top bar, then the Stage) and T1 (prompt 23: the top bar
gains `Control room · Ops`; the Stage and panes 1–4 stay unchanged). A behaviour test (no pixels), so it stays in
`integration` while `test_visual.py` runs `nightly`. Found by the tester (prompt 23 step 3): with ENV=aws the pill
(`scripted · ENV=aws · admin off`) and its note no longer fitted beside the tabs, the health strip wrapped to a second
row, and the Stage and every pane moved down 32 px."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from . import test_served as served_mod
from .test_visual import READY, camera

pytestmark = pytest.mark.integration

serve = served_mod.serve
Served = served_mod.Served
needs_browser = served_mod.needs_browser
__all__ = ["camera", "serve"]  # fixtures, imported so pytest finds them here

ROWS = """() => {
  const items = [...document.querySelectorAll('header.top > *')].filter(e => e.getBoundingClientRect().width > 0);
  const tops = items.map(e => { const r = e.getBoundingClientRect(); return Math.round(r.top + r.height / 2); });
  return {spread: Math.max(...tops) - Math.min(...tops), stage: document.querySelector('.stage').getBoundingClientRect().top,
          right: Math.max(...items.map(e => e.getBoundingClientRect().right))};
}"""


@needs_browser
def test_the_top_bar_is_one_row_at_1440_in_every_env(serve: Callable[..., Served], camera: Any) -> None:
    seen = {}
    for env, kw in (("local", {}), ("aws", {"env": "aws", "mock_url": "", "alerts_url": ""})):
        served = serve(**kw)
        page = camera.page()
        page.goto(served.url)
        page.wait_for_function(READY, timeout=20_000)
        page.wait_for_selector(f'#pill [data-env="{env}"]')
        seen[env] = page.evaluate(ROWS)
        page.context.close()
    for env, m in seen.items():
        assert m["spread"] <= 4, (env, m)  # every item's centre on one line
        assert m["right"] <= 1440, (env, m)  # nothing pushed off the viewport instead
    assert seen["aws"]["stage"] == seen["local"]["stage"], (
        seen
    )  # the Stage does not move with the pill's length
