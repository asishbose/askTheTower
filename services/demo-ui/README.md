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
| 3 Live feed | SMS from Alerts' `GET /internal/sent` (role, template, body), shown on a mocked phone (U4), and the last 50 audit rows read as each line's owner |
| 4 Binding | bind link as a QR code (drawn here with `segno`), grants, `resolve`, Mom's revoke / re-grant |
| Ops tab | a second tab (`Control room · Ops`, `/#ops`): "Run here" runs an allowlist of local `make` targets; the AWS pipeline is a fourteen-step stepper whose Run buttons start the runbook's `make … ENV=aws` targets behind a gate; one streamed, redacted log; Teardown at the bottom (see "Ops tab" below) |

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
make ops-ui          # the same host run with the Ops tab's aws runner asked for (DEMO_UI_AWS_OPS=1): http://127.0.0.1:8091/#ops
```

On the host without make: set the variables below (see `.env.example`) and `uv run demo-ui`. On ENV=aws, run
`make showcase-ui ENV=aws`: the UI reads `deploy/.env.aws` itself. With the demo admin path closed (the default)
the conversation (Asish only) and the audit feed work; the carrier pane, Reset and the macros are off with
"admin path closed: see Ops → Admin path"; pane 4 shows only the QR code from Tower's `next_step`; the pill reads
`… · ENV=aws · admin off`. With the path open (`demo_admin_cidrs` = your /32, 10 §2.1) the pill reads
`admin on`, and the carrier pane, the SMS lane (from `SentSms`), pane 4, Reset and Moment 1–3 work against the
deployment; Transplant stays local, with its caption.

## Config

| Variable | Default | Meaning |
|---|---|---|
| `ENV` | `local` | `local`, `eks`, `aws`; not `local` → pane 2, Reset and macros off (doc 11 decision 2), Asish only |
| `TOWER_URL`, `TOWER_BEARER` | `http://localhost:8080/mcp`, — | Tower's MCP endpoint and bearer (a JWT on AWS) |
| `MOCK_URL`, `MOCK_ADMIN_TOKEN` | `http://localhost:8443`, — | the mock's `/_admin` and its bearer (08 §3, G1; local); empty URL → pane 2 off |
| `BINDING_URL` | `http://localhost:8081` | the binding page's local admin (`/_admin/*`, 404 off-local) |
| `ALERTS_URL`, `ALERTS_INTERNAL_BEARER` | `http://localhost:8082`, — | `GET /internal/sent` (06 §3.1, G3) for the SMS pane and the Transplant macro's wait for each contact's text (09 §4; without it the wait is skipped); the bearer only locally |
| `DYNAMO_ENDPOINT` (`TOWER_DYNAMODB_ENDPOINT`), `TOWER_TABLE_PREFIX`, `AWS_REGION` | `http://localhost:8000`, ``, `us-east-1` | audit reads; with an endpoint, DynamoDB Local's placeholder credentials are set in code, never in `AWS_*` |
| `REF_AGENT`, `BEDROCK_MODEL_ID` | `auto`, `amazon.nova-micro-v1:0` | `auto` = Bedrock when credentials resolve |
| `DEMO_UI_HOST`, `DEMO_UI_PORT` | `127.0.0.1`, `8090` | listen address (`make showcase-ui` sets `8091`, from `SHOWCASE_UI_PORT`) |
| `DEMO_UI_PUBLISHED_LOOPBACK` | `0` | `1` = listens on 0.0.0.0 but is published on 127.0.0.1 only (compose, kind) |
| `DEMO_UI_TOKEN` | — | required for a non-loopback listen; open once with `?token=` (HttpOnly cookie) or send a bearer |
| `FEED_POLL_S` | `2` (`5` off-local) | feed poll interval; the poller runs only while a page is open |
| `DEMO_ALERTS_SETTLE_S` | `2.5` (`0` off-local) | macro pause after a clock jump Alerts acts on, so the Transplant macro shows both contacts' texts (06 §11.4) |
| `DEMO_UI_SEED_PATH` | the checkout's `deploy/compose/seed/seed.py` | the `make seed` module Reset runs (copied into the image) |
| `DEMO_UI_CHECKOUT` | found from the package location | the repo root (holds `Makefile` and `mk/`): the Ops runner's `cwd` and where the stepper reads `artifacts/` and `deploy/` |
| `DEMO_UI_AWS_OPS` | — | exactly `1` asks for the Ops tab's aws runner (`make ops-ui` sets it); on only when the whole gate holds (below) |
| `DEMO_UI_AWS_ENV_FILE` | `<checkout>/deploy/.env.aws` | ENV=aws only: read at start; for its keys (`TOWER_URL`, `BINDING_URL`, `BINDING_ADMIN_URL`, `MOCK_URL`, `ALERTS_URL`, `WEB_CHAT_URL`, `DEMO_ADMIN_BEARER`, `TOWER_TABLE_PREFIX`, `AWS_REGION`) the file wins over the shell |
| `DEMO_UI_COGNITO_USERS` | `<checkout>/artifacts/cognito-users.json` | ENV=aws only: Asish's and Mom's Cognito subs for Reset and the Grant step; missing → Reset and macros off with "run `make cognito-users ENV=aws`" |
| `BINDING_ADMIN_URL`, `BIND_ADMIN_TOKEN` | `BINDING_URL`, — | where pane 4 and the AWS Reset call `/_admin/*`; on AWS the execute-api URL (required there). `BIND_ADMIN_TOKEN` is never used on ENV=aws |
| `DEMO_ADMIN_BEARER` | — | ENV=aws, from `deploy/.env.aws`: the one bearer of the demo admin path, sent on every call to the mock's, Alerts' and the binding page's admin routes (10 §2.1, decision 1 of 2026-10-11). The local tokens (`MOCK_ADMIN_TOKEN`, `ALERTS_INTERNAL_BEARER`, `BIND_ADMIN_TOKEN`) are never sent on ENV=aws |

The two file defaults apply only to the real process (`Settings.from_env()`); a test that passes its own mapping
names its files explicitly, so a developer's own `deploy/.env.aws` never leaks into a test.

Every POST needs the `HX-Request` header that HTMX sends, so another web page cannot drive the UI on 127.0.0.1.
Logs are JSON lines with event names and counts; uvicorn's access log is off; every fragment and SSE frame passes
the number filter (`redact.py`). `/healthz` reports the mode, `ENV`, which panes are live and `ops: {runner, aws, running}`
(`aws`: `on`/`off`, never the reason), never a setting.

## Ops tab

A second tab, `Control room · Ops` in the top bar; `/#ops` opens it directly (doc 11 §2 "The Ops tab", prompts 22
and 23, decision 14 = T1–T8). It works with the stack down. Its fragments load only when the tab is first shown, so
presenting on Control room starts no probe and no AWS call. Layout: a header (credentials · region · account · the
gate · the cost banner), "Run here" (local) above the one shared log viewer, the "AWS pipeline" stepper on the right
with the admin path after step 5, and a red-bordered **Teardown** at the bottom.

**What runs.** Only the 29 entries of `src/demo_ui/ops/allowlist.py` (the table in doc 11 §2; `test_ops_allowlist.py`
compares the two): eleven **local** targets as `make <name> ENV=local` (`up`, `down`, `seed`, `demo`, the three
showcases, the four test targets), and eighteen **aws** targets as `make <target> ENV=aws` (`ecr-up`, `push`,
`plan`, `deploy`, `outputs`, `cognito-users`, `seed-aws`, `web-chat-sync`, `web-chat-url`,
`seed-aws SEED_ARGS=--bind-asish`, `demo`, `latency-aws`, `test-e2e`, `test-report`, `admin-open`, `admin-close`,
`down`, `down-all`). No free text, no shell, no argv from the browser: the only inputs are a confirm word (compared,
never passed) and, for `admin-open`, a `/32` (validated as a public IPv4 address, then appended as `ADMIN_CIDR=`).
One job at a time across both, never beside a macro (409). Lines stream on `/events` (`ops` frames every 200 ms,
`ops-status` at the end), each through `redact.mask_secrets` (bearers, `*_TOKEN=`-style assignments,
`aws_secret_access_key`/`aws_session_token`/`aws_access_key_id`, `AKIA`/`ASIA` ids, 40-character secret-shaped runs,
100+ character base64 runs, the UI's own secrets and the secret values of `deploy/compose/.env` and
`deploy/.env.aws`) and `redact.mask` (numbers).

**Credentials (T3).** The child gets an allow-listed environment (PATH, HOME, locale, CA bundles, proxies, Docker
and uv settings); nothing matching `AWS_*`, `TF_VAR_*`, `COGNITO_*`, `*_TOKEN`, `*_BEARER`, `*_SECRET`,
`*_PASSWORD`, `*_KEY`, `VIRTUAL_ENV` or `ENV` passes from the UI's process. The aws targets get their credentials
(`AWS_PROFILE` or the key pair, `AWS_REGION`) from the root `.env`, which the child `make` loads itself through
`mk/vars.mk`, exactly as in a terminal. The UI never opens `.env` (it only asks whether it exists) and never reads a
key into Python: the header's facts come from a `make --eval` probe that prints `${VAR:+1}` instead of a value.

**The gate (T4).** The aws Run buttons, POSTs and probes are on only when all hold, checked in order; the first that
fails is the reason, shown verbatim in the header, on every aws button and as the POST's 403:
G1 the host run (not the compose/kind container), G2 `DEMO_UI_HOST` exactly `127.0.0.1`, G3 `DEMO_UI_AWS_OPS=1`,
G4 `make` and the checkout, G5 the root `.env` exists, G6 `make config-check` passes (cached until `.env` changes or
re-check). Copy buttons always work. `ENV` is not part of the gate; with `ENV=aws` only the *local* runner is off.

**Confirm words (T5).** `down` (Down), `down-all` (Down all), `close` (admin Close), and `deploy` when the last plan
destroys anything or its summary cannot be read. Run enables when the field holds the word; the server compares
again (400 "type <word> to confirm", nothing runs). Only then is `FORCE=1` appended (to the three whose recipes
ask; never to `deploy`). The plan summary (`Plan: A to add, C to change, D to destroy.`) becomes chips on step 3 and
in the job badge. Deploy needs a *current* plan (newer than `artifacts/image-tag` and `envs/aws.tfvars`).
`admin-open` stops with exit 3 after a destroying plan (changing `demo_admin_cidrs` rotates `DEMO_ADMIN_BEARER` and
rolls the mock task): read the plan, then 4 · Deploy with `deploy`. Restart the UI after Open or Close so the Control
room uses the new bearer.

**Stop.** Local jobs: SIGINT, SIGTERM after 1.5 s, SIGKILL at 2 s. AWS jobs: SIGINT once ("stopping: terraform is
saving state"), SIGTERM on a second press, never SIGKILL (a kill leaves the Terraform state lock held).

**Transcripts (T7).** Every aws job is appended to `artifacts/ops-log/<YYYY-MM-DD>-<name>.log` (directory 0700, file
0600, gitignored): a start line (with `ADMIN_CIDR=<cidr>`, never the address), every line exactly as the browser got
it, `== plan +A ~C -D` when a summary was seen, and an end line. Paste it to the agent when an apply fails. Local
jobs write nothing.

**State first (T6).** The stepper is recomputed on tab open, after every job, on re-check and every 15 s (while the
tab is shown) from the files `make` writes (`artifacts/tf-outputs-ecr.json`, `image-tag`, `terraform-plan.txt`,
`tf-outputs.json`, `cognito-users.json`, `seed-aws.json`, `web-chat-sync.json`, `latency-aws.md`, `test-report.md`,
the transcripts, `deploy/.env.aws`), from unauthenticated GETs (binding `/healthz`, the web chat page, the admin
clock) and, with the gate on, from read-only probes (`aws sts get-caller-identity`, `aws ecr
describe-repositories` names only, `terraform output -json` filtered to five non-sensitive keys, `curl
checkip.amazonaws.com` for the `/32` default; 5 s each, never on the 15 s tick). The next step is highlighted;
evidence never shows a key, a bearer, a sub, an admin URL or the `/32`. Without the `aws` CLI the header says
"account unknown: aws CLI not found (runbook 0.8)" and the steps fall back to their files.

**How to turn it off.** It is off unless asked for: `make showcase-ui` never sets `DEMO_UI_AWS_OPS`. Leave
`DEMO_UI_AWS_OPS=` empty in the root `.env` and use `make showcase-ui`, or stop `make ops-ui`.

## How to showcase

`make up && make showcase-ui`, open http://127.0.0.1:8091 (the compose container keeps 8090; `SHOWCASE_UI_PORT=<port>`
picks another, and a port already in use is reported before anything starts). Press Moment 1, 2, 3, then Transplant: every badge
is green; the feed shows the watcher's SMS (`watcher`, named "mom"), then the `SUPPRESSED_REVOKED` row, then the
partner's (`escalation[0]`) and the neighbour's (`escalation[1]`) texts. "QR: bind Asish's line" draws a bind link
a phone on the same Wi-Fi can scan (the caption says nothing texts it yet: D1). What it proves: the same
transcripts as `make demo`, live, in one view — nothing the other showcases don't already prove.

The Ops tab: with the stack down, open http://127.0.0.1:8091/#ops (from `make showcase-ui`): only the local targets
that need no stack are enabled, the aws runner says "AWS runs are off: start the UI with make ops-ui …", and the
stepper shows what exists from files ("Nothing deployed yet" on a fresh checkout). Press `up`, watch the log stream
and the badge turn green; then `seed`, `demo`, `test-unit`. Then stop it and run `make ops-ui` (same port): the
header shows the profile or "key pair from .env", the region and the account id (dashed), never a key; step 1 is
highlighted; Run on `1 · ECR repositories` (idempotent, no cost) turns it green with the five repository names, and
the transcript is in `artifacts/ops-log/`. The rest is the runbook (`docs/runbooks/aws-first-run.md`), one button per
command; Teardown needs its word. With the admin path open (Open, then 4 · Deploy with `deploy`, then
`make showcase-ui ENV=aws`): Reset, Moment 1–3; the SMS phone fills from `SentSms`. Close it afterwards.

## How to test

```bash
uv run pytest services/demo-ui -m "unit or integration"   # unit (config, redaction, macro sequencer, feed, views) + integration (app over ASGI, moto, Playwright smoke)
uv run pytest services/demo-ui/tests/test_visual.py -m nightly   # pixel baselines + reduced motion + axe (nightly; also in `make test-nightly`)
```

The fuller suite (privacy on the wire, macros vs golden over the in-process stack, import graph, never-on-AWS
static test, Playwright smoke) is doc 11 §11. Presentation (prompt 21): `test_assets.py` (no external request
target, `static/` ≤ 600 KB, a colour class per reason code, no number in any picture, the font licence),
`test_fragment_privacy.py` (all eighteen fragments masked, attributes included; the Ops tab added six) and `test_visual.py` (the five states
against `tests/visual/baseline/`, 0.5 % pixel tolerance, reduced motion, no request off 127.0.0.1). `test_visual.py`
is layer `nightly`, not `make test-integration`: its baselines are local (gitignored) and tied to this machine's
renderer. With no baseline it writes one and skips saying so; re-run to compare. Each shot is taken under emulated
reduced motion once the page is quiet, after `document.fonts.ready` and network idle, and a capture over 0.5 % is
retaken and compared once more at the same limit. Its axe-core check runs when `AXE_CORE_JS` names a local
`axe.min.js` (injected into the page from disk, never from a CDN), and skips otherwise. The stepper-fold check
(Moment 2 after Moment 1 in one page) is a behaviour test and stays `integration` in `test_stepper_replay.py`.

Pane 5 and ENV=aws (prompt 22): `test_ops_allowlist.py` (unit: the allowlist equals doc 11's table, argv rules,
the O2 names, denied keys refused at import, the child env, `mask_secrets`), `test_ops_runner.py` (integration,
real subprocesses with test targets: ordered masked frames, Stop of a whole group within 2 s, 409, exit codes,
no parent credential in the child), `test_ops_aws_status.py` (unit: each step absent/present/malformed, probes
200/403/404/timeout over `httpx.MockTransport`, no `Authorization`, no SDK or process import),
`test_ops_routes.py` (integration: guards, 404, ENV=aws and container mode off, frames on `/events`, the stack-down
state, every checklist state), `test_config_aws.py`, `test_aws_reset.py`, `test_aws_macros.py` (ENV=aws with the
admin path open: the file wins, the HTTP Reset order and bearers, Moment 1–3, Transplant off, `admin on`).
Ops tab (prompt 23): `test_ops_allowlist.py` (29 rows vs the doc, all ten columns, confirm words, env modes, the
never-lists, doctored copies refused), `test_ops_envload.py` (a real `make` over a temp checkout with a copy of
`mk/vars.mk`: `.env` values reach the child, the UI's own never; `facts` prints names and booleans only; the three
modules never read a file), `test_ops_gate.py` (G1–G6, the cache, flipped conditions), `test_ops_probes.py` (fake
binaries: filters, missing binaries, timeout kills the group, the cap), `test_ops_status.py` and
`test_ops_aws_status.py` (the fourteen steps, injected probes, next step, banner, admin box), `test_ops_runner_aws.py`
(integration over ASGI with a fake `make` first on PATH: words, FORCE=1, plan chips, deploy's word, the `/32`,
transcripts 0600/0700 and masked, Stop SIGINT→SIGTERM, gate off → 403), `tests/aws/test_admin_cidr.py` (the
tfvars editor and the two recipes in a temp Makefile copy with fake terraform/uv/curl). No test runs a real target
of the allowlist or touches AWS.
