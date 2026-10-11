# alerts

The proactive path (design: [`docs/architecture/components/06-alerts-service.md`](../../docs/architecture/components/06-alerts-service.md)).
Alexa+ cannot speak first, so when a watched line changes, the consented watcher is told by SMS from here.

What it does:

- **Two triggers, one evaluation.** CAMARA subscription webhooks (`POST /hooks/{kind}/{sink_token}`) and
  scheduled polls (`{profile}` from EventBridge Scheduler, or the local scheduler) both call
  `evaluate.evaluate(line_id, trigger, now)`: consent first → carrier facts → `tower_policy.evaluate_line` /
  `evaluate_reachability` → diff against `Watches.last_state` → windows → a `Decision`.
- **Release.** `send.deliver`: re-read the grant (revoked → `SUPPRESSED_REVOKED`), rate limit (one alert per
  line, watcher and reason per 6 h; repeats audited), audit row, then SMS. Templates are `tower_policy.phrase(form="sms")`.
- **Revocation stops the watching (06 §4).** Subscriptions are kept only for Watches that are enabled *and*
  consented (owner or an active `watch` grant). Each poll's watchdog also visits lines whose Watches of that
  profile are all off, unsubscribes what nothing needs, re-subscribes at once when the needed kinds shrank, and
  never re-subscribes after a revoke. An event after a revoke is audited `SUPPRESSED_REVOKED`.
- **Recipients.** The escalation chain on the Watch (default: the watcher) plus the line-holder at their alert
  phone — except after a SIM swap of the line: then the line-holder's backup phone if any, and nobody whose
  phone *is* the swapped line is texted.
- **Escalation.** A chain step with `requires_ack` waits 15 min for "OK"/"CANCEL"; no reply → next step. A reply
  from a line within 24 h of its own SIM swap is ignored, audited `ACK_IGNORED_SWAPPED_LINE`, and the watcher's
  next message says so (on the line-holder's own chain: the next contact's message, 06 §11.3).
- **The line-holder's chain (06 §11).** On the owner's own Watch every contact's `watch` grant is re-read just
  before their text (gone → `SUPPRESSED_REVOKED`, next step at once), each contact's SMS names the line by their
  own alias, and a parked chain carries `remaining` (the steps still to go) so a settings change can't shift it.
- **Failures.** A failed observation is never a change: `last_state` is kept, misses are counted, the 3rd
  consecutive miss writes one `CARRIER_ERROR` row. An SMS is retried once, then `ALERT_FAILED` and the next
  recipient. A sent-SMS ledger write that fails after a delivered SMS is logged (role, template), counted
  (`sent_log_failed`) and audited `ALERT_LOG_FAILED`; the SMS is not re-sent and the invocation succeeds (06 §8).
  Subscriptions silent for more than TTL/2 are renewed by the poller; `subscription-ended` renews at once.

What it doesn't do: decide anything `tower_policy` doesn't (no model), store or log a phone number (numbers
are decrypted only for the carrier call and the SMS send), or alert on the absence of data.

## Layout

| Module | |
|---|---|
| `evaluate.py` | `evaluate()`, `decide()` (pure), `watch_consent()`, `fetch_facts()` |
| `windows.py` | pure window arithmetic: continuous-false clock, 20 min / 4 h, daytime 08–22, rate limit, ACK_DISTRUST |
| `send.py` / `send_backends.py` | `deliver()`, recipients, `SmsSender` Protocol: `SnsSender`, `LogSender`, `WebhookSmsSender` |
| `escalation.py` | `start_chain`, `tick`, `handle_reply` |
| `subscriptions.py` | `subscribe` / `unsubscribe` / `watchdog`; sink URL per line and kind |
| `hooks.py` | webhook receiver: vendored CloudEvents schema, token → line, dedupe |
| `internal_api.py` | `POST /internal/watch` (Tower's `watch_line`), `POST /inbound/sms` (local reply relay); with `Settings.admin_routes` (local mode or `ALERTS_ADMIN=1`): `GET /internal/sent?after=<n>` (the sent-SMS ledger, 06 §3.1; the demo UI's feed) and `POST /internal/reset` (06 §3.2), both behind the admin bearer: `DEMO_ADMIN_BEARER` when set (AWS, demo admin path open; then the internal bearer is a 401 there), else `INTERNAL_BEARER` in local mode only, else 503 |
| `sent_log.py` | the `SentLog` Protocol and its three implementations (`MemorySentLog`, `DynamoSentLog` on `SentSms`, `NullSentLog`): `{n, at, template, role, user_id, body}`, never a number |
| `demo_reset.py` | `reset_users`: the demo reset behind `POST /internal/reset` (Watches + line-keyed `AlertsState` rows of the given users' lines; counts only; no audit row) |
| `runner.py` | `process_line`, `poll`, `build_service` |
| `handler.py` | Lambda entry: API Gateway, Scheduler `{profile}`, SNS inbound, direct `{"action": "watch"}` |
| `local.py` | FastAPI app + in-process scheduler (`python -m alerts.local`) |
| `state.py` | the `AlertsState` table (sink tokens, dedupe, rate-limit claims, escalations, misses) |
| `templates.py` | `tower_policy.phrase(form="sms")` + the one ack-ignored sentence |
| `schemas/*.cloudevents.json` | generated from `specs/camara/` by `scripts/vendor_schemas.py` (`--check` in tests) |
| `testing.py` | `FakeCarrier`, `World`, in-process wiring helpers (tests and showcase only) |

## State

`Watches.last_state` (`sim_change_at, cf_status, reachable, unreachable_since, last_alert_at, at`) moves only
on a complete observation, so Tower's stored-state path (02 §4) can trust its `at`. Everything else lives in
one small Alerts-owned table, **`AlertsState`** (PK `pk`, TTL `expires_at`; definition
`alerts.state.terraform_definition()` for Terraform):

| `pk` | Holds |
|---|---|
| `sink#<token>` | sink token → `line_id` |
| `line#<line_id>` | sink token, subscription ids, `subs_at`, `last_event_at`, `misses` |
| `evt#<digest>` | webhook dedupe (7-day TTL) |
| `rl#<line>#<watcher>#<code>` | rate-limit claim (6-h TTL) |
| `esc#<line>#<watcher>` | escalation in flight; every write conditional on the `chain_id` + `version` read (single owner, 06 §5) |
| `ack#<phone line_id>` | which escalation a reply from that phone acknowledges |

**Sent SMS (06 §3.1, U1/U4).** `send.send_one` always calls `svc.sent_log.record(...)`; which `SentLog` it is
is decided once in `runner.build_sent_log`: `ALERTS_MODE=local` → `MemorySentLog` (last 200, in memory);
`lambda`/`k8s` with `ALERTS_ADMIN=1` → `DynamoSentLog` on the `SentSms` table (PK `pk` = `"sent"`, SK `n`; a counter
row `n = 0` with `ADD seq :1` gives the next `n`, then a conditional `PutItem`; `after(n)` is one ascending `Query`;
TTL `expires_at` = wall time of the write + 24 h); otherwise `NullSentLog` (keeps nothing). Terraform creates `SentSms` and sets
`ALERTS_ADMIN=1` only while the demo admin path is open (`demo_admin_cidrs`, 10 §2.1). The body is kept on AWS for
the demo window only (decision U4, 2026-10-11): the demo UI shows it as a mocked phone.

**Demo reset (06 §3.2).** `POST /internal/reset {"user_ids": [...]}` (1–8 ids `[A-Za-z0-9_-]{1,128}`, internal
bearer): for every line those users own, deletes its Watches and its `line#`, `sink#`, `esc#<line>#…`,
`rl#<line>#…` and `ack#<line>` rows; `evt#` rows are left to their TTL; Users, Lines, Grants, Audit and `SentSms`
are untouched; answers `{lines, watches_deleted, alerts_state_deleted}`. The demo UI's Reset on `ENV=aws` calls it
after reloading the mock scenario.

**Dedupe design.** Idempotency is a conditional `PutItem` (`attribute_not_exists(pk)`) of
`evt#SHA-256(sink token, CloudEvents source, id)` (hex re-lettered a–p) with a 7-day TTL — CloudEvents ids are
unique per source, and the token keeps two lines' sources apart. A duplicate is answered 200 and dropped.

## Run

```bash
uv run pytest services/alerts -q                     # moto + DynamoDB Local (when docker info works)
TOWER_DDB_BACKENDS=moto uv run pytest services/alerts -q
uv run python -m alerts.local                        # port ALERTS_PORT=8082 (mk/vars.mk ALERTS_URL); needs DynamoDB Local + mock carrier
make showcase-alerts                                 # §2.6, in-process; --fast to skip pacing
docker build -f services/alerts/Dockerfile -t ask-the-tower/alerts .
```

**Kubernetes** (`deploy/helm/alerts`, prompt 14): `ALERTS_MODE=k8s` runs the same FastAPI app (hooks + internal
API + health) without the in-process scheduler; the 10 §3 schedules are CronJobs running
`python -m alerts.job poll transplant|care|self`, `python -m alerts.job tick` and `python -m alerts.job reconcile`,
which hand the EventBridge payload to the Lambda dispatcher. The sender defaults to `sns` as in `lambda` mode
(kind sets `ALERTS_SENDER=log`); with `DYNAMO_ENDPOINT` set (kind) the tables are created at start-up as locally.

## Config

See `.env.example` and the table in `src/alerts/config.py`. The essentials: `ALERTS_MODE` (`lambda`|`local`|`k8s`),
`HOOKS_BASE_URL` (must be `https://` — CAMARA sinks are), `INTERNAL_BEARER`, `ALERTS_SENDER`
(`sns`|`log`|`webhook`), `SNS_TOPIC_ARN` (inbound replies topic), `SMS_GATEWAY_URL`, `ALERTS_CLOCK`,
`ALERTS_CLOCK_SCALE`, `CARRIER_*` (Alerts defaults: `CARRIER_PROFILE=proactive`, `CARRIER_CLIENT_ID=alerts`),
`DYNAMO_ENDPOINT` (alias of `TOWER_DYNAMODB_ENDPOINT`), `TOWER_ENV` + keys, `ALERTS_ADMIN` (`1`: serve
`/internal/sent` and `/internal/reset` outside local mode and record SMS in `SentSms`; off by default, set by
Terraform only while the demo admin path is open), `DEMO_ADMIN_BEARER` (the admin routes' only bearer when set;
Terraform sets it with `ALERTS_ADMIN=1`; decision 1 of 2026-10-11, 06 §3.1).

**`ALERTS_CLOCK_SCALE` and the mock clock.** Two different things move in a demo. *Time* — what `now` is —
comes from the clock: with `ALERTS_CLOCK=mock` every evaluation asks the mock carrier (`GET /_admin/clock`),
so the 20-minute transplant window, the 4-hour care window and the 15-minute escalation are measured on the
mock's scripted clock, the same clock that stamps the carrier's own `lastStatusTime`. *Cadence* — how often
the local scheduler wakes up to poll and tick — is the 10 §3 schedule divided by `ALERTS_CLOCK_SCALE`. Set the
scale to match how fast the mock clock is being moved: the showcase advances the mock one minute per real
second, and with `ALERTS_CLOCK_SCALE=60` the scheduler polls once per simulated poll interval, so "20 minutes
dark" takes 20 seconds on camera. A mismatch is safe, never wrong: a scale too high polls more often than
needed (same decisions — evaluation is idempotent and rate-limited), too low only delays an alert to the next
poll. The scale never changes a window; only the clock does.
One loop wakes every tick interval (`ALERTS_TICK_S / ALERTS_CLOCK_SCALE`), reads the clock once and runs each job
whose real interval has passed **or** whose simulated cadence the clock has moved since it last ran
(`local.Scheduler`, `local.due`). So a script that jumps the mock clock 20 minutes gets its transplant poll on the
next wake, not up to one real poll interval later, when the line may already be back (06 §11.4). A clock that goes
back (scenario reload) is a new baseline.

**Two clocks (06 §7 "Clocks").** Windows (rate limit, escalation, `ACK_DISTRUST`) compare only against
`svc.clock`. `svc.wall` (`alerts.clock.wall_clock`, injected by `runner.build_service`) is the real time and is
used only for DynamoDB TTLs: every `expires_at` is `wall + horizon`. DynamoDB Local 2.x enforces TTL by the wall
clock, so a TTL from the mock's scenario clock (days behind) deleted rows within a second. TTL never decides
anything (`tests/test_clocks.py`).

**On AWS with the demo admin path open** (10 §2.1): the public API routes `GET /internal/sent` and
`POST /internal/reset` (more specific than the ungated `ANY /internal/{proxy+}`) through the `admin_gate` CIDR
authorizer; the internal bearer is still required (401 without). Closed, both answer 404. Tests:
`tests/test_sent_log.py` (the one `SentLog` contract run against memory and DynamoDB, mode selection, no branch in
`send.py`), `tests/test_reset_endpoint.py`, `tests/test_sent_endpoint.py`.

## Showcase

`make showcase-alerts` (`scripts/showcase.py`) runs testing-and-showcase §2.6 locally, in one process: enable
the watch → admin `sim_swap` on Mom → the watcher's text is logged within the 5 s budget (Mom's own number is
not texted) → repeat within 6 h → audited only → revoke → `SUPPRESSED_REVOKED` → transplant: 19 min dark →
nothing, 20 → first text, +15 min without an ack → the second contact. The transcript is written to
`artifacts/transcripts/alerts-showcase.log`. On AWS the phone is real (SNS); that run is deferred.

## Wiring for other components

- Tower (`watch_line`): upsert the Watch, then `POST {ALERTS_INTERNAL_URL}/internal/watch` with
  `Authorization: Bearer $INTERNAL_BEARER` and `{"line_id", "enable", "profile"?, "watcher_user_id"?}`
  (or invoke the Lambda with `{"action": "watch", ...}`).
- Terraform: the `AlertsState` table (above); EventBridge Scheduler targets with input `{"profile": "<p>"}`;
  the SNS inbound topic subscription to the Lambda; API Gateway routes `/hooks/{kind}/{token}` and `/internal/watch`.
- Compose: the mock sends webhooks only to `https://` sinks, so a local compose stack needs TLS in front of
  Alerts (or the mock's loopback sink); prompt 12 decides.
