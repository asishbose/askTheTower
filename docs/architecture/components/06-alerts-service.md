# 06 — Alerts Service

**Role:** the proactive path. Alexa+ cannot speak first, so when a watched line changes, the consented second person is told by SMS or push from here.
**Runs on:** Lambda (webhook receiver + poller + sender), EventBridge Scheduler (polls), SNS (SMS/push), DynamoDB `Watches` (state).
**Shares:** the policy engine (03) and the consent library (04) with Tower, as the same packages.

---

## 1. Two triggers, one evaluation

```
 Carrier event (CAMARA subscription webhook) ──┐
                                               ├──▶ fetch facts ──▶ policy.evaluate ──▶ compare with last_state ──▶ notify? ──▶ SNS
 EventBridge schedule (poll, per Watch profile) ┘                                         │
                                                                                          └──▶ update Watches.last_state; audit
```

- **Subscriptions first.** `watch_line(enable=true)` registers a SIM Swap subscription and (for `care`/`transplant` profiles) a Reachability subscription with a per-line sink URL. Events arrive within seconds.
- **Polls as fallback.** Not every carrier (or sandbox) supports subscriptions, and subscriptions expire. A schedule per profile re-checks: `self` and `care` daily at 08:00 local plus every 30 min during the day for `care`; `transplant` every 5 min.
- Both paths converge on the same function, so an event and a poll can never disagree about what "changed" means.

## 2. What counts as a change

| Watch profile | Fact compared | Change when |
|---|---|---|
| `self` / `care` | `latest_sim_change` | newer than `last_state.sim_change_at` |
| `self` / `care` | `call_forwarding` | transitions to `unconditional` |
| `care` | `reachable` | `false` continuously for `UNREACHABLE_ALERT` (4 h, daytime window) |
| `transplant` | `reachable` | `false` continuously for 20 min |

"Continuously" means every observation in the window said `false`; one `true` resets the clock. The clock lives in `Watches.last_state`, not in memory, so a Lambda cold start can't forget it.

## 3. Who gets told, and how

```python
for recipient in watch.escalation:       # ordered: [watcher, then e.g. neighbour]
    send(recipient, template(outcome))   # SNS → SMS (E.164) or push
    if recipient.requires_ack and not acked_within(ESCALATE_NEXT := 15 min):
        continue                          # next in chain
    break
```

- The **line-holder is always told too** (SMS to `Users.alert_phone_e164`, which defaults to the bound line) — except on a SIM swap, where that number may now be the attacker's. Then the line-holder is told via the backup phone they registered, if any, and otherwise only the watcher is told. This is the one case where the system deliberately does not text the line it's about.
- On the **line-holder's own** Watch, every step that is not the line-holder is a contact: their `watch` grant is re-read just before their text (skipped `SUPPRESSED_REVOKED` if gone, next step at once), and their SMS names the line by their own grant alias (§11.2). A parked chain carries a snapshot of the steps still to go (§11.3).
- Acknowledgement is a reply to the SMS ("OK") or a tap on a link. No ack path → treated as not acked.
- **A reply or cancel from a line that was SIM-swapped in the last 24 h is ignored and audited as `ACK_IGNORED_SWAPPED_LINE`** — it may be the attacker holding the number. Escalation continues as if unacknowledged, and the watcher's next message says the reply was ignored (the one sentence not from 03 §4 — `ACK_IGNORED_SWAPPED_LINE` is audit-only there — lives in `services/alerts/src/alerts/templates.py`). The 24 h window is `ACK_DISTRUST` in `thresholds.yaml`.
- Messages never contain a phone number, a location, or a health word. Templates are the same ones Tower uses (03 §4), in their SMS-length form.

### 3.1 Sent SMS (`GET /internal/sent`; doc 11 G3, G7; U1 2026-10-11)

Every SMS that `send.send_one` delivers is recorded through one Protocol, chosen once in the composition root (`runner.build_service`); `send.py` calls `svc.sent_log.record(...)` unconditionally and never asks which mode it is in:

```python
class SentLog(Protocol):                       # alerts/sent_log.py
    def record(self, *, at: datetime, template: str, role: str, user_id: str, body: str) -> SentEntry: ...
    def after(self, n: int, limit: int = 200) -> list[SentEntry]: ...   # entries with entry.n > n, ascending
```

| Implementation | Chosen when | Store |
|---|---|---|
| `MemorySentLog` (today's class, renamed) | `ALERTS_MODE=local` (compose, kind) | in memory, last 200 |
| `DynamoSentLog` | `ALERTS_MODE=lambda\|k8s` and `ALERTS_ADMIN=1` (Terraform sets it only while `demo_admin_cidrs` is non-empty, 10 §2.1) | table `SentSms` (§7) |
| `NullSentLog` | `lambda\|k8s` without `ALERTS_ADMIN` (the default) | nothing; `after` → `[]` |

The routes `GET /internal/sent?after=<n>` and `POST /internal/reset` (§3.2) are registered when `ALERTS_MODE=local` **or** `ALERTS_ADMIN=1` (`Settings.admin_routes`); otherwise they do not exist (404), on Lambda behind the existing `ANY /internal/{proxy+}` route too. **Bearer (decision 1, 2026-10-11):** both take only the admin bearer, `Settings.admin_routes_bearer`: `DEMO_ADMIN_BEARER` when it is set (Terraform sets it on the Alerts Lambda only while the demo admin path is open, 10 §2.1), and then the internal bearer is a 401 there; without it, local mode keeps `INTERNAL_BEARER` (compose, the in-process tests); any other mode without it answers 503 (fail closed). `/internal/watch` and `/inbound/sms` keep `INTERNAL_BEARER` and refuse `DEMO_ADMIN_BEARER`. Each entry is `{n, at, template, role, user_id, body}`: `n` counts from 1 so a reader asks only for what is new; `at` is the (mock) time of the send; `template` is the `message_ref`; `role` is `watcher` (a grantee's own Watch), `line-holder` or `line-holder backup` (the copy of §3), or `escalation[n]` (step *n* of the line-holder's chain, §11.2), with ` backup` added when a step's backup phone was used; `body` is the rendered text. The number is not a field and cannot be returned. The demo UI's live feed reads it.

**A failed ledger write never fails the alert (human decision 2026-10-11).** `record` runs only after the SMS was delivered. If it raises (store down, throttled), Alerts logs one line with the recipient's role and the template id (never the number, the body or the error text), counts `sent_log_failed`, appends one audit row `ALERT_LOG_FAILED` (outcome `ok`, `message_ref` = the SMS's template, after the alert's own `changed` row), and carries on: the SMS is not re-sent, the alert still counts as sent (`last_alert_at`, the rate limit), the chain goes on as after any delivered SMS, and the Lambda invocation succeeds.

**Cursor on DynamoDB.** DynamoDB has no auto-increment, and the local contract is an integer `n` from 1 with `after=<n>`. `DynamoSentLog.record` keeps it: one `UpdateItem` on the counter row `{pk:"sent", n:0}` with `ADD seq :1` (`ReturnValues=UPDATED_NEW`) gives the next `n`; then one `PutItem` of the entry `{pk:"sent", n, at, template, role, user_id, body, expires_at}` with `attribute_not_exists(pk)`. `after(n)` is one `Query` `pk = "sent" AND n > :n`, ascending, `Limit` 200; the counter row (`n = 0`) can never match because `after` is never negative. The wire shape and the reader are byte-for-byte the local ones. One consequence: two concurrent Lambdas can make `n = 8` visible before `n = 7`; the demo UI therefore asks from `max(0, last − 5)` and drops what it already has (11 §4). Cost: two writes per SMS, on the proactive path only, while the admin path is open; the hot path is untouched.

**Why gated and short-lived (U1 as built; see the build log).** Bodies are templated text that name what changed (03 §4), so a stored list of them is a history of a line beyond the audit, which 07 §3 and design rule 3 deliberately keep out of the product. The local ledger lives in process memory for a demo session; `SentSms` is its cloud equivalent for the demo window only: it exists only while `demo_admin_cidrs` is non-empty (closing the path destroys the table), entries expire after 24 h (`expires_at`), and nothing but `/internal/sent` reads it. **The body is kept on AWS** (U4, human decision 2026-10-11): the demo UI shows it as a mocked phone; the build log's "drop `body` on AWS" alternative was not taken.

### 3.2 Demo reset (`POST /internal/reset`; U3 2026-10-11)

The AWS counterpart of the Watches/`AlertsState` part of `make seed` (`deploy/compose/seed/seed.py`), for the demo UI's Reset on ENV=aws. Registered as in §3.1 (local or `ALERTS_ADMIN=1`), with §3.1's admin bearer (`DEMO_ADMIN_BEARER` on AWS, never the internal bearer).

- Body `{user_ids: [str]}`, 1–8 ids, each `[A-Za-z0-9_-]{1,128}` (Cognito subs on AWS, `user-…` locally); anything else 422.
- For every Line owned by one of those users (`Lines` `by_owner`): delete every `Watches` row on that line, and every `AlertsState` row keyed on it: `line#<line_id>`, the `sink#…` tokens it names, `esc#<line_id>#…`, `rl#<line_id>#…`, `ack#<line_id>`. Webhook dedupe rows (`evt#…`) are left to their TTL.
- Never touches `Users`, `Lines`, `Grants`, `Audit` or `SentSms`, and writes no audit row (a demo reset, not a resident's choice, like `watch-settings` `reset: true`, 04 §9.2). It does not unsubscribe at the carrier: the caller reloads the mock scenario first, which drops the mock's subscriptions.
- Answers `200 {lines, watches_deleted, alerts_state_deleted}` (counts only).

## 4. Re-resolve before sending

Immediately before any send, the service re-reads the Grant. A revoked grant means the alert is dropped and audited as `SUPPRESSED_REVOKED`. This is what makes "revocable any time" true for the proactive path, not just the request path.

Revocation also stops the *watching*, not just the sending. `tower_consent.revoke` disables the grantee's Watch on the line (04 §3). Alerts subscribes a line only for Watches that are enabled **and** still consented (owner, or an active `watch` grant — the same re-read as above), so a Watch whose grant is revoked counts for nothing even if its row still says enabled. On every poll the watchdog reconciles each line that has a Watch of that profile, enabled or not: if no enabled, consented Watch needs the line, its subscriptions are deleted and nothing is re-subscribed; if the kinds needed changed (a `care` grantee revoked, the owner's `self` Watch remains), it re-subscribes at once with only what remains (`sim-swap`). An event that still arrives after the revoke is audited `SUPPRESSED_REVOKED` and texts nobody. The binding page does not call Alerts on revoke; the subscriptions go within one poll interval.

## 5. Quiet hours and rate

- Fraud alerts (SIM swap, forwarding): **no quiet hours** — the point is speed.
- Reachability alerts: respect the profile's window (`care`: 08:00–22:00 line-holder local time).
- At most one alert per line per reason per 6 h (per watcher, when a line has several Watches); repeats go to the audit, not the phone. An atomic claim in `AlertsState` makes an event and a poll racing on the same change send once.

## 6. Webhook receiver

- One endpoint per subscription kind, per line: `POST /hooks/{kind}/{sink_token}`.
- `sink_token` is unguessable and maps to exactly one `line_id`; a token that doesn't resolve returns 200 and is logged — never a 404 that would confirm existence.
- Payloads are validated against the vendored CloudEvents/CAMARA schemas; anything else is dropped and counted.
- Idempotent on `event_id`: a conditional put (`attribute_not_exists`) of SHA-256(sink token, CloudEvents `source`, `id`) into `AlertsState` with a 7-day TTL; a duplicate gets 200 and is dropped.

## 7. State

`Watches.last_state` is the only *observed* state: `{sim_change_at, cf_status, reachable, unreachable_since, last_alert_at{reason: ts}, at}`. Small, per line, overwritten each evaluation, and moved only by a complete observation (so Tower can trust its `at`).

Bookkeeping that must not move `last_state.at` lives in one small Alerts-owned table, `AlertsState` (PK `pk`, TTL `expires_at`): sink token → `line_id`, subscription ids and miss counter per line, webhook dedupe keys, rate-limit claims, escalations in flight and ack routes. No key or value is a phone number. (Built in prompt 10; see `services/alerts/README.md`.)

`SentSms` (§3.1; only while the demo admin path is open): PK `pk` (S, always `"sent"`), SK `n` (N), TTL `expires_at` (epoch s, wall time of the write + 24 h; see "Clocks"), no index, no PITR. Defined beside `MemorySentLog` in `alerts/sent_log.py` (`SENT_SMS`) and added to `tables.auto.tfvars.json` by `modules/dynamodb/generate.py` with `"demo_admin_only": true`, which the module honours. No key or value is a phone number.

**Clocks (human decision 2026-10-11; fixes build-log/22-ops.md R1).** Alerts has two clocks with two jobs. `svc.clock` (the system clock, or the mock's scenario clock with `ALERTS_CLOCK=mock`) is the only time any window compares against: the 6 h rate limit (`last_state.last_alert_at` and the claim's `sent_at`), the 15 min `ESCALATE_NEXT` (`esc.sent_at`), the 24 h `ACK_DISTRUST`, `SWAP_WINDOW` and `STALE`. `svc.wall` is the real time, injected by the composition root (`runner.build_service` passes `alerts.clock.wall_clock`; tests pass a fixed one), and it is used for one thing only: every DynamoDB TTL attribute Alerts writes (`expires_at` on `AlertsState` `evt#`/`rl#`/`esc#`/`ack#` and on `SentSms`) is `wall + horizon` (7 d, the rate window, 2 d, 2 d, 24 h). DynamoDB deletes by the wall clock, and so does DynamoDB Local 2.x (within about a second): a TTL computed from a scenario clock days behind deleted parked escalations before their tick. TTL is garbage collection, never a decision: a window still holds if its row's TTL has passed in wall time, and a row TTL removed early never changes an outcome (the rate limit is also held by `last_state`). The audit table's `ttl` is 07's (`ts` + 90 d, inside the hashed row), not one of these.

## 8. Failure behaviour

| Failure | Behaviour |
|---|---|
| Carrier unreachable on a poll | keep `last_state`, count the miss; after 3 misses, audit `CARRIER_ERROR`; never alert on absence of data |
| SNS send fails | retry once, then audit `ALERT_FAILED`; next recipient in chain is tried |
| Sent-SMS ledger write fails after a delivered SMS | log (role, template; no number, no body), count `sent_log_failed`, audit `ALERT_LOG_FAILED`; no re-send; the invocation succeeds (§3.1) |
| Subscription expired | poller notices (no events for > TTL/2), re-subscribes, audits — only for kinds an enabled, consented Watch still needs; none left (e.g. after a revoke) → unsubscribes instead, never re-subscribes (§4) |
| Duplicate event | dropped on `event_id` |

The rule again: a failure to observe is never reported as a change.

## 9. Tests

- Event and poll producing the same facts produce the same decision and at most one alert.
- Unreachable-window arithmetic: 19 min dark → nothing; 20 → alert (transplant); a single `true` at minute 10 resets.
- Revoke between evaluate and send → suppressed.
- Ack from the swapped line within 24 h → ignored, audited, escalation continues; the same ack from the watcher's phone → accepted.
- SIM-swap alert does not text the swapped line.
- Rate limit: second identical change within 6 h is audited, not sent.
- Webhook with unknown token → 200, no state change.
- Sent-SMS ledger (§3.1): a grantee's alert is `watcher`; the transplant chain is `line-holder`, `escalation[0]`, `escalation[1]`; `after=<n>` returns only newer entries; the route needs the bearer and is absent outside local mode **unless `ALERTS_ADMIN=1`**; no entry contains a number (`tests/test_sent_endpoint.py`).
- `SentLog` (§3.1): one suite run against `MemorySentLog` and `DynamoSentLog` (DynamoDB Local or moto): `n` counts from 1; `after(n)` is ascending and only newer; `limit`; no field holds a number; the counter row is never returned; `DynamoSentLog` sets `expires_at`; `NullSentLog.after` is empty; `build_service` picks each for its mode, and `send.py` has no branch on it (`tests/test_sent_log.py`).
- Ledger write failure (§3.1, §8): a failing `SentLog` → exactly one SMS, never to the swapped line, the `changed` row before it and one `ALERT_LOG_FAILED` row after it, `sent_log_failed` = 1, no number or body in the log, and an API Gateway webhook invocation answers 200 (`tests/test_sent_log_failure.py`).
- Clocks (§7): every TTL is `wall + horizon` whether the service clock is behind or ahead of the wall; the rate limit and `ESCALATE_NEXT` still hold after their row's TTL has passed in wall time (`tests/test_clocks.py`).
- Demo reset (§3.2): only the given users' lines lose their Watches and `AlertsState` rows; another user's line keeps both; `Users`, `Lines`, `Grants`, `Audit` untouched; bearer required; absent without local mode or `ALERTS_ADMIN=1`; 422 on 0 or 9 ids (`tests/test_reset_endpoint.py`).
- Admin bearer (§3.1, decision 1): with `DEMO_ADMIN_BEARER` set, `/internal/sent` and `/internal/reset` answer the internal bearer 401 and `DEMO_ADMIN_BEARER` 200, in every mode, and `/internal/watch` answers `DEMO_ADMIN_BEARER` 401; `lambda` without it → 503; local without it keeps the internal bearer (`tests/test_reset_endpoint.py`).

## 10. Showcase on its own

`make showcase-alerts`, with the mock carrier and a real phone number registered as the watcher: enable a watch, fire `POST /_admin/lines/<Mom's number>/events {type: sim_swap}` on the mock, and the phone buzzes within seconds — on camera. Then revoke the grant on the binding page, fire again, and show the audit line `SUPPRESSED_REVOKED` instead of a text. Finally the escalation, on the product shape: Asish's own Watch saved as `transplant` with contacts `[partner, neighbour]` (`set_watch_settings`, 04 §9), alerts on; advance the mock clock 20 min dark, see the first text; don't acknowledge; advance 15 min; the second contact is texted. See `testing-and-showcase.md` §2.6.

## 11. The line-holder's profile and contacts (resolves D8, D9; built 2026-10-09)

The line-holder sets the profile and an ordered list of contacts on the binding page (04 §9). Both are stored on their own Watch: `Watches{line_id, watcher_user_id = owner}.profile` and `.escalation[]`. This section covers what Tower and Alerts do with them. The rule from 03 §5 is unchanged: the policy says *whether*, and the Watch says *to whom*.

### 11.1 `watch_line` (Tower, 02 §2): arguments and description unchanged

| Call | Behaviour |
|---|---|
| `watch_line(self, true)`, an owner Watch exists (the page has saved settings) | `enabled=true`. `profile` and `escalation` are kept exactly as stored (the code already reads `existing.profile` / `existing.escalation`). Alerts gets `POST /internal/watch {…, profile: <stored>}`, so a `transplant` line gets the reachability subscriptions |
| `watch_line(self, true)`, no Watch | Today's default: `profile=self`, `escalation=[owner]` |
| `watch_line(self, false)` | `enabled=false`. Profile and contacts are kept for next time |
| `watch_line(alias, true)` by a grantee | Unchanged: the grantee's **own** Watch, `profile=care` by default, `escalation=[grantee]`. The line-holder's settings are neither read nor copied. A grantee cannot set a profile on a line they don't hold |
| `watch_line(*, null)` | Status, as today |

Result changes (02 §2):

- `facts` gains `profile`: `self | transplant | care | null`. It is the stored value, also when watching is off, and `null` when there is no Watch.
- The `enabled` summary has a second **fixed** string, used when the profile is `transplant` or `care`: "Alerts are on for that line. A text goes out if it's SIM-swapped, forwarded, or off the network too long." It has no interpolation and no digits.
- Contacts never appear in a `ToolResult`: no `user_id`, no alias, no number.

Cost: none on the hot path. `watch_line` already does one resolve and one Watch `GetItem`.

### 11.2 Sending down the line-holder's chain

`deliver()` and `start_chain()` (§3) run as built, plus these rules. Rules 1–2 apply to a Watch whose watcher **is the line-holder** (`ChainAlert.owner_chain` in `escalation.py`); a grantee's own Watch keeps §3 as it was (its watcher was re-resolved already, §4).

1. **Per-step consent.** Immediately before texting a chain step whose `user_id` is not the line-holder, Alerts re-reads that user's grant on the line (one `Grants` query; Alerts only, not the hot path). If there is no active `watch` grant, the step is skipped and audited `suppressed · SUPPRESSED_REVOKED` (`tool=alert`). The **next step is tried at once**, with no 15-minute wait for a step that can't be texted. This applies on the first send and in `tick`.
2. **Per-recipient name.** Each contact's SMS is rendered with **that contact's own grant alias** (the one the line-holder chose when granting: "asish's phone has been off the network since…"). The line-holder's own copy uses no alias ("Your phone…"). This is the D15 fix, limited to the chain. The alias comes from the grant read in rule 1, so it costs no extra read.
3. **Line-holder copy.** Unchanged (§3). The owner is not in their own chain, so they get their copy at `Users.alert_phone` (default: the bound line). After a SIM swap within `ACK_DISTRUST`, the copy goes to their backup phone or to nobody. The swapped number is never texted.
4. **`requires_ack`** is stored per step (04 §9.1: true except on the last step). A delivered step with `requires_ack` parks the chain, as today.

### 11.3 Escalation, ack, rate limit, revoke

- **Snapshot when parked.** The parked escalation (`AlertsState esc#…`) gains `remaining: [{user_id, requires_ack}]`, the steps after the one just texted. `tick` walks `remaining`, not the live `watch.escalation[step+1:]`. *As built,* the tick writes its `changed` row just before the first text it actually sends, so a tick whose remaining steps were all revoked writes only `SUPPRESSED_REVOKED` and clears the chain. A settings change or a contact removal therefore cannot shift indexes under an alert already in flight. Each step is still re-checked by rule 11.2.1 before its send. A row without `remaining` (in flight across a deploy) falls back to today's behaviour.
- **Ack.** Today's paths, no change:
  - "OK" or "CANCEL" from the texted contact's phone (the ack route saved at park) → accepted, chain cleared, later steps never texted.
  - A reply from the watched line itself → accepted, unless that line had a SIM swap within 24 h. Then it is ignored and audited `ACK_IGNORED_SWAPPED_LINE`, and the chain continues.
- **The ignored-reply note.** When the Watch's watcher is the line-holder, `ACK_IGNORED_NOTE` is appended to the **next chain step's** message. It does not go to the watcher, whose number is the swapped line. A grantee's Watch keeps today's rule: the note goes to the watcher.
- **Rate limit.** Unchanged: one alert per line, per reason, per Watch, per 6 h (§5). A second 20-minute dark spell within 6 h is audited `suppressed · UNREACHABLE`. No contact is texted and no chain starts.
- **Revoke (D7).** Revoking a contact's `watch` grant removes them from the stored chain (04 §9.4). If the alert is in flight, rule 11.2.1 skips them. The line-holder's Watch is never disabled by revoking someone else's grant. D7's disabling of the *grantee's own* Watch is separate and still applies.
- **Unchanged thresholds.** `UNREACHABLE_ALERT.transplant` 20 min continuous (one `true` resets), `ESCALATE_NEXT` 15 min, `ACK_DISTRUST` 24 h, `RATE_LIMIT` 6 h. All come from `thresholds.yaml`.

### 11.4 The transplant story in `make demo`

**Who is who.** The partner is `user-partner` and the second contact is `user-neighbour`. Each binds their own mock line through the real one-tap flow: simulated client ids `phone-partner` and `phone-neighbour`, on two new lines `+1 613 555 0103` and `+1 613 555 0104` in `scenarios/demo.yaml`. 08 §2 must be updated with the file. Their alert phone is their own bound line (the fallback in §3), so nothing is typed.

**Seed** (`deploy/compose/seed/seed.py`):
- creates and binds both users;
- Asish grants each of them `watch` under the alias `asish`, through `/_admin/grants`;
- does **not** set the profile.

**Story** (`ref_client/demo.py`). Before the first utterance, a new `Settings` control step calls `POST /_admin/watch-settings {owner_user_id: user-asish, profile: transplant, contacts: [user-partner, user-neighbour]}`, the same function the page's form calls. The transcript records it as a control. `Control.reset` sends `reset: true` for `user-asish`, so a re-run starts clean.

The `Note` lines drop "(make showcase-alerts)": the SMS log now shows `to=chain:user-partner` after the +20 advance and `to=chain:user-neighbour` after the +15. In the demo timeline Asish's line was SIM-swapped at +12 min (moment 1), so his own copy is suppressed (no backup phone). That is the swapped-line rule working, and it is expected.

**Pacing (fixed 2026-10-09, build-log/21.md).** The story jumps the mock clock +20 and +15 and brings the line back within a few real seconds, faster than the local scheduler's real cadence (a transplant poll every 5 s at `ALERTS_CLOCK_SCALE=60`), so no evaluation ever saw the line dark at +20 and neither contact was texted. Two changes, both needed: (1) Alerts' local scheduler wakes every tick interval, reads the clock once and runs each job that is due by its real interval **or** because the clock moved the job's simulated cadence since it last ran (`alerts.local.Scheduler`, `due`); a mock-clock jump is acted on at the next wake (≤ 1 s in compose). (2) The two transplant `Advance` steps are marked `alerts=True`; after them `Control.advance` waits `DEMO_ALERTS_SETTLE_S` (2.5 s on ENV=local, 0 elsewhere: on AWS and EKS Alerts runs on EventBridge/CronJob schedules no wait can match). `make demo` and the demo UI's Transplant macro share this code, so both show `escalation[0]` at +20 and `escalation[1]` at +35.

**Golden.** `tests/e2e/golden/transplant.json` keeps the same three utterances, tool calls and reason codes: `watch_line{self,true}` → `OK`, `is_reachable{self}` → `UNREACHABLE`, then `OK`. Only its narrative `control` entries change: one is added for the settings step. The comparator ignores controls. The SMS is asserted by the e2e test (acceptance criterion 24), not by the golden.

`make showcase-alerts` now uses the owner-Watch shape (built 2026-10-09): both contacts hold `watch` grants, the settings are saved with `set_watch_settings`, and each contact's text names the line "asish's phone…".

### 11.5 Acceptance criteria

**Consent library and binding page (unit + integration):**

1. An owner saves `transplant` with contacts `[partner, neighbour]`, both holding active `watch` grants. Expected:
   - the owner Watch row is `profile=transplant`, `escalation=[{partner, requires_ack: true}, {neighbour, requires_ack: false}]`;
   - `enabled=false` on a new row;
   - exactly one new audit row: `tool=watch_line, trigger=binding, outcome=ok, actor=owner`.
2. Refused with nothing written and no audit row:
   - a non-owner (403);
   - a contact holding only `reachability`, a revoked grant, or no grant;
   - the owner as a contact;
   - a duplicate contact;
   - 4 contacts;
   - `transplant` or `care` with no contact (422).
3. `self` with no contacts is accepted and stores `escalation=[]`.
4. Saving on an **enabled** Watch that has `last_state.unreachable_since` set:
   - keeps `enabled=true` and leaves `last_state` byte-identical;
   - makes Alerts `/internal/watch` get exactly one call with `enable=true` and the new profile;
   - with Alerts down, still answers 303 and keeps the settings.

   Saving on a disabled Watch makes no Alerts call.
5. `GET /me` shows the Watching card with the three behaviour labels. The contact selects list only active `watch` grantees. The page has no "transplant", no health word and no phone-shaped digits. Without a session it answers 401; without CSRF, 403.
6. Revoking the partner's `watch` grant has these effects:
   - the owner's chain becomes `[{neighbour, requires_ack: false}]`;
   - the partner's own Watch is disabled (D7, unchanged);
   - the owner's Watch stays enabled.
7. `/_admin/watch-settings` answers 404 unless `TOWER_ENV=local` and `BIND_ADMIN=1`. It applies the same validation as the form, and `reset: true` deletes the owner's Watch.
8. No log line from a settings save contains a number (the privacy grep, 04 §7).

**Tower `watch_line`:**

9. With transplant settings stored, `watch_line(self, true)` gives:
   - `enabled=true`, with profile and chain unchanged;
   - an Alerts call with `profile=transplant`;
   - `facts.profile == "transplant"`;
   - the fixed reachability summary;
   - no user id or alias in the result.
10. With no Watch: `profile=self`, `escalation=[owner]`, and `facts.profile == "self"`.
11. `watch_line(self, false)`, then `watch_line(self, true)`, gives back the same profile and chain.
12. `watch_line(mom, true)` by a grantee creates or updates only the grantee's Watch (`care`, `[grantee]`). Mom's Watch row is not read for settings and not changed.
13. `watch_line(self, null)` reports `facts.profile` while watching is off.

**Alerts (`services/alerts/tests`, mock clock):**

14. Owner Watch `transplant` with chain `[partner (ack), neighbour]`:
    - dark 19 min → no SMS;
    - dark 20 min → one SMS to `chain:user-partner`, whose body starts with the partner's alias ("asish's phone…");
    - the audit row `changed · UNREACHABLE` exists before the send;
    - the neighbour is not texted.
15. Dark 10 min, one `true`, dark 19 → nothing. Dark 20 from the new start → the partner's SMS.
16. No ack for 15 min → `chain:user-neighbour` is texted and audited `changed`. An "OK" from the partner's phone within 15 min instead → accepted (`ok · OK`), and the neighbour is never texted.
17. With the watched line SIM-swapped < 24 h ago, "OK" from that line:
    - is audited `ACK_IGNORED_SWAPPED_LINE`;
    - the neighbour is texted at +15 with `ACK_IGNORED_NOTE` appended;
    - nothing is sent to the swapped number.
18. With the partner's grant revoked after the first SMS, the neighbour is still texted at +15 (from the snapshot). With the neighbour's grant revoked instead, the tick writes `SUPPRESSED_REVOKED`, sends nothing and clears the chain.
19. Partner revoked but still in the stored chain (the second write failed) → at the 20-minute send:
    - the partner is skipped with `SUPPRESSED_REVOKED`;
    - the neighbour is texted at once, with no 15-minute wait.
20. A second 20-minute dark spell within 6 h → `suppressed · UNREACHABLE`. No SMS and no parked chain.
21. Contacts are reordered while a chain is parked → the tick follows the snapshot taken at park time.
22. No SMS body contains a phone number or a health word (`tests/helpers/patterns`).

**End to end (`tests/e2e`, `ENV=local`):**

23. `make demo` still matches the golden. The transplant story ends with `demo ok`.
24. The full path:
    1. Asish, the partner and the neighbour each bind with the one-tap flow.
    2. The partner and the neighbour create invite codes on `/me`.
    3. Asish grants both `watch` on `/me`.
    4. Asish saves `transplant` + `[partner, neighbour]` with the real form (session + CSRF).
    5. MCP `watch_line(self, true)` as Asish → `OK`, `facts.profile == "transplant"`.
    6. Mock: Asish unreachable. Advance 10 min, then `reachable`, then `unreachable` again.
    7. Advance 19 min → no `chain:` SMS within one real transplant-poll interval.
    8. Advance 1 min → within one real transplant-poll interval, exactly one SMS to `chain:user-partner` and none to `chain:user-neighbour`.
    9. Advance 15 min with no reply → within one tick interval, one SMS to `chain:user-neighbour`.
    10. Asish's audit rows, in order, are `watch_line/binding ok`, `watch_line/voice ok`, `alert changed UNREACHABLE` and `alert changed UNREACHABLE`.

### 11.6 Deliberately not done

- **Duplicate texts.** A contact who has also said "watch mom's line" has a Watch of their own, which is rate-limited separately. They can get two texts for one incident. A per-recipient dedupe is a follow-up.
- **Contacts without an account.** A contact who has not bound a phone of their own cannot be added.
- **Settings by voice.**
