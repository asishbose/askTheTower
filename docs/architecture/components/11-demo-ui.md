# 11 — Demo UI (control room)

**Role:** one browser page for presenting and rehearsing the demo. It shows the conversation, the carrier's state, the live alerts and audit rows, and the binding QR code side by side. It only drives what `make demo` and the showcase scripts already drive.
**Runs on:** the presenter's laptop only: a container in compose on `127.0.0.1:8090` (scripted agent), or `uv run` on the host on `127.0.0.1:8091` (`make showcase-ui`, Bedrock when credentials resolve). Demo tooling, not a system component. It is never deployed to AWS.
**Owns:** nothing. No table, no consent, no policy, no carrier credentials. It reads, and it calls admin endpoints that already exist.
**Status:** built 2026-10-09 (step 2): `services/demo-ui`, its compose entry, the kind-only Helm chart `deploy/helm/demo-ui` and `make showcase-ui` (10 §5). Diagram [`11-demo-ui`](../diagrams/11-demo-ui.drawio). The decisions of §13 were taken by the user on 2026-10-09.

---

## 1. What it is and is not

| It is | It is not |
|---|---|
| A view over the reference client (09), the mock's `/_admin` (08 §3), the binding page's local admin (04), Alerts' local sent-SMS list (06 §3.1) and the audit table (07) | A second client of the carrier: it makes no CAMARA call, never calls the carrier gateway and holds no carrier token |
| A runner for the four showcase stories in `ref_client.demo.STORIES`, step for step, with their narration and a pass/fail per step | A new demo script: the stories, expected reason codes and golden files stay in the reference client |
| Text in, text out (Alexa+'s part is played by the reference client's agent) | A voice surface, or a replacement for the Alexa+ simulator in the video |
| Laptop-only, with ENV=local as the default | Anything that runs in the AWS account (no image in ECR, no Terraform, `values-eks` `enabled=false`) |

## 2. Shape

FastAPI + HTMX, one static page with no build step, mobile-friendly (the four panes stack on a phone). HTMX 2 and its SSE extension are vendored under `static/` (no CDN at demo time). One server process holds one feed poller and one demo runner. The browser gets HTML fragments and one Server-Sent Events stream, `GET /events`.

```
services/demo-ui/
  src/demo_ui/config.py     # Settings from the environment (§6); mode, ENV, which panes are live
  src/demo_ui/deps.py       # composition root: httpx clients, the Store, the agent factory, the seed reset
  src/demo_ui/app.py        # routes: /, /healthz, /events (SSE), /say, /macro/{name}, /carrier/*, /binding/*
  src/demo_ui/feed.py       # the poller (§4): audit rows, sent SMS, carrier state, grants → SSE frames
  src/demo_ui/runner.py     # macros over ref_client.demo.STORIES via run_demo(on_step=…) (§5); one run at a time
  src/demo_ui/clients.py    # thin httpx wrappers: mock /_admin (bearer), binding /_admin, Alerts /internal/sent
  src/demo_ui/views.py      # Jinja2 fragments (autoescaped) for each pane
  src/demo_ui/redact.py     # the output filter (§8): every frame and fragment passes through it
  src/demo_ui/templates/    # the fragments (flat: one file per fragment, names in "Presentation" below)
  src/demo_ui/static/       # index.html, app.css, stage.js, htmx.min.js, htmx-sse.js, img/, fonts/
```

| Pane | Does | Consumes |
|---|---|---|
| **1 Conversation** | Type an utterance as Asish or Mom (Asish only on ENV=aws). See the tool call, `reason_codes` (as chips), `summary` / spoken text and `next_step`. Macro buttons: Moment 1, Moment 2, Moment 3, Transplant, each step with a pass/fail badge and its timing. Top bar: an environment pill with the agent mode (`scripted` / `bedrock`) and `ENV`. | `ref_client` as a library: `TowerClient` → Tower `/mcp`, `BedrockAgent` or `ScriptedAgent`, `run_demo(on_step=…)` |
| **2 Carrier controls** | Reset (the `make seed` code). Show and advance the mock clock. Fire `sim_swap`, `cf_set`, `cf_clear`, `unreachable`, `reachable` on Asish's or Mom's line by the mock's opaque `ref`. Inject or clear faults. Show line state and subscriptions. | mock `/_admin/*` with `MOCK_ADMIN_TOKEN` |
| **3 Live feed** | The last 50 audit rows, from the line-holders' own views, and the SMS that Alerts sent, live over SSE. | DynamoDB (`tower_audit` reader), Alerts `GET /internal/sent` |
| **4 Binding** | QR code of a bind link, current grants and what `resolve` returns, and Mom's revoke and re-grant of Asish's `watch`. | binding page `/_admin/*`; Tower's `next_step.url` |

### Presentation (prompt 21, specified 2026-10-09)

The look changes. The behaviour stays the same. This section covers templates, `static/` and CSS only. No route, SSE event, feed source or fragment field is added. `redact.py`, the `HX-Request` check, the token cookie, the poller and the runner are unchanged. **If a design element needs data the page does not already receive, the element is dropped** (see "Dropped" below). No backend call is ever added for decoration.

**Layout** (1440×900 and up, degrades to 1024; on a phone the panes stack as they do today): a top bar (wordmark with the tower glyph from `docs/img/hero.svg`, the pill, the five-dot health strip, `#run-status`), then **the Stage** (full width, about 260 px), then the four panes as glass cards in a 2×2 grid, then the footer (its text unchanged).

**Files, as built (flat).** There is no `templates/fragments/`, no `base.html` and no `macros.py`: the sequencer is `runner.py`.
- `static/index.html` is served raw by `GET /`. It is not a Jinja template and is not masked. It keeps `sse-connect="/events"` and `/static/htmx.min.js` (`test_app.py`), and every target id: `#pill` (`GET /fragment/pill`), the controls loaded by `GET /fragment/conversation-controls` and `GET /fragment/carrier-controls`, `#run-status` (`status`), `#log` (`turn`, `afterbegin`), `#carrier`, `#sms`, `#audit`, `#qr`, `#grants`, `#carrier-status` and `#binding-status`.
- `static/app.css` replaces `demo-ui.css`. It is the only stylesheet: CSS variables, nesting allowed. `static/stage.js` is an ES module. It fetches `static/img/scene.svg` once (same origin), inlines it, and then only toggles classes.
- `static/img/scene.svg` has the ids `#house-you`, `#tower`, `#rings`, `#beacon`, `#beam-req`, `#beam-sms`, `#house-mom`, `#phone`, `#shield` and `#ledger`. `static/img/icons.svg` is a `<symbol>` sprite. `static/img/empty-state.svg`. `static/fonts/*.woff2` with the OFL `LICENSE` beside them.
- The twelve templates keep their names and context variables: `pill`, `conversation_controls`, `carrier_controls`, `carrier`, `turn`, `said`, `status`, `sms`, `audit`, `grants`, `qr`, `unavailable`. Only markup, classes and `data-*` attributes change. A `data-*` attribute carries only values the fragment already shows.
- As built (step 2): a reason-code chip is `<span class="rc rc-<CODE>"><span class="chip">CODE</span></span>`, so the inner element keeps `class="chip"` exactly (the existing view tests count it). A turn's `data-*` (`step`, `kind`, `ok`, `ms`, `codes`, `who`) sit on its `<header>`, not on `<article>`, whose tag the runner tests match as `class=… id=…>`; `replay` is read from the header's `<span class="tag">replay</span>`, never from the article's class: `#log` keeps every run, so a replayed step's `id="step-…"` repeats an earlier run's, and until htmx settles the new element carries the old one's attributes (R21-1). A revoked grant row is `<li class="revoked">`, a live one `<li class="live">`. The scene's `viewBox` is wider than the art (`-400 0 2400 300`, `slice`), so the Stage fills any width from 1024 up without letterboxing; `#beam-req` holds two paths, `.from-you` and `.from-mom`. The font is Space Grotesk 2.0.0, one variable woff2 (49 KB), from the upstream repository `floriankarsten/space-grotesk` (provenance in the service README). `static/` totals 175 KB.

**Stage triggers.** `stage.js` listens to `htmx:sseMessage` and `htmx:afterSwap` on the existing targets and reads the `data-*` attributes from the swapped fragment. It animates only items that are *new* since the previous swap of that target (keyed by step id, SMS `n` or audit row key). Frames replayed when a page connects set the state without animating.

| Stage element | Event (SSE / swap) | Field it reads (template ← source) |
|---|---|---|
| cyan `#beam-req`, house→tower, 600 ms | `turn` | `turn.html` `r.kind == "say"` ← `StepReport.kind` (`runner.on_step` → `views.step_fragment`). Macro steps start at `#house-you`, because all four `STORIES` run as `user-asish` |
| the same beam from `#house-mom` | `turn` (published by `/say`) | `said.html` `who` (`app.say` → `views.say_fragment`) |
| `#rings` pulse | `turn` · `carrier` | `turn.html` `r.kind in {fire, advance}` (+ `r.fired`); `carrier.html` line state or subscription `events` changed ← `feed.carrier_view` |
| `#beacon` coral | `turn` | a coral-class code in `turn.codes` ← `Turn.reason_codes` (`views.turn_view`) |
| amber `#beam-sms` tower→phone, `#phone` buzz | `sms` | a new `sms.html` entry (`n`, `role`, `template`) ← `SmsSource` (Alerts `/internal/sent`) |
| `#shield` grey with a slash, flash | `audit` | a new `audit.html` row with `SUPPRESSED_REVOKED` in `r.codes` ← `AuditSource.rows` |
| `#shield` on/off toggle | `turn` · `grants` | `turn.html` `r.kind == "grant"`; `grants.html` `revoked_at` on the `watch` row and `resolve.grant` ← `GrantsSource` |
| outcome chips float up from the tower and fade; the same codes stay in a "last answer" row on the Stage (state, so it shows with reduced motion too) | `turn` | `turn.codes`, text verbatim |
| `#ledger` tick | `audit` | any new audit row |
| houses clickable (scroll to `#pane-binding`) | none | static |

**Panes and top bar, mapped to existing data**

| Element | Event / route | Field |
|---|---|---|
| Health strip: mock · alerts · binding · DynamoDB | `carrier` · `sms` · `grants` · `audit` | a dot is mint when the pane fragment is rendered and grey when it is `unavailable.html` (mark its root `data-down`). The hover text is the fragment's own reason |
| Health strip: Tower | `turn` | mint once a `say` turn has returned codes this session, grey before ("not asked yet"). This shows "answered", not liveness |
| Mode pill, Bedrock tooltip | `GET /fragment/pill` | `mode`, `env` (add `data-mode`/`data-env`); the `pill-note` text stays visible and is also the `title` |
| Story tiles, spinner | `#run-status` (`status`) | `status.html` `level` (`info` = running, `pass`, `fail`); the macro list comes from `conversation_controls.html` `macros`/`captions` |
| Stepper (ticks/crosses, timings) | `turn` | `r.step_id`, `r.kind`, `r.ok`, `r.elapsed_ms`, `r.replay` (replayed steps fold into one node) |
| Chat bubbles | `turn` | `r.narration`/`turn.utterance`, `turn.calls`, `turn.codes`, `turn.spoken`, `agent`, `turn.next_step`. Macro bubbles show "A". `/say` bubbles show `who` |
| Line card (holder, `sim` / `cf` / `reach` chips) | `carrier` | `l.holder`, `l.ref`, `l.sim_change_at`, `l.call_forwarding`, `l.reachable`, `l.connectivity`. **Neutral chips** that show raw state: the UI never labels a SIM change "recent", because that is policy |
| Clock widget | `carrier` | `clock` (the mock's RFC 3339) in `<time datetime>`. Redrawn on each `carrier` frame (after +1/+12/+20, `feed.poke`), never extrapolated |
| Feed timeline, filter chips `all · sms · audit` | `sms` · `audit` | `e.at`, `e.role`, `e.user_id`, `e.template`, `e.body`; `r.ts`, `r.log`, `r.line`, `r.actor`, `r.tool`, `r.trigger`, `r.outcome`, `r.codes`, `r.message_ref`. The filters are CSS only, with no request |
| Consent graph (owner line —kind→ grantee) | `grants` | `g.alias`, `g.line`, `g.grant`, `g.grantee`, `g.revoked_at`; mint edge when live, grey dashed with a slash when revoked |
| `resolve` chip row | `grants` | `resolve.bound`, `resolve.grant` |
| QR code in a phone frame | `qr` | `svg`, `who`, `caption` (D1, unchanged), `url` |

**Dropped or re-scoped** (each would need data the page does not receive):
1. *Rings on every Tower→carrier call.* The page never sees Tower's carrier calls. Inferring them from reason codes would put policy knowledge in the UI (§8.1). Re-scoped: the rings mean "the carrier's state moved" (the row above).
2. *A `GET /fragment/health` endpoint.* `/healthz` reports the UI's own config, not the other services' health. A health endpoint would add four outbound health calls per tick for decoration. Not added: the strip is derived from frames the page already gets.
3. *One merged chronological timeline and relative times.* Audit `ts` is `HH:MM:SS` only (`feed.py`), SMS `at` is the mock clock, and on ENV=aws the audit uses the wall clock and there are no SMS. Kept: one timeline column with two lanes (SMS, then audit), each newest first, with the time exactly as given.
4. *"Step n of N".* The page is never told a macro's length, so the stepper grows as the steps arrive.
5. *Per-recipient phones.* The scene has one `#phone`. The SMS `role` goes in its tooltip.

**Colour has one meaning per hue** (tokens from `hero.svg`, on navy `#070b1f → #1d2b63`):

| Hue | Token | Means | Reason codes (`.rc-<CODE>`, text verbatim) |
|---|---|---|---|
| cyan `#4fe3ff` | `--req` | the request path: beam, user bubbles, focus ring | none |
| amber `#ffd27a` | `--sms` | proactive and SMS: `#beam-sms`, SMS rows | `ACK_IGNORED_SWAPPED_LINE` |
| mint `#7CF2B4` | `--ok` | consent and ok: live grants, shield on, pass, health up | `OK` |
| coral `#ff6b6b` | `--alert` | refusal or alert: beacon, FAIL, crosses | solid: `SIM_SWAPPED_RECENT`, `CALL_FORWARDING_SET`, `UNREACHABLE`, `ALERT_FAILED`; outline (refusals): `NOT_BOUND`, `NO_CONSENT`, `STALE_DATA`, `CARRIER_ERROR`, `SERVICE_UNAVAILABLE` |
| slate grey | `--muted` | consent withdrawn, unknown, raw state | `SUPPRESSED_REVOKED` (grey, with a slash) |

Every code in `packages/tower-policy/src/tower_policy/codes.py` has a class. An unknown code falls back to a neutral chip. Colour never carries meaning alone: the code text, ✓/✗ glyphs and the shield's slash carry it too.

**The five states**
1. **Stack down.** Every feed fragment (`carrier`, `sms`, `grants`, `audit`) is `unavailable.html` and the pill says ENV=local. Every pane then shows `empty-state.svg`, "Waiting for the stack: run `make up`" and the fragment's own reason. Pane 1 is covered too. There are no stack traces and no italic error strings. Before the first frame arrives the panes show a skeleton, not this state. If the UI process itself is down there is no page, and nothing to design.
2. **ENV ≠ local.** Pane 2 and the macro tiles are greyed (hatched) and show `carrier_off_reason` verbatim (`carrier_controls.html`, `conversation_controls.html`). The Stage still runs for `/say` and audit rows. `make up` is not shown.
3. **Bedrock unavailable.** The pill reads `scripted · ENV=…` with the existing note as visible text and as the tooltip. The free-text row stays disabled with its existing placeholder (§9).
4. **Feed paused.** `#audit` holds `unavailable.html` ("feed paused: DynamoDB …"), shown as a banner across the audit lane with a pause icon, not italic text. "SMS list unavailable" in `#sms` is shown the same way. The other lane goes on.
5. **Failed macro step.** On `turn.html` with `r.ok is false` (`.bad`), the stepper node turns into a coral ✗ with the `step_id` and a "see transcript" link to `#step-<id>`. The bubble shows the actual and the expected chips (as today). If a run stops (`status` with level `fail`), the stepper ends in a coral ✗ "stopped" with the status text verbatim; a run that passes ends in a mint ✓ with the status text.

**Asset rules**
- Vector only: inline or vendored SVG, no raster photos. No Alexa, Amazon or carrier logos or trade dress; the speaker is a plain cylinder with a light ring.
- No external URL anywhere in `templates/` or `static/` (no CDN, fonts, analytics or tracking). Fonts are vendored woff2 with the OFL `LICENSE`; the UI text uses `system-ui`, the display face only the title and the pane names.
- Page weight ≤ 600 KB for all of `static/` (66 KB today, 51 KB of it HTMX). First paint < 1 s on the laptop.
- Motion is 150–300 ms (the beam 600 ms). `prefers-reduced-motion: reduce` turns off transitions and keyframes but still applies the state classes (beacon colour, shield, health).
- Accessibility: Lighthouse/axe ≥ 95. Text contrast ≥ 4.5:1 on navy, with a visible focus ring. `aria-live="polite"` on `#run-status`, on the stepper and on one visually hidden feed announcer that `stage.js` writes the newest row into (the feed lanes are swapped whole, so they are not live regions themselves). The Stage is `role="img"` with an `aria-label`.
- **Privacy on art.** No number, fake number or redacted-looking number (`•••• 0101`) in any picture. Every fragment goes through `redact.mask`, so **fragments never carry inline SVG geometry**: they use `<svg><use href="/static/img/icons.svg#…"></use></svg>`. Coordinates such as `120 340 1200` can match the grouped-number regex and come out mangled. In `static/` SVGs, coordinates are rounded to ≤ 2 decimals, so no run of 10 or more digits can occur.

**Out of scope for this prompt.** `test_redact.py` had 4 red cases (`test_grouped_numbers_are_masked`, `+`-prefixed international groupings) that prompt 21 could not fix. Resolved by decision 1 (2026-10-11): §8.3 now masks with the shared rule `ref_client.privacy`, and the four pass with their assertions unchanged.

## 3. Interfaces it consumes (exact, as built)

| Pane | Call | Where it is defined | Notes |
|---|---|---|---|
| 1 | MCP `tools/list`, `tools/call` over Streamable HTTP at `TOWER_URL` | `ref_client/mcp_client.py` `TowerClient`, `TowerConfig.headers` | `Authorization: Bearer $TOWER_BEARER`; locally also `X-Tower-User: user-asish \| user-mom` (ignored on AWS) |
| 1 | Bedrock Converse (tool use) | `ref_client/agent.py` `BedrockAgent` | the only model call in the repo; `ScriptedAgent` when there is no Bedrock (`REF_AGENT=auto\|bedrock\|scripted`, `has_aws_credentials`) |
| 1 | `run_demo(agent_for, control, stories=…, reset=False, on_step=…)` → one `StepReport` per step | `ref_client/demo.py` (G4) | step id, kind, narration, expected vs actual reason codes, `ok`, `elapsed_ms`, the `Turn` |
| 1 | `next_step.kind == "bind_line"`, `next_step.url` | `tower_mcp/next_step.py` `build_next_step` | a fresh 10-min bind link; pane 4 renders it as a QR code |
| 2 | `GET /_admin/state?view=refs` | `mock_carrier/routers/admin.py` `state` (G2) | lines keyed by the opaque `ref`, no `msisdn`, no sink URLs, no sink inbox: the UI never receives a number |
| 2 | `POST /_admin/scenarios/load {name:"demo"}` · `GET /_admin/scenarios` | `admin.py` `load` · `scenarios` | load needs the bearer (G1) |
| 2 | `GET /_admin/clock` · `POST /_admin/clock {advance_s}` | `admin.py` `get_clock` · `clock` | GET is open (Tower and Alerts read it); POST needs the bearer; the response lists the timeline events that fired |
| 2 | `POST /_admin/lines/{ref}/events {event}` | `admin.py` `line_event` (G2) | the path takes the `ref` (`line:<16 hex>`); the reply then carries no `msisdn` either |
| 2 | `POST /_admin/faults {kind, n}` · `DELETE /_admin/faults` | `admin.py` `faults` · `clear_faults` | `timeout`, `500`, `429`; both need the bearer |
| 2 | Reset: `seed.reset(mock, page, store)` | `deploy/compose/seed/seed.py` | the `make seed` code path (decision 5): scenario reload, Watches and `AlertsState` cleared, users, binds, grants. ENV=local only |
| 2 | `GET /healthz` | `mock_carrier/app.py` | |
| 3 | `tower_consent.list_lines(store, owner)` → `tower_audit.list_for_line(store, line_id, owner)` | `tower_consent/bind.py`; `tower_audit/reader.py` (owner check `_require_owner`) | read as each demo line-holder, so 07 §4 holds: the feed is Asish's log plus Mom's log, never a watcher's view |
| 3 | `GET {ALERTS_URL}/internal/sent?after=<n>` | `alerts/internal_api.py` (G3) | bearer `ALERTS_INTERNAL_BEARER`; served only with `ALERTS_MODE=local`; `{n, at, template, role, user_id, body}`, never a number |
| 3 | `GET /healthz` | `alerts/local.py` | |
| 4 | `POST /_admin/bind-tokens {user_id}` → `{url}` | `binding_page/routes/admin.py` `admin_bind_token` | the UI adds `?as=phone-<who>` locally (the mobile-data simulation, e2e §5) and draws the QR code itself (`segno`, SVG); the page has no QR endpoint |
| 4 | `GET /_admin/tables?format=json&table=Grants` | `admin.py` `admin_tables` | the page returns the Grants table only (04 §8; 2026-10-11), already redacted, so the UI never receives another line's Audit rows (§8.5) |
| 4 | `GET /_admin/resolve?user=..&line=..` | `admin.py` `admin_resolve` | shows `resolve` flipping after a revoke |
| 4 | `POST /_admin/grants {owner_user_id, grantee_user_id, grant, alias, action}` | `admin.py` `admin_grant` | idempotent; the same `tower_consent.grant` / `revoke` the resident's `/me` page calls |
| 4 | all `/_admin/*` answer 404 unless `TOWER_ENV=local` and `BIND_ADMIN=1` | `binding_page/config.py` `admin_enabled` | so on AWS pane 4 has only the QR code from `next_step` |

## 4. The live feed (SSE, no DynamoDB Streams)

One poller per UI process, every `FEED_POLL_S` (2 s locally, 5 s on AWS), and only while at least one page is open. Each source is rendered to its pane fragment; a fragment is pushed as an SSE event to every open page only when it differs from the last tick's. A page that connects gets the current fragments at once.

| SSE event | Source per tick | Shown |
|---|---|---|
| `audit` | `list_lines` for `user-asish` and `user-mom` (cached for 30 s), then `list_for_line` for each line as its owner; merged, newest 50 | time, whose log, actor, tool, trigger, `outcome`, `reason_codes`, `message_ref`. `line_id` shortened to 8 characters |
| `sms` | `GET {ALERTS_URL}/internal/sent?after=<n>` (G3) | time (mock clock), recipient role (`watcher`, `line-holder`, `line-holder backup`, `escalation[n]`) and user id, template id, body |
| `carrier` | `GET {MOCK_URL}/_admin/state?view=refs` | clock, per holder: SIM change time, forwarding, reachable, connectivity; subscriptions with status and events sent (by `ref`); pending faults |
| `grants` | `GET {BINDING_URL}/_admin/tables?format=json&table=Grants` (the page sends only Grants) | owner line (8 characters), grantee, kind, alias, `revoked_at` |
| `turn`, `status` | the macro runner (§5) and `/say` | each step's narration and badge; the run's state |

The chain-verified badge stays on the binding page (`/me/lines/{line_id}/audit`, `binding_page/routes/audit.py`). Verifying the chain needs the marker signer key (`tower_audit/chain.py`), and the UI holds no keys.

## 5. Macros

The four buttons run `ref_client.demo.STORIES`: `moment-1`, `moment-2`, `moment-3`, `transplant`, through `run_demo(..., reset=False, on_step=...)` (G4). They are the same `Say`, `Advance`, `Fire`, `Grant`, `Settings` and `Note` steps in the same order as `make demo`, executed by the same code. The `why` and `text` of each step are the narration. Each step's report carries its expected and actual reason codes and its timing; a `Say` shows a green or red badge (the golden-file check, 09 §4). That badge is the on-screen evidence that a macro is the `make demo` sequence.

- **One timeline.** The stories share one mock timeline: Moment 2's +8 min only means something after Moment 1's +12. A button runs Reset, replays the earlier stories collapsed (marked `replay`), then its own story in full. Reset (ENV=local only) is the `make seed` code (`deploy/compose/seed/seed.py` `reset`): it reloads the scenario and clears Watches and `AlertsState`, because a stale `last_state` would otherwise answer.
- **One run at a time.** A second click while a run is going is refused (409, and the page says so).
- **Honest captions.** A caption names what the code does not do yet (`code-vs-docs.md`). D7 (revoke) and D9/D8 (transplant profile and escalation) are fixed, so Moment 3 and Transplant show the whole story.

| Macro | What it shows | Caption |
|---|---|---|
| Moment 1 | `OK`, clock +12, `SIM_SWAPPED_RECENT` | — |
| Moment 2 | `SIM_SWAPPED_RECENT` + `CALL_FORWARDING_SET` | — |
| Moment 3 | `watch_line(mom)`, the swap, the watcher's SMS in the feed (named "mom"), the revoke, the `SUPPRESSED_REVOKED` audit row, `NO_CONSENT` | — |
| Transplant | the Settings step, `watch_line(self)` → `OK`, `UNREACHABLE`, the partner's SMS (`escalation[0]`) after +20 min, the neighbour's (`escalation[1]`) after +15, then `OK` | **D15 remainder:** the line-holder's own copy of an `UNREACHABLE` alert would read "That person's phone…"; in this run it is withheld anyway (his line was SIM-swapped at +12) |
| any `NOT_BOUND` | the bind link as a QR code in pane 4 | **D1:** nothing texts the link. The QR code stands in for the SMS, and the caption says so. |

## 6. Configuration

| Variable | Local (compose) | Host (`make showcase-ui`) | ENV=aws (laptop) | |
|---|---|---|---|---|
| `ENV` | `local` | `local` | `aws` | shown in the environment pill; anything but `local` turns off pane 2, Reset and the macros (decision 2) |
| `TOWER_URL` | `http://tower-mcp:8000/mcp` | `http://localhost:8080/mcp` | `deploy/.env.aws` (Runtime URL) | |
| `TOWER_BEARER` | from `deploy/compose/.env` | the same, via `mk/vars.mk` | a JWT for Asish | Tower answers 401 without it |
| `MOCK_URL` | `http://mock-carrier:8443` | `http://localhost:8443` | empty | empty → pane 2 shows "not reachable from this laptop" |
| `MOCK_ADMIN_TOKEN` | from `deploy/compose/.env` (generated) | the same | — | `Authorization: Bearer` on the mock's mutating `/_admin` routes (G1) |
| `BINDING_URL` | `http://binding-page:8081` | `http://localhost:8081` | `deploy/.env.aws` (API Gateway) | |
| `ALERTS_URL` | `http://alerts:8082` | `http://localhost:8082` | empty | |
| `ALERTS_INTERNAL_BEARER` | `INTERNAL_BEARER` from `deploy/compose/.env` | the same | — | for `/internal/sent` (G3) |
| `DYNAMO_ENDPOINT` | `http://dynamodb-local:8000` | `http://localhost:8000` | empty (real DynamoDB, `AWS_PROFILE`) | mapped to `TOWER_DYNAMODB_ENDPOINT`: `Store.from_env` reads only that name. With an endpoint the client gets DynamoDB Local's placeholder credentials in code, never through `AWS_*` env vars (they would look like Bedrock credentials) |
| `TOWER_TABLE_PREFIX`, `AWS_REGION` | as compose | as compose | `deploy/.env.aws` | |
| `REF_AGENT` | `scripted` (decision 6) | `auto` | `auto` | `auto` = Bedrock when credentials resolve, else scripted |
| `BEDROCK_MODEL_ID` | `amazon.nova-micro-v1:0` | same | same | |
| `DEMO_UI_HOST`, `DEMO_UI_PORT` | `0.0.0.0` in the container, published on `127.0.0.1:8090` | `127.0.0.1`, `8091` (`SHOWCASE_UI_PORT`) | `127.0.0.1` | |
| `DEMO_UI_PUBLISHED_LOOPBACK` | `1` | — | — | the container must listen on `0.0.0.0` for Docker to forward to it; `1` says the only way in is a `127.0.0.1` publish (compose) or `kubectl port-forward` (kind), so no token is needed. Set only by those two files |
| `DEMO_UI_TOKEN` | — | — | — | required when `DEMO_UI_HOST` is not a loopback address and `DEMO_UI_PUBLISHED_LOOPBACK` is not `1` (decision 7): the UI refuses to start without it. Then every route except `/healthz` (the page, `/events` included) needs `?token=` once (it sets an HttpOnly cookie) or `Authorization: Bearer` |
| `FEED_POLL_S` | `2` | `2` | `5` | |
| `DEMO_ALERTS_SETTLE_S` | `2.5` | `2.5` | `0` | pause after a clock jump Alerts acts on (the Transplant macro's +20 and +15), so Alerts' scheduler sees it before the line changes again (06 §11.4). Shared with `make demo` (`ref_client.demo.alerts_settle_s`) |
| `DEMO_UI_SEED_PATH` | `/app/seed/seed.py` (copied into the image) | found from the checkout | — | the `make seed` module that Reset imports |

The UI holds no HMAC or KMS key and no carrier secret. It never prints `.env` values. `/healthz` reports the mode, `ENV` and which panes are live, not the settings.

## 7. Wiring

**ENV=local (default).** One compose service `demo-ui` on the `tower` network, published on `127.0.0.1:8090`, with the scripted agent: this is what CI and `make demo` exercise. The judge-facing run is the UI on the host with Bedrock, `make showcase-ui`, against the same compose stack, on `127.0.0.1:8091` so that it and the container can be open side by side (S1, 2026-10-09: both on 8090 made `make up && make showcase-ui` fail with "address already in use"; the alternative, stopping the compose container, would change what `make up` leaves running). If the port is taken, `make showcase-ui` says so and exits 2; `SHOWCASE_UI_PORT=<port>` picks another. Every pane works. The phone that scans the QR code reaches the binding page at `BINDING_PUBLIC_URL`, as in `showcase-binding`; it never reaches the UI.

**ENV=aws.** The same container, or `uv run`, on the laptop, with `deploy/.env.aws` loaded and `ENV=aws`. Nothing is created in the account. What a laptop can reach:

| Pane | Reachable from the laptop? | Result |
|---|---|---|
| 1 Conversation | Tower on AgentCore Runtime: yes, with a valid JWT (D21: with an empty discovery URL every call is 401). Bedrock: yes, with the `att` profile. | works as Asish only (decision 3): no persona switch. |
| 2 Carrier | No. `MOCK_URL` is empty (`scripts/render_env.py`), the ALB is internal and never forwards `/_admin` (`modules/mock_carrier/main.tf`). | off, with the reason on screen. Pane 2, Reset and the macros are local-only (decision 2): no SSM tunnel is planned. |
| 3 Feed: audit | DynamoDB with the `att` profile: yes | works (about 3 reads/s at 5 s polling) |
| 3 Feed: SMS | No (Lambda, SNS) | not shown. The SMS arrives on the real phone. The audit row with `message_ref` is the evidence. |
| 4 Binding | API Gateway: `/bind/*` and `/me` yes; `/_admin/*` 404 | QR code from `next_step.url` only. Grants and revoke happen on the resident's own phone at `/me` (`routes/grants.py`), never from the UI. |

## 8. What it must never do

1. **Decide anything.** It imports nothing from `tower_policy` or `camara_client` and calls no CAMARA path. Outcome chips come only from Tower's `reason_codes` in the `ToolResult` and from the audit; the pass/fail badge compares those codes with the story's expected codes and decides nothing about the line.
2. **Call the carrier, or the carrier gateway.** Its only routes to the mock are under `/_admin`, which is a simulation aid and not CAMARA. It holds no Gateway URL or token.
3. **Show a phone number, or handle one.** Lines are shown by holder ("Asish", "Mom") and by the mock's opaque `ref`. The UI reads `/_admin/state?view=refs` and fires events by `ref` (G2), so no request it sends and no response it reads carries a number. As defence in depth, every HTML fragment and SSE frame goes through the number filter (`demo_ui.redact`) before it leaves the process. It masks with the shared phone-number rule `ref_client.privacy` (decision 1, 2026-10-11), the same rule the privacy tests' `phone_hits` uses: (a) `+` followed by 8 or more digits, with spaces, dots, dashes or parentheses between digit groups, is always a hit; (b) a bare run of 10 or more digits is a hit unless the *whole* token around it (letters, digits, `:.+-`) is on `DATE_TIME_ALLOWLIST` — ISO-8601 with or without separators, `T`, `Z` or an offset (`2026-10-05T14:12:00Z`, `20261005T141200Z`, `20261005141200`; without `T` the seconds are required) or 13-digit epoch milliseconds starting with `1`. Epoch seconds are not allowed (10 digits is a North-American national number), and a number glued to or beside a date is still a hit. On top of the rule, `GROUPED` masks bare North-American groupings (`(613) 555-0101`, `613.555.0101`, `1 613 555 0101`). Uvicorn runs with `access_log=False`; log lines carry event names and counts, never bodies.
4. **Write consent.** Grants and revokes go through the binding page (`/_admin/grants`, then `tower_consent`), which records them. The one direct store write is Reset, which reuses the `make seed` code (decision 5).
5. **Read another person's audit.** Only `list_for_line` as the line's owner. Pane 4 asks the binding page for `?table=Grants`, so no other table (and no Audit row) reaches the process: the build-log finding of the demo UI review (pane 4 received every line's Audit rows through `/_admin/tables`) is resolved (2026-10-11, `test_app.py::test_feed_tick_renders_every_pane_without_a_number`, `test_real_backends.py::test_revoke_from_the_grants_pane_flips_resolve_and_the_feed`).
6. **Speak first.** It has no way to make Alexa+ say anything. Proactive messages stay SMS from Alerts.
7. **Be deployed to AWS.** No ECR repository (`SERVICES` stays five for `push`; `build` adds the sixth image locally), no Terraform resource, `values-eks.yaml` `enabled: false`.
8. **Touch the hot path.** It is a client like the reference client. Tower's path still makes one consent read plus the carrier calls (and D2).
9. **Be depended on.** It is the only importer of `ref_client` outside the reference client, and nothing imports `demo_ui`: no product component (Tower, Alerts, the binding page, the mock, `packages/`) gains a model on its path through it (09 §4 "Who may depend on it"; `services/ref-client/tests/test_fallback.py`).

## 9. Failure behaviour

| Failure | Pane shows | Never |
|---|---|---|
| Tower 401 / unreachable | the `TowerError` text in pane 1; the macro stops | a made-up answer |
| No Bedrock | "scripted agent: tool calls from the script" in the pill and pane 1; free text is turned off | a hand-written answer standing in for the model |
| Mock `/_admin` 401 / 404 / unreachable | pane 2 greyed out with the reason; macros turned off | a fallback to CAMARA calls |
| Binding admin 404 (AWS, or `BIND_ADMIN` off) | pane 4: QR code from `next_step` only | a session forged to call `/me` |
| Alerts `/internal/sent` 404 / 401 / unreachable | "SMS list unavailable" in pane 3; audit rows go on | — |
| DynamoDB unreachable | a "feed paused" banner; the poller retries | — |
| Story codes ≠ expected | a red badge on that utterance; the run goes on (as `run_demo` records mismatches) | hiding the mismatch |

## 10. Gaps the build needed from other services (closed 2026-10-09)

| # | Service | Gap | As built |
|---|---|---|---|
| G1 | mock-carrier | No admin token; compose published `:8443` on every laptop interface | `MOCK_ADMIN_TOKEN` (08 §3): when set, `POST /_admin/scenarios/load`, `POST /_admin/lines/{line}/events`, `POST` and `DELETE /_admin/faults` and `POST /_admin/clock` need `Authorization: Bearer`. `GET /_admin/clock`, `GET /_admin/state`, `GET /_admin/scenarios` and `GET /_admin/sink` stay open (Tower's `TOWER_CLOCK_URL` and `alerts/clock.py` read the clock). Compose generates the token and publishes the mock on `127.0.0.1` only; `seed.py`, `ref_client.demo.Control`, the e2e helpers and the showcase scripts send it. Unset, the routes stay open: that is the in-process tests, kind and the Fargate task, where the network keeps `/_admin` private. |
| G2 | mock-carrier | `POST /_admin/lines/{msisdn}/events` took the raw number in the path | The path also takes the opaque `ref` (`state.line_by_ref`); a reply to a `ref` request has no `msisdn`. `GET /_admin/state?view=refs` returns lines keyed by `ref` without numbers, sink URLs or the sink inbox. `ref_client.demo.Control` uses both, so `make demo` no longer handles a number either. |
| G3 | alerts | No way to read sent SMS | `GET /internal/sent?after=<n>` (06 §3.1), behind the internal bearer, registered only with `ALERTS_MODE=local`. Returns `{n, at, template, role, user_id, body}`: the template id (`message_ref`), the rendered text and the recipient role (`watcher`, `line-holder`, `line-holder backup`, `escalation[n]`), never `to_e164`. |
| G4 | ref-client | `run_demo` always reset and reported only through text `echo` | `run_demo(..., reset=True, on_step=None)` (09 §4): `on_step` gets a `StepReport` per step (story, step id, kind, narration, expected and actual codes, `ok`, `elapsed_ms`, `replay`, the `Turn`); `reset=False` skips the scenario reload and only looks up the line refs. |
| G5 | deploy (aws) | No laptop path to the mock admin | Not built (decision 2): pane 2 and the macros are local-only. |
| G6 | tower-mcp (aws) | Identity comes from the JWT `sub`; `X-Tower-User` is ignored | Not built (decision 3): pane 1 is Asish only on AWS. |

## 11. Tests (for `tester`)

Built with the code: `services/demo-ui/tests/` (unit: config and token rules, redaction, the macro sequencer over fake controls, view rendering; integration: the app over ASGI with fake mock / binding / Alerts transports and moto for the audit). The fuller suite below is step 3 (2026-10-09). Its files are `test_macros_make_demo.py` (macros against golden, over the ref-client stack), `test_real_backends.py` (the real mock with its token, the real binding page, wire privacy), `test_served.py` (uvicorn + Playwright: panes, a macro over SSE, the phone layout, two tabs and one poller, the SSE token, log privacy), `test_static.py`, and `tests/aws/test_terraform_static.py::test_demo_ui_is_never_deployed_to_aws`. In-process, Reset is `Control.reset`. The real `seed.reset` runs only against a live stack (`make showcase-ui`).

- **Privacy:** drive every pane against a mock state whose keys are the demo numbers. Grep every HTML response and SSE frame with `E164_STRICT`: zero hits. The uvicorn log, too. **And the wire:** every request the UI sends to the mock (path and body) and every response it reads from it (`/_admin/state?view=refs`, event replies) carries no E.164 — the UI never sends or receives a raw number (G2).
- **No second policy path:** an import-graph test (no `tower_policy`, no `camara_client` under `services/demo-ui`), and a grep for CAMARA path prefixes and the Gateway URL variables. Outcome chips render only codes present in the `ToolResult`. The existing `services/ref-client/tests/test_only_llm_call.py` still passes with the new service in the tree, and `test_fallback.py::test_nothing_depends_on_the_reference_client` (fixed against 09 §4 on 2026-10-09: the product may not import `ref_client`; the demo UI may, and nothing may import `demo_ui`).
- **Macros = `make demo`:** with `ScriptedAgent` against in-process Tower and the mock (the ref-client test seams), each macro's step reports equal `STORIES` and `tests/e2e/golden/*.json`. Moment 2 run alone replays Moment 1 first.
- **Owner-only audit:** the feed reads only through `list_for_line` with the owner id. A test passes a watcher id and expects `AuditAccessDenied`, which the feed never triggers.
- **ENV=aws degradation:** with `ENV=aws`, `MOCK_URL=""` and binding `/_admin` answering 404, pane 2, Reset and the macros are off with the reason, pane 1 offers Asish only, pane 4 shows only the `next_step` QR code, and the UI makes no request to an empty URL.
- **Tokens:** a non-loopback `DEMO_UI_HOST` without `DEMO_UI_TOKEN` refuses to start; with it, `/` and `/events` answer 401 without the token. The mock's mutating `/_admin` routes answer 401 without `MOCK_ADMIN_TOKEN` when it is set (08 §6).
- **Never on AWS:** a static test in `tests/aws/`: no `demo-ui` in Terraform, in `SERVICES` for `push`, or enabled in `values-eks.yaml`.
- **One feed:** two browser tabs, one poller (count the DynamoDB calls per tick).
- **Presentation (prompt 21):** `test_assets.py` (unit). No external URL in `templates/` or `static/`; `static/` is ≤ 600 KB; every `ReasonCode` in `tower_policy.codes` has an `.rc-<CODE>` rule in `app.css` (the test may import `tower_policy`, the UI may not); no `\+?\d{10,15}` match in `static/img/*.svg`; a font `LICENSE` sits beside the woff2 files; every `STORIES` entry runs as `user-asish` (otherwise the "A" on macro bubbles would be wrong). `test_visual.py` (Playwright, skipped without a browser) takes the five states at 1440×900 into `artifacts/screenshots/demo-ui/` and compares them with `services/demo-ui/tests/visual/baseline/*.png` (0.5 % of pixels, a pixel differing when any channel differs; the repo has no top-level `tests/visual/`), with no request off `127.0.0.1` in the network log and reduced motion on as one variant (no transient Stage class, no floating chip, the same settled picture). A missing baseline is written and the test skips; `DEMO_UI_UPDATE_BASELINES=1` rewrites them. **Layer `nightly` (decided 2026-10-11):** `test_visual.py` is not part of `make test-integration`; it runs in `make test-nightly` or with `-m nightly`. Its baselines are local (`services/demo-ui/tests/visual/baseline/` is gitignored), because they are tied to the renderer that wrote them; the README's pictures are copies in `docs/img/demo-ui/`. Each shot is taken under emulated `prefers-reduced-motion: reduce` once the page is quiet, after `document.fonts.ready` and network idle; a capture over 0.5 % is retaken and compared once more at the same limit (no wider tolerance). The stepper-fold check (Moment 2 after Moment 1 in one page) is behaviour, not pixels, and stays `integration` (`test_stepper_replay.py`). Its axe-core check runs when `AXE_CORE_JS` names a local `axe.min.js`, injected from disk (no CDN, no npm), and skips otherwise. `test_fragment_privacy.py` renders all twelve fragments with demo numbers in every field, `data-*` and `title` included (the wire sweeps never reach `said.html` or `unavailable.html`). The existing tests pass unchanged.

## 12. Showcase on its own

`make showcase-ui`: needs `make up`. It runs the UI on the host on `http://127.0.0.1:8091` (`SHOWCASE_UI_PORT`; the compose container keeps 8090), with Bedrock when credentials resolve and the scripted agent otherwise; the pill says which. Press Moment 1, 2 and 3, then Transplant. Each step's badge is green. The feed shows the watcher's SMS, then the `SUPPRESSED_REVOKED` row, then the partner's and the neighbour's texts. Scan the QR code with a phone on the same Wi-Fi. The compose container (`http://127.0.0.1:8090` after `make up`, scripted) shows the same. What it proves: the same transcripts as `make demo`, live, in one view. It proves nothing the other showcases don't already prove.

## 13. Decisions (taken 2026-10-09)

1. G1: `MOCK_ADMIN_TOKEN` on the mutating `/_admin` routes; GET clock and state stay open; compose binds the mock's published port to `127.0.0.1`.
2. G5: no SSM tunnel. On ENV=aws pane 2, Reset and the macros are off, with the reason on screen.
3. G6: no second JWT. Asish only on AWS.
4. G3: the Alerts sent-SMS endpoint, local only (`ALERTS_MODE=local`), behind the internal bearer, never a number.
5. Reset reuses the `make seed` code path, Watches and `AlertsState` included.
6. Compose runs the scripted agent (CI and `make demo`); the judge-facing run is the host UI with Bedrock (`make showcase-ui`); mode and `ENV` are in the environment pill.
7. Bind `127.0.0.1` by default; `DEMO_UI_TOKEN` is required for any other address and also protects `/events`.
8. Transplant ships with no D9 caption: D9/D8 are fixed.
9. G4: a hook in `run_demo` (`on_step`, `reset=`), not a loop owned by the UI.
10. Row 11 is in the README's components table.
