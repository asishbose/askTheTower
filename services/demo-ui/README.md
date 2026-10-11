# demo-ui — the demo control room

One browser page for presenting and rehearsing the demo: the conversation, the carrier's state, the live alerts
and audit rows, and the binding QR code, side by side. Design: [`docs/architecture/components/11-demo-ui.md`](../../docs/architecture/components/11-demo-ui.md),
diagram [`11-demo-ui`](../../docs/architecture/diagrams/png/11-demo-ui.png).

**What it is.** FastAPI + HTMX, one static page, no build step (HTMX 2 and its SSE extension are vendored in
`src/demo_ui/static/`). The reference client is used as a library: the macro buttons run
`ref_client.demo.STORIES` through `run_demo(reset=False, on_step=…)`, the same code and steps as `make demo`,
with a pass/fail badge per step.

**What it is not.** Not part of the product and not on any path (e2e §3–§5): it owns no table, decides nothing
(outcome chips are Tower's `reason_codes`), makes no CAMARA or Gateway call, holds no key, never shows or handles a
phone number, and is **never deployed to AWS** (no ECR repository, no Terraform, `values-eks` `enabled: false`).
There is no `prompts/` folder: the UI has no agent role of its own.

| Pane | Does |
|---|---|
| 1 Conversation | macros Moment 1, 2, 3, Transplant (Reset → earlier stories replayed → this one, badges + timings); free text as Asish or Mom with Bedrock; environment pill (`scripted`/`bedrock` · `ENV`) |
| 2 Carrier controls | Reset (= `make seed`), clock +1/+12/+20 min, line events by the mock's opaque `ref`, faults; state by holder |
| 3 Live feed | SMS from Alerts' `GET /internal/sent` (role, template, body) and the last 50 audit rows read as each line's owner |
| 4 Binding | bind link as a QR code (drawn here with `segno`), grants, `resolve`, Mom's revoke / re-grant |

## Look

A night-time operations room over a town (doc 11 §2 "Presentation"): a top bar (wordmark, the mode pill, a
five-dot health strip, the run status), **the Stage** (your house, the tower with the audit ledger at its foot,
Mom's house with the consent shield, the phone that receives texts), then the four panes as glass cards.
Only the presentation is new: the routes, SSE events, feed sources, fragment names and their context variables,
`redact.py`, the `HX-Request` check, the token cookie, the poller and the runner are unchanged.

| Asset | What it is |
|---|---|
| `static/index.html` | the page, served raw (not Jinja); every HTMX target id is unchanged |
| `static/app.css` | the only stylesheet: tokens, cards, chips (`.rc-<CODE>` for every reason code), stepper, timeline, phone frame, the five states |
| `static/stage.js` | ES module: fetches `img/scene.svg` once, inlines it, then only toggles classes from the SSE frames and the fragments' `data-*` |
| `static/img/scene.svg`, `icons.svg`, `empty-state.svg` | vector art derived from `docs/img/hero.svg`; fragments reference the sprite with `<use>`, never inline geometry |
| `static/fonts/SpaceGrotesk-wght.woff2` + `LICENSE` | Space Grotesk 2.0.0 variable (300–700), SIL OFL 1.1, from the upstream repository `floriankarsten/space-grotesk` at commit `03507d0` (`fonts/woff2/SpaceGrotesk[wght].woff2`, sha256 `8e085aa4…a4b08c07`); title and pane names only, the UI text is `system-ui` |

**Colour has one meaning per hue:** cyan the request path, amber proactive and SMS, mint consent and OK, coral a
refusal or an alert (solid: the line changed or an alert failed; outline: Tower refused), slate grey withdrawn or
raw state (`SUPPRESSED_REVOKED` with a ⊘). Chip text is always Tower's reason code verbatim. The carrier pane's
state chips are neutral on purpose: the UI never calls a SIM change "recent", because that is policy.

**The Stage, driven by what the page already receives:** a `say` turn lights the cyan beam from your house (from
Mom's house for her free-text turns) and floats its reason codes up from the tower; a coral code turns the beacon
coral and keeps it so until the next answer; a carrier state change (or a `fire`/`advance` step) pulses the
rings; a new SMS sends the amber pulse to the phone and buzzes it; a new audit row ticks the ledger; a
`SUPPRESSED_REVOKED` row flashes the shield, and the shield shows a grey slash while `resolve` says Mom's grant is
`none`. Clicking either house scrolls to the Binding pane. Replayed frames on connect set the state without
animating; `prefers-reduced-motion` turns the motion off and keeps the state.

**The five states:** (1) stack down: every pane shows the dark-tower picture with "Waiting for the stack: run
`make up`" and each fragment's own reason; (2) ENV ≠ local: the carrier pane and the story tiles are hatched with
the reason verbatim; (3) no Bedrock: the pill reads `scripted · ENV=…` with the note as text and tooltip, free
text stays disabled; (4) feed paused: a banner with a pause icon in the audit lane, the SMS lane goes on;
(5) a failed step: a coral ✗ node in the stepper with a "see transcript" link, and a "stopped" node with the run
status verbatim.

The five states at 1440×900, as `tests/test_visual.py` takes them (the real app under uvicorn over the test fakes
and moto, fixed data, step timings shown as "— ms"). The images below are copies of those shots in
`docs/img/demo-ui/` (256-colour PNG, each ≤ 150 KB; taken 2026-10-11). The test's own baselines
(`tests/visual/baseline/`) and each run's shots (`artifacts/screenshots/demo-ui/<state>.png`) are gitignored and
exist only on the machine that ran the tests.

| State | Screenshot | Generated locally |
|---|---|---|
| 1 stack down | ![stack down](../../docs/img/demo-ui/stack-down.png) | `artifacts/screenshots/demo-ui/stack-down.png` |
| 2 ENV ≠ local | ![ENV=aws](../../docs/img/demo-ui/env-not-local.png) | `artifacts/screenshots/demo-ui/env-not-local.png` |
| 3 no Bedrock (scripted pill), after Moment 1 | ![scripted, Moment 1](../../docs/img/demo-ui/moment-1-scripted.png) | `artifacts/screenshots/demo-ui/moment-1-scripted.png` |
| 4 feed paused | ![feed paused](../../docs/img/demo-ui/feed-paused.png) | `artifacts/screenshots/demo-ui/feed-paused.png` |
| 5 a failed step | ![failed step](../../docs/img/demo-ui/failed-step.png) | `artifacts/screenshots/demo-ui/failed-step.png` |

`artifacts/screenshots/demo-ui/moment-1-reduced-motion.png` is the reduced-motion variant; it must match the
`moment-1-scripted` baseline (the same state, run without the motion). After an intended visual change, run
`DEMO_UI_UPDATE_BASELINES=1 uv run pytest services/demo-ui/tests/test_visual.py -m nightly`, look at the new PNGs,
and refresh the `docs/img/demo-ui/` copies if the README should show the change. Baselines are tied to the renderer
that wrote them (Chromium build and system fonts), which is why they stay local.

## Run

```bash
make up              # the stack, including this UI in compose at http://127.0.0.1:8090 (scripted agent)
make showcase-ui     # the judge-facing run: the UI on the host at http://127.0.0.1:8091, Bedrock when AWS credentials resolve
```

On the host without make: set the variables below (see `.env.example`) and `uv run demo-ui`. On ENV=aws, run
`make showcase-ui ENV=aws`: the conversation (Asish only) and the audit feed work; the carrier pane, Reset and the
macros are local-only and say so; pane 4 shows only the QR code from Tower's `next_step`.

## Config

| Variable | Default | Meaning |
|---|---|---|
| `ENV` | `local` | `local`, `eks`, `aws`; not `local` → pane 2, Reset and macros off (doc 11 decision 2), Asish only |
| `TOWER_URL`, `TOWER_BEARER` | `http://localhost:8080/mcp`, — | Tower's MCP endpoint and bearer (a JWT on AWS) |
| `MOCK_URL`, `MOCK_ADMIN_TOKEN` | `http://localhost:8443`, — | the mock's `/_admin` and its bearer (08 §3, G1); empty URL → pane 2 off |
| `BINDING_URL` | `http://localhost:8081` | the binding page's local admin (`/_admin/*`, 404 off-local) |
| `ALERTS_URL`, `ALERTS_INTERNAL_BEARER` | `http://localhost:8082`, — | `GET /internal/sent` (06 §3.1, G3) |
| `DYNAMO_ENDPOINT` (`TOWER_DYNAMODB_ENDPOINT`), `TOWER_TABLE_PREFIX`, `AWS_REGION` | `http://localhost:8000`, ``, `us-east-1` | audit reads; with an endpoint, DynamoDB Local's placeholder credentials are set in code, never in `AWS_*` |
| `REF_AGENT`, `BEDROCK_MODEL_ID` | `auto`, `amazon.nova-micro-v1:0` | `auto` = Bedrock when credentials resolve |
| `DEMO_UI_HOST`, `DEMO_UI_PORT` | `127.0.0.1`, `8090` | listen address (`make showcase-ui` sets `8091`, from `SHOWCASE_UI_PORT`) |
| `DEMO_UI_PUBLISHED_LOOPBACK` | `0` | `1` = listens on 0.0.0.0 but is published on 127.0.0.1 only (compose, kind) |
| `DEMO_UI_TOKEN` | — | required for a non-loopback listen; open once with `?token=` (HttpOnly cookie) or send a bearer |
| `FEED_POLL_S` | `2` (`5` off-local) | feed poll interval; the poller runs only while a page is open |
| `DEMO_ALERTS_SETTLE_S` | `2.5` (`0` off-local) | macro pause after a clock jump Alerts acts on, so the Transplant macro shows both contacts' texts (06 §11.4) |
| `DEMO_UI_SEED_PATH` | the checkout's `deploy/compose/seed/seed.py` | the `make seed` module Reset runs (copied into the image) |

Every POST needs the `HX-Request` header that HTMX sends, so another web page cannot drive the UI on 127.0.0.1.
Logs are JSON lines with event names and counts; uvicorn's access log is off; every fragment and SSE frame passes
the number filter (`redact.py`). `/healthz` reports the mode, `ENV` and which panes are live, never a setting.

## How to showcase

`make up && make showcase-ui`, open http://127.0.0.1:8091 (the compose container keeps 8090; `SHOWCASE_UI_PORT=<port>`
picks another, and a port already in use is reported before anything starts). Press Moment 1, 2, 3, then Transplant: every badge
is green; the feed shows the watcher's SMS (`watcher`, named "mom"), then the `SUPPRESSED_REVOKED` row, then the
partner's (`escalation[0]`) and the neighbour's (`escalation[1]`) texts. "QR: bind Asish's line" draws a bind link
a phone on the same Wi-Fi can scan (the caption says nothing texts it yet: D1). What it proves: the same
transcripts as `make demo`, live, in one view — nothing the other showcases don't already prove.

## How to test

```bash
uv run pytest services/demo-ui -m "unit or integration"   # unit (config, redaction, macro sequencer, feed, views) + integration (app over ASGI, moto, Playwright smoke)
uv run pytest services/demo-ui/tests/test_visual.py -m nightly   # pixel baselines + reduced motion + axe (nightly; also in `make test-nightly`)
```

The fuller suite (privacy on the wire, macros vs golden over the in-process stack, import graph, never-on-AWS
static test, Playwright smoke) is doc 11 §11. Presentation (prompt 21): `test_assets.py` (no external request
target, `static/` ≤ 600 KB, a colour class per reason code, no number in any picture, the font licence),
`test_fragment_privacy.py` (all twelve fragments masked, attributes included) and `test_visual.py` (the five states
against `tests/visual/baseline/`, 0.5 % pixel tolerance, reduced motion, no request off 127.0.0.1). `test_visual.py`
is layer `nightly`, not `make test-integration`: its baselines are local (gitignored) and tied to this machine's
renderer. With no baseline it writes one and skips saying so; re-run to compare. Each shot is taken under emulated
reduced motion once the page is quiet, after `document.fonts.ready` and network idle, and a capture over 0.5 % is
retaken and compared once more at the same limit. Its axe-core check runs when `AXE_CORE_JS` names a local
`axe.min.js` (injected into the page from disk, never from a CDN), and skips otherwise. The stepper-fold check
(Moment 2 after Moment 1 in one page) is a behaviour test and stays `integration` in `test_stepper_replay.py`.
