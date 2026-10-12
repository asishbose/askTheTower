"""Doc 11 §2 "Presentation" and §11 "Presentation (prompt 21)": the five states at 1440×900, compared with
local baselines, plus the reduced-motion variant and the axe-core audit. Layer `nightly` (`make test-nightly`, or
`uv run pytest services/demo-ui/tests/test_visual.py -m nightly`): pixel baselines belong to the machine that wrote
them, so they are not part of `make test-integration`. Skipped when Chromium for Playwright is not installed.

The page is the real app under uvicorn (`test_served.serve`: fake mock / binding / Alerts transports, a moto store,
the fake story agent), so every frame, fragment and class comes from the shipped templates, CSS and `stage.js`.

Screenshots go to `artifacts/screenshots/demo-ui/<state>.png` (generated, gitignored) and are compared with
`services/demo-ui/tests/visual/baseline/<state>.png` (gitignored: local to the machine): at most 0.5 % of pixels may
differ, a pixel "differs" when any channel differs at all. A capture over the limit is taken and compared once more
(a stale raster tile under load), never with a wider limit. A missing baseline is written from this run and the test
skips saying so; set `DEMO_UI_UPDATE_BASELINES=1` to rewrite them after an intended visual change.

Determinism: the data is fixed (the fakes' mock state and clock, the fixture's audit rows at a fixed time, fixed
line keys); the only measured values on the page, the step timings (`.ms`), are set to "— ms" before the shot;
the shot is taken under `prefers-reduced-motion: reduce` (emulated once the page is quiet, after the state was
reached with motion on), after `document.fonts.ready`, network idle, the scene, every fragment and the end of every transient
Stage class, with CSS animations stopped, the caret hidden, focus and hover cleared. Baselines are tied to the renderer that
wrote them (Chromium build and system fonts): another machine may need `DEMO_UI_UPDATE_BASELINES=1`.

Every request the page makes, in every state, goes to 127.0.0.1.
"""

from __future__ import annotations

import base64
import os
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from botocore.exceptions import EndpointConnectionError
from demo_ui import feed as feed_mod
from ref_client.demo import STORIES

from . import test_served as served_mod
from .fakes import StoryAgent

pytestmark = pytest.mark.nightly

serve = served_mod.serve  # the served-app fixture, reused as is
Served = served_mod.Served
needs_browser = served_mod.needs_browser

ROOT = Path(__file__).resolve().parents[3]
BASELINE = Path(__file__).resolve().parent / "visual" / "baseline"
SHOTS = ROOT / "artifacts" / "screenshots" / "demo-ui"
VIEWPORT = {"width": 1440, "height": 900}
TOLERANCE = 0.005  # share of pixels that may differ (prompt 21)
UPDATE = os.environ.get("DEMO_UI_UPDATE_BASELINES") == "1"
WHY_WRITTEN = "DEMO_UI_UPDATE_BASELINES=1" if UPDATE else "no baseline to compare with"

MOMENT_1 = STORIES[0]
SWAP_QUESTION = "My phone just lost signal. Is my line OK?"  # Moment 1's SIM_SWAPPED_RECENT step
# the classes stage.js `pulse()` adds for a moment, on the scene ids it adds them to (the scene also has static
# children classed `tick` and `buzz`)
PULSES = {"rings": "pulse", "beam-sms": "go", "phone": "buzz", "ledger": "tick", "shield": "flash",
          "beacon": "flash", "beam-req": "go-you go-mom", "house-you": "speaking"}  # fmt: skip
TRANSIENT = ", ".join(f"#stage #{i}.{c}" for i, cs in PULSES.items() for c in cs.split())

READY = """() => !!document.querySelector('#stage svg')
  && !!document.querySelector('#pill [data-env]')
  && document.querySelectorAll('.skeleton').length === 0
  && [...document.querySelectorAll('[hx-get^="/fragment/"]')]
    .filter((d) => !d.closest('#tab-ops'))  // the Ops tab's fragments load only when it is first shown (T1)
    .every((d) => d.children.length > 0)"""
QUIET = f"""() => !document.querySelector('{TRANSIENT}')
  && !document.querySelector('.stage-float *')
  && !document.body.classList.contains('running')"""
SHOW = """(sel) => { const top = document.querySelector('header.top').getBoundingClientRect().height;
  window.scrollTo(0, Math.round(document.querySelector(sel).getBoundingClientRect().top + window.scrollY - top - 12)); }"""
GEOMETRY = """() => ({ scrollY: window.scrollY, height: document.documentElement.scrollHeight,
  fonts: document.fonts.status, panes: [...document.querySelectorAll('.pane')].map((p) => {
    const r = p.getBoundingClientRect(); return [Math.round(r.top * 100) / 100, Math.round(r.height * 100) / 100]; }) })"""
# the same geometry on 8 consecutive animation frames (at most ~5 s), then return it
STEADY = (
    "async () => { const g = "
    + GEOMETRY
    + "; const frame = () => new Promise((r) => requestAnimationFrame(r));"
    " let last = JSON.stringify(g()), same = 0; for (let i = 0; i < 300 && same < 8; i++) { await frame();"
    " const now = JSON.stringify(g()); same = now === last ? same + 1 : 0; last = now; } return g(); }"
)
BUSY = """(sel) => ({ transient: [...document.querySelectorAll(sel)].map((e) => `${e.id}.${e.getAttribute('class')}`),
  floats: document.querySelector('.stage-float')?.innerHTML.slice(0, 200), body: document.body.className })"""
FONT = """async () => { await document.fonts.ready;
  return document.fonts.check('600 20px "Space Grotesk"'); }"""
SETTLE = """() => { document.querySelectorAll('.ms').forEach((e) => { e.textContent = '— ms'; });
  if (document.activeElement) document.activeElement.blur(); window.scrollTo(0, 0); }"""
DIFF = """async ([a, b]) => {
  const load = (src) => new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = bad; i.src = src; });
  const [x, y] = await Promise.all([load(a), load(b)]);
  if (x.width !== y.width || x.height !== y.height) return { size: [x.width, x.height, y.width, y.height], diff: -1, total: 0 };
  const px = (img) => { const c = document.createElement('canvas'); c.width = img.width; c.height = img.height;
    const g = c.getContext('2d', { willReadFrequently: true }); g.drawImage(img, 0, 0); return g.getImageData(0, 0, c.width, c.height).data; };
  const p = px(x), q = px(y); let diff = 0;
  for (let k = 0; k < p.length; k += 4) {
    if (p[k] !== q[k] || p[k + 1] !== q[k + 1] || p[k + 2] !== q[k + 2] || p[k + 3] !== q[k + 3]) diff++;
  }
  return { size: [x.width, x.height], diff, total: p.length / 4 };
}"""


def _agents_with(agent: StoryAgent) -> Callable[[], Any]:
    @asynccontextmanager
    async def agents() -> AsyncIterator[Callable[[str], Any]]:
        async def agent_for(user_id: str) -> StoryAgent:
            return agent

        yield agent_for

    return agents


def _dynamo_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: Any, **__: Any) -> Any:
        raise EndpointConnectionError(endpoint_url="http://localhost:8000")

    monkeypatch.setattr(feed_mod, "list_for_line", refuse)


class Camera:
    """One Chromium for the module's shots; records every request URL from every page it opens."""

    def __init__(self, browser: Any) -> None:
        self.browser = browser
        self.requests: list[str] = []
        self.written: list[str] = []
        self.compared: dict[str, float] = {}
        self.geometry: dict[str, Any] = {}

    def page(self, *, reduced: bool = False) -> Any:
        ctx = self.browser.new_context(
            viewport=VIEWPORT, device_scale_factor=1, reduced_motion="reduce" if reduced else "no-preference"
        )
        page = ctx.new_page()
        page.on("request", lambda r: self.requests.append(r.url))
        return page

    def shoot(self, page: Any, name: str, *, show: str | None = None) -> bytes:
        """`show`: a pane below the fold that the state is about; scrolled to just under the sticky top bar."""
        page.wait_for_function(READY, timeout=20_000)
        assert page.evaluate(FONT), "Space Grotesk did not load from /static/fonts"
        page.mouse.move(0, 0)
        page.evaluate(SETTLE)
        if show:
            page.evaluate(SHOW, show)
        try:
            page.wait_for_function(QUIET, timeout=10_000)
        except Exception as e:  # noqa: BLE001 - re-raised with what kept the page busy
            busy = page.evaluate(BUSY, TRANSIENT)
            raise AssertionError(f"{name}: the page never settled: {busy}") from e
        # No looping animation (the paused-feed pulse) in the shot. Only once quiet: a chip floating when reduced
        # motion turns on never gets its `animationend`.
        page.emulate_media(reduced_motion="reduce")
        png = self._steady_shot(page, name)
        SHOTS.mkdir(parents=True, exist_ok=True)
        (SHOTS / f"{name}.png").write_bytes(png)
        return png

    def _steady_shot(self, page: Any, name: str) -> bytes:
        """Under load a raster tile can be captured stale after a scroll or a late frame: wait until the layout
        holds still for several frames, then shoot until two consecutive captures are byte-identical (what
        Playwright's own `toHaveScreenshot` does)."""
        last = self._capture(page)
        for _ in range(6):
            png = self._capture(page)
            self.geometry[name] = page.evaluate(GEOMETRY)
            if png == last:
                return png
            last = png
        raise AssertionError(f"{name}: no two consecutive screenshots agreed; {self.geometry[name]}")

    @staticmethod
    def _capture(page: Any) -> bytes:
        """One screenshot after the fonts, the network and the layout are all still."""
        page.evaluate("async () => { await document.fonts.ready; }")
        page.wait_for_load_state("networkidle")
        page.evaluate(STEADY)
        png: bytes = page.screenshot(animations="disabled", caret="hide", scale="css")
        return png

    def check(self, page: Any, name: str, *, show: str | None = None, baseline: str | None = None) -> None:
        """Shoot `name` and compare it with `baseline` (default: its own). Over the limit, shoot and compare once
        more: under load a capture can still carry a stale band; the second one decides, at the same limit."""
        base = baseline or name
        share = self.compare(base, self.shoot(page, name, show=show))
        if share is not None and share > TOLERANCE:
            share = self.compare(base, self.shoot(page, name, show=show))
        if share is not None:
            assert share <= TOLERANCE, (
                f"{name}: {share:.3%} of pixels differ from baseline {base} (limit 0.5 %) on two captures; "
                f"{self.geometry.get(name)}"
            )

    def compare(self, name: str, png: bytes) -> float | None:
        """The share of pixels that differ from baseline `name`; `None` when the baseline was (re)written."""
        base = BASELINE / f"{name}.png"
        if UPDATE or not base.exists():
            BASELINE.mkdir(parents=True, exist_ok=True)
            base.write_bytes(png)
            self.written.append(name)
            return None
        r = self.diff(base.read_bytes(), png)
        assert r["diff"] >= 0, f"{name}: size changed {r['size']}"
        share = float(r["diff"] / r["total"])
        self.compared[name] = share
        return share

    def diff(self, a: bytes, b: bytes) -> dict[str, Any]:
        ctx = self.browser.new_context()
        page = ctx.new_page()
        urls = [f"data:image/png;base64,{base64.b64encode(x).decode()}" for x in (a, b)]
        result: dict[str, Any] = page.evaluate(DIFF, urls)
        ctx.close()
        return result


@pytest.fixture
def camera() -> Iterator[Camera]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield Camera(browser)
        browser.close()


def _run_moment_1(page: Any) -> None:
    from playwright.sync_api import expect

    page.wait_for_function(READY, timeout=20_000)
    page.get_by_role("button", name="Moment 1").click()
    expect(
        page.locator('#run-status .status[data-level="pass"], #run-status .status[data-level="fail"]')
    ).to_have_count(1, timeout=20_000)
    expect(page.locator("#log article.step")).to_have_count(len(MOMENT_1.steps))


def _assert_local_only(requests: list[str]) -> None:
    off = sorted({u for u in requests if not u.startswith("data:") and urlsplit(u).hostname != "127.0.0.1"})
    assert off == [], f"requests leaving 127.0.0.1: {off}"
    assert requests  # the log saw the page load


@needs_browser
def test_the_five_states_match_their_baselines_and_stay_on_127_0_0_1(
    serve: Callable[..., Served], camera: Camera, monkeypatch: pytest.MonkeyPatch
) -> None:
    from playwright.sync_api import expect

    # (3) Bedrock unavailable: the scripted pill, after Moment 1 pressed in the page
    served = serve()
    page = camera.page()
    page.goto(served.url)
    _run_moment_1(page)
    expect(page.locator("#pill")).to_contain_text("scripted")
    expect(page.locator("#log .rc-SIM_SWAPPED_RECENT .chip").first).to_be_visible()
    expect(page.locator("#log .spoken").first).to_contain_text("Your SIM was moved")  # never "as it was" here
    camera.check(page, "moment-1-scripted")
    page.context.close()

    # (5) a failed macro step: Moment 1 with the SIM-swap question answered CARRIER_ERROR
    served = serve()
    served.deps.runner.agents = _agents_with(StoryAgent(wrong=SWAP_QUESTION))
    page = camera.page()
    page.goto(served.url)
    _run_moment_1(page)
    expect(page.locator("#stepper li.fail")).to_contain_text("see transcript")
    expect(page.locator("#stepper li.stopped, #stepper li.fail").first).to_be_visible()
    # exactly one failed badge, on the step that failed; under load the browser can lag the run's status frame
    expect(page.locator("#log article.step.bad .badge.fail")).to_have_count(1, timeout=20_000)
    expect(page.locator("#log .badge.fail")).to_have_count(1, timeout=20_000)
    camera.check(page, "failed-step")
    page.context.close()

    # (2) ENV != local: carrier pane and story tiles hatched with the reason
    served = serve(env="aws", mock_url="", alerts_url="")
    page = camera.page()
    page.goto(served.url)
    page.wait_for_function(READY, timeout=20_000)
    expect(page.locator('#pill [data-env="aws"]')).to_have_count(1)
    expect(page.locator("#audit")).to_contain_text("SUPPRESSED_REVOKED")
    camera.check(page, "env-not-local")
    page.context.close()

    # (4) feed paused: DynamoDB refuses, the SMS lane goes on
    served = serve()
    with monkeypatch.context() as m:
        _dynamo_down(m)
        page = camera.page()
        page.goto(served.url)
        expect(page.locator("#audit")).to_contain_text("feed paused: DynamoDB EndpointConnectionError")
        expect(page.locator("#sms")).to_contain_text("watcher")
        camera.check(page, "feed-paused", show="#pane-feed")
        page.context.close()

    # (1) stack down: every neighbour answers 503 and DynamoDB refuses
    served = serve()
    served.rec.status = 503
    with monkeypatch.context() as m:
        _dynamo_down(m)
        page = camera.page()
        page.goto(served.url)
        expect(page.locator("body.stack-down")).to_have_count(1, timeout=20_000)
        expect(page.locator("#pane-carrier .waiting")).to_be_visible()
        expect(page.locator("#pane-carrier .waiting")).to_contain_text("make up")
        camera.check(page, "stack-down")
        page.context.close()

    _assert_local_only(camera.requests)
    if camera.written:
        pytest.skip(
            f"{WHY_WRITTEN}: wrote {', '.join(camera.written)} to {BASELINE.relative_to(ROOT)} "
            "(gitignored, local to this machine); look at them, then re-run to compare"
        )


OPS_LOADED = """() => ['#ops-header .ops-head', '#ops-checklist .ops-headline', '#ops-teardown .td-what', '#ops-run .ops-bar']
  .every((sel) => !!document.querySelector(sel))"""


def _ops_rig(served: Served, root: Path, snapshot: Any) -> None:
    """Deterministic Ops tab: no GET leaves the process (every one answers 200), the gate's config-check passes,
    and the quiet probes answer `snapshot` (doc 11 §11: "fixture files and injected probes")."""
    import httpx
    from demo_ui.ops.probes import ProbeResult

    quiet_http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    served.deps.aws_status.http = quiet_http
    served.deps.stack.http = quiet_http

    async def passes() -> Any:
        return ProbeResult(True)

    async def snap(**_: Any) -> Any:
        return snapshot

    served.deps.gate.config_check = passes
    served.deps.quiet.snapshot = snap  # type: ignore[method-assign]


def _checkout(root: Path, *, dotenv: bool) -> Path:
    (root / "mk").mkdir(parents=True, exist_ok=True)
    (root / "Makefile").write_text("all:\n")
    if dotenv:
        (root / ".env").write_text("AWS_REGION=us-east-1\n")
    return root


@needs_browser
def test_the_ops_tab_in_three_states(serve: Callable[..., Served], camera: Camera, tmp_path: Path) -> None:
    """Doc 11 §11 (prompt 23): the Ops tab with the gate off, with nothing deployed, and deployed; `/#ops` opens it
    directly and the Control room stays hidden; every request stays on 127.0.0.1."""
    from demo_ui.ops.envload import Facts
    from demo_ui.ops.probes import ProbeResult, ProbeSnapshot, TfState
    from playwright.sync_api import expect

    from .opsfix import BINDING, SERVICES, WEB_CHAT, everything

    facts = ProbeResult(True, Facts("", "us-east-1", True, 2))
    cases = [
        ("ops-gate-off", _checkout(tmp_path / "off", dotenv=True), "", ProbeSnapshot()),
        ("ops-nothing-deployed", _checkout(tmp_path / "fresh", dotenv=True), "1",
         ProbeSnapshot(facts=facts, sts=ProbeResult(True, "123456789012"), ecr=ProbeResult(True, ()),
                       tf=ProbeResult(True, TfState(False, {})))),
        ("ops-deployed", _checkout(tmp_path / "deployed", dotenv=True), "1",
         ProbeSnapshot(facts=facts, sts=ProbeResult(True, "123456789012"),
                       ecr=ProbeResult(True, tuple(f"att-dev/{s}" for s in SERVICES)),
                       tf=ProbeResult(True, TfState(True, {"binding_url": BINDING, "web_chat_url": WEB_CHAT})),
                       checkip=ProbeResult(True, "8.8.8.8/32"))),
    ]  # fmt: skip
    everything(tmp_path / "deployed")
    for name, root, flag, snapshot in cases:
        served = serve(checkout=root, aws_ops=flag)
        _ops_rig(served, root, snapshot)
        page = camera.page()
        page.goto(f"{served.url.rstrip('/')}/#ops")
        page.wait_for_function(OPS_LOADED, timeout=20_000)
        expect(page.locator("#panes")).to_be_hidden()
        expect(page.locator('#tab-btn-ops[aria-selected="true"]')).to_have_count(1)
        head = page.locator("#ops-header").inner_text()
        assert ("AWS runs: on" in head) == (flag == "1"), head
        camera.check(page, name)
        page.context.close()
    _assert_local_only(camera.requests)
    if camera.written:
        pytest.skip(f"{WHY_WRITTEN}: wrote {', '.join(camera.written)}; look at them, then re-run to compare")


TRACK = """() => { window.__added = []; const svg = document.querySelector('#stage svg');
  new MutationObserver((ms) => ms.forEach((m) => { const now = m.target.getAttribute('class') || '';
    const was = m.oldValue || ''; now.split(/\\s+/).filter((c) => c && !was.split(/\\s+/).includes(c))
      .forEach((c) => window.__added.push(`${m.target.id}.${c}`)); }))
  .observe(svg, { subtree: true, attributes: true, attributeFilter: ['class'], attributeOldValue: true });
  window.__floats = 0; new MutationObserver(() => { window.__floats += document.querySelectorAll('.stage-float *').length; })
  .observe(document.querySelector('.stage-float'), { childList: true, subtree: true }); }"""


@needs_browser
def test_reduced_motion_stops_the_animations_but_keeps_the_state(
    serve: Callable[..., Served], camera: Camera
) -> None:
    """`prefers-reduced-motion: reduce`: no transient Stage class is ever added and no chip floats, yet the state
    classes are applied (coral beacon after SIM_SWAPPED_RECENT, the "last answer" row, health dots, stepper), and
    the settled page is the same picture as with motion on."""
    from playwright.sync_api import expect

    served = serve()
    page = camera.page(reduced=True)
    page.goto(served.url)
    page.wait_for_function(READY, timeout=20_000)
    page.evaluate(TRACK)
    _run_moment_1(page)
    expect(page.locator("#stage #beacon.alert")).to_have_count(1)
    expect(page.locator(".stage-codes .rc-SIM_SWAPPED_RECENT")).to_have_count(1)
    expect(page.locator('.health li[data-svc="tower"].up')).to_have_count(1)
    expect(page.locator("#stepper li.pass")).to_have_count(2)
    expect(page.locator("#stepper li.done")).to_have_count(1)
    transient = {f"{i}.{c}" for i, cs in PULSES.items() for c in cs.split()}
    added = page.evaluate("() => window.__added")
    assert [a for a in added if a in transient] == []
    assert page.evaluate("() => window.__floats") == 0
    styles = page.evaluate(
        "() => [...document.querySelectorAll('#stage *, .tile, .stepper li, .chip')].map((e) => getComputedStyle(e))"
        ".filter((s) => s.animationName !== 'none' || parseFloat(s.transitionDuration) > 0).length"
    )
    assert styles == 0
    # same settled picture as Moment 1 run with motion on (the baseline's run; only its shot is reduced)
    camera.check(page, "moment-1-reduced-motion", baseline="moment-1-scripted")
    page.context.close()
    _assert_local_only(camera.requests)
    if camera.written:
        pytest.skip(
            f"{WHY_WRITTEN}: wrote {', '.join(camera.written)} to {BASELINE.relative_to(ROOT)}; "
            "re-run to compare"
        )


AXE = os.environ.get("AXE_CORE_JS", "")
_NO_AXE = (
    None if AXE and Path(AXE).is_file() else "set AXE_CORE_JS to a local axe-core axe.min.js (no CDN, no npm)"
)
AXE_RUN = """async () => { const r = await axe.run(document, { runOnly: { type: 'tag',
  values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice'] }, resultTypes: ['violations'] });
  return { version: axe.version, passes: r.passes.length, incomplete: r.incomplete.map((v) => v.id), violations: r.violations.map((v) => ({ id: v.id, impact: v.impact,
    nodes: v.nodes.slice(0, 5).map((n) => `${n.target.join(' ')} :: ${n.failureSummary.split('\\n').slice(1).join(' ')}`) })) }; }"""


@needs_browser
@pytest.mark.skipif(_NO_AXE is not None, reason=_NO_AXE or "")
def test_axe_finds_no_wcag_aa_violation(
    serve: Callable[..., Served], camera: Camera, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Doc 11 §2 accessibility (Lighthouse/axe ≥ 95): axe-core, injected from a file on disk (an inline script, not
    a request), over the settled page after Moment 1, after a failed step, on ENV=aws and with the stack down."""
    found: dict[str, Any] = {}
    ran: dict[str, Any] = {}

    def audit(page: Any, name: str) -> None:
        page.wait_for_function(READY, timeout=20_000)
        page.wait_for_function(QUIET, timeout=10_000)
        page.add_script_tag(path=AXE)
        r = page.evaluate(AXE_RUN)
        found[name] = r["violations"]
        ran[name] = (r["version"], r["passes"], r["incomplete"])
        assert r["passes"] >= 10, (name, r)  # axe really ran over the page
        page.context.close()

    page = camera.page()
    page.goto(serve().url)
    _run_moment_1(page)
    audit(page, "moment-1")
    served = serve()
    served.deps.runner.agents = _agents_with(StoryAgent(wrong=SWAP_QUESTION))
    page = camera.page()
    page.goto(served.url)
    _run_moment_1(page)
    audit(page, "failed-step")
    page = camera.page()
    page.goto(serve(env="aws", mock_url="", alerts_url="").url)
    audit(page, "env-not-local")
    served = serve()
    served.rec.status = 503
    with monkeypatch.context() as m:
        _dynamo_down(m)
        page = camera.page()
        page.goto(served.url)
        page.wait_for_selector("body.stack-down", timeout=20_000)
        audit(page, "stack-down")
    _assert_local_only(camera.requests)
    assert {k: v for k, v in found.items() if v} == {}, ran
    print(f"axe {ran}")  # the counts for the report (pytest -s)
