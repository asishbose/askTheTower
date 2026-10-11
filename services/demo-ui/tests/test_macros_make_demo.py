"""Doc 11 §5, §11 "Macros = `make demo`": the four buttons, pressed in the UI (over ASGI) against the in-process
mock and Tower with the scripted agent, give exactly the tool calls and reason codes of `tests/e2e/golden/*`, the
same golden set `make demo` is checked against; every earlier story is replayed first on the one timeline; every
`Say` badge is green. Along the way (§8.3, §8.5): every SSE frame is free of numbers, health words and keys, the
UI's mock traffic is `/_admin` by `ref` only in both directions, and the audit is read only as each line's owner.

Reset here is `Control.reset` (scenario reload, line refs, Asish's watch settings, Tower's clock): the UI's real
Reset is `seed.reset` over sync clients to the running services, which cannot be served in-process; it is the
`make seed` code `make showcase-ui` exercises (unrun here, see the build log).
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from demo_ui import feed as feed_mod
from demo_ui import runner as runner_mod
from demo_ui.app import create_app
from demo_ui.config import Settings
from demo_ui.deps import Deps, build_deps
from demo_ui.feed import Frame
from mock_carrier.testing import BASE
from ref_client.demo import STORIES, Control, StepReport
from ref_client.transcript import compare
from tower_audit import list_for_line
from tower_consent import Store, list_lines

from tests.helpers.transcripts import GOLDEN, load
from tests.privacy.patterns import AWS_KEY_ID, BEARER, HEALTH_WORDS, phone_hits

from .fakes import Recorder
from .refstack import Wire, load_ref_conftest

pytestmark = pytest.mark.integration

_ref = load_ref_conftest()
ref_stack = _ref.ref_stack  # the fixture, re-exported (its `store` is this service's moto store)
HX = {"HX-Request": "true"}
FIRE_PATH = re.compile(r"^/_admin/lines/line:[0-9a-f]{16}/events$")
STORY = {s.name: s for s in STORIES}


class UI:
    def __init__(self, deps: Deps, client: httpx.AsyncClient, wire: Wire, frames: list[Frame]) -> None:
        self.deps, self.client, self.wire, self.frames = deps, client, wire, frames
        self.reports: list[StepReport] = []
        self.audit_reads: list[tuple[str, str]] = []

    async def press(self, name: str) -> Any:
        self.reports.clear()
        r = await self.client.post(f"/macro/{name}", headers=HX)
        assert r.status_code == 200 and "started" in r.text, r.text
        task = self.deps.runner.task
        assert task is not None
        return await task


@pytest.fixture
async def ui(ref_stack: Any, store: Store, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[UI]:
    wire = Wire(ref_stack.control.mock)  # every mock call the UI makes, request and response
    fake = Recorder()  # binding page and Alerts: not part of this check (the ref stack has neither)
    settings = Settings(
        tower_url=f"{_ref.TOWER_URL}/mcp",
        tower_bearer=_ref.LOCAL_BEARER,
        mock_url=BASE,
        binding_url="http://binding.test",
        alerts_url="http://alerts.test",
        alerts_bearer="ab",
    )
    deps = build_deps(
        settings,
        transports={"mock": wire, "binding": fake.binding(), "alerts": fake.alerts()},
        store=store,
        tower_factory=_ref.http_client_factory(ref_stack.tower_app),
        mode="scripted",
    )
    seams = ref_stack.control
    control = Control(
        mock=httpx.AsyncClient(transport=wire, base_url=BASE),
        grants=seams.grants,
        settings=seams.settings,
        on_reset=seams.on_reset,
        on_advance=seams.on_advance,
    )
    deps.runner.control = lambda: control
    deps.runner.reset = control.reset
    app = create_app(deps)
    q = deps.feed.subscribe()
    frames: list[Frame] = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ui.test") as c:
        harness = UI(deps, c, wire, frames)
        real_run_demo = runner_mod.run_demo

        async def tee(*args: Any, on_step: Any, **kw: Any) -> Any:
            def both(r: StepReport) -> None:
                harness.reports.append(r)
                on_step(r)

            return await real_run_demo(*args, on_step=both, **kw)

        def spy(s: Store, line_id: str, viewer: str) -> Any:
            harness.audit_reads.append((line_id, viewer))
            return list_for_line(s, line_id, viewer)

        monkeypatch.setattr(runner_mod, "run_demo", tee)
        monkeypatch.setattr(feed_mod, "list_for_line", spy)
        try:
            yield harness
        finally:
            while not q.empty():
                frames.append(q.get_nowait())
            await control.mock.aclose()
            await deps.aclose()


def transcript_of(story: str, reports: list[StepReport]) -> dict[str, Any]:
    return {
        "story": story,
        "steps": [r.turn.dump() for r in reports if r.story == story and r.kind == "say" and r.turn],
    }


def drain(ui: UI) -> list[Frame]:
    q = next(iter(ui.deps.feed.subscribers))
    while not q.empty():
        ui.frames.append(q.get_nowait())
    return ui.frames


def assert_clean(text: str, where: str) -> None:
    assert phone_hits(text) == [], where
    assert not HEALTH_WORDS.search(re.sub(r"<[^>]*>", " ", text)), where  # visible words, not SVG attributes
    assert not AWS_KEY_ID.search(text) and not BEARER.search(text), where
    assert _ref.LOCAL_BEARER not in text, where


async def test_each_macro_is_the_make_demo_sequence(ui: UI, store: Store) -> None:
    golden = {p.stem: load(p) for p in GOLDEN.glob("*.json")}
    for name in ("moment-1", "moment-2", "moment-3", "transplant"):  # in order, on one stack: Reset each time
        result = await ui.press(name)
        assert result.error is None and result.failed == 0, (name, result)
        stories = [s.name for s in STORIES][: [s.name for s in STORIES].index(name) + 1]
        assert [r.story for r in ui.reports] == [s for s in stories for _ in STORY[s].steps]
        assert all(r.replay == (r.story != name) for r in ui.reports), name
        for story in stories:  # the replayed stories too: one timeline, the same codes
            assert compare(golden[story], transcript_of(story, ui.reports)) == [], (name, story)
        says = [r for r in ui.reports if r.kind == "say"]
        assert all(r.ok and r.actual == r.expected for r in says), name

        frames = drain(ui)
        turns = [f.html for f in frames if f.event == "turn"][-len(ui.reports) :]
        assert sum(h.count('class="badge pass"') for h in turns) == len(says), name
        assert not any("FAIL" in h for h in turns), name
        await ui.deps.feed.tick()  # what the panes show after the macro, as the poller would push it
        for f in drain(ui):
            assert_clean(f.sse(), f"{name}: SSE {f.event}")

    assert "NO_CONSENT" in ui.deps.feed.last["audit"]  # moment 3's refusal, in Mom's own log


async def test_the_mock_sees_admin_by_ref_only_and_answers_without_numbers(ui: UI) -> None:
    await ui.press("moment-3")
    await ui.press("transplant")
    await ui.deps.feed.tick()
    log = ui.wire.log
    assert log and all(x.url.startswith(f"{BASE}/_admin/") for x in log)  # never a CAMARA path (§8.2)
    fires = [x for x in log if x.url.endswith("/events")]
    assert {httpx.URL(x.url).path for x in fires} and all(
        FIRE_PATH.match(httpx.URL(x.url).path) for x in fires
    )
    # moment 3: Mom's swap twice; transplant replays those two, then Asish unreachable and reachable again
    assert len(fires) == 2 + 2 + 2
    states = [x for x in log if httpx.URL(x.url).path == "/_admin/state"]
    assert states and all(httpx.URL(x.url).params["view"] == "refs" for x in states)
    for x in log:  # what the UI sent and what it read back
        assert phone_hits(x.url + x.body) == [], x.text()
        assert phone_hits(x.response) == [], x.text()
        assert "msisdn" not in x.response, x.text()


async def test_the_audit_feed_reads_each_line_only_as_its_owner(ui: UI, store: Store) -> None:
    await ui.press("moment-3")
    ui.audit_reads.clear()
    await ui.deps.feed.tick()
    owners = {ln.line_id: user for user in ("user-asish", "user-mom") for ln in list_lines(store, user)}
    assert ui.audit_reads and sorted(set(ui.audit_reads)) == sorted(owners.items())
    audit = ui.deps.feed.last["audit"]
    assert "Asish" in audit and "Mom" in audit
    assert json.dumps(ui.audit_reads).count("user-asish") == 1  # Asish reads his line, never Mom's
