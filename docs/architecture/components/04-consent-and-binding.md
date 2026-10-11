# 04 — Consent and Line Binding

**Role:** prove which phone number belongs to which user (binding), and record who may ask what about which line (consent).
**Runs on:** a small web app on Lambda + API Gateway (the binding page), DynamoDB (the store), and a library used by Tower (02) and Alerts (06).
**The one tap:** binding happens **on the phone, over mobile data**. It cannot happen on an Echo. This document says so everywhere it matters.

---

## 1. Why binding needs the phone

CAMARA Number Verification proves a number by observing the device's *cellular* data session (network-based authentication, or an OS-provided token). A request that arrives over Wi-Fi cannot be attributed to a line. So:

- The Echo asks Tower → Tower has no binding → Tower returns `NOT_BOUND` with a short-lived binding URL → Alexa+ says "I've texted you a link".
- The user opens the link **on their phone with Wi-Fi off** (the page asks for this; it can't detect it, and the carrier flow fails if Wi-Fi is on).
- The page runs the Number Verification flow; the carrier confirms the number; Tower stores the binding.
- One tap. Done once per line.

## 2. Binding flow

```
Phone (mobile data)            Binding page (Lambda)           Carrier (via Gateway)
   │  GET /bind/{token}  ─────────▶ │                                │
   │ ◀────── page: "turn Wi-Fi off, tap Verify"                      │
   │  POST /bind/{token}/verify ──▶ │                                │
   │                                │ ── auth-code / CIBA start ───▶ │
   │ ◀── redirect to carrier auth (network-auth, no password) ◀──── │
   │ ── carrier redirects back with code ───────────────────────────▶│
   │                                │ ── number-verification/verify ▶│
   │                                │ ◀── devicePhoneNumberVerified ─│
   │ ◀── "Line connected"           │  store Line{line_id, bound_at} │
```

- `token` is single-use, 10-minute TTL, tied to the `user_id` that asked.
- The number itself is **never typed** by the user; the carrier asserts it. This blocks binding someone else's number by knowing it.
- Consent model note (from the review): Tower is the **contracted API consumer**; the carrier's auth-code or CIBA flow is how the *user* consents to Tower asking about their line. AgentCore Gateway handles auth-code; CIBA needs custom code (05).

## 3. Grants — someone else's line

Mom binds her own line the same way (her phone, her tap). Then, on the same page, she can grant:

| Grant | Lets the grantee call | Alerts the grantee? |
|---|---|---|
| `reachability` | `is_reachable(alias)` | no |
| `watch` | `line_is_ok(alias)`, `is_reachable(alias)`, `watch_line(alias)` | yes — SMS when the line changes |

Grants are to a **named user** (by the Alexa-linked `user_id`, discovered via an invite code the grantee gets from their own binding page), with an **alias** Mom chooses ("Asish" sees her line as "mom"). Revocation is one tap on the same page. Revoking `watch` also **disables** (does not delete) the grantee's Watch on that line, in the same `revoke` call, right after `revoked_at` is set — other grantees' Watches are untouched, and revoking `reachability` changes no Watch (it never covered alerts). The line-holder's own Watch stays enabled; the revoked grantee is only removed from its contact chain (§9.4). The page makes no call to Alerts: the Alerts watchdog reconciles the line's carrier subscriptions against its enabled, consented Watches on the next poll and unsubscribes what nothing needs (06 §4). By voice, Mom can *see* who has grants (`watch_line` status) but not revoke — revocation stays on the page, deliberately, so it's never a misheard sentence.

Nothing else is grantable. There is no `owner` grant for another person; ownership is binding.

## 4. Store (DynamoDB)

| Table | PK / SK | Attributes | TTL |
|---|---|---|---|
| `Users` | `user_id` | `alexa_link_id`, `alert_phone_enc` (the alert E.164, encrypted), `created_at` | — |
| `Lines` | `line_id`; GSI `by_owner` (`owner_user_id` / `bound_at`) | `msisdn_enc` (KMS), `owner_user_id`, `bound_at`, `binding_method`, `carrier_hint` | — |
| `Grants` | `line_id` / `grantee_grant` = `grantee_user_id#grant`; GSI `by_grantee` (`grantee_user_id` / `line_id#grant`) | `alias`, `grant`, `granted_at`, `revoked_at` | — |
| `Watches` | `line_id` / `watcher_user_id`; GSI `by_profile` (`profile` / `line_id`) | `profile` (`care` / `transplant` / `self`), `last_state{sim_change_at, cf_status, reachable, unreachable_since, last_alert_at{reason: ts}, at}`, `subscription_ids[]`, `escalation[]`, `enabled` | — |
| `BindTokens` | `token` | `user_id`, `kind` (`bind` / `invite`), `created_at` | 10 min for `bind`, 24 h for `invite` (single use: consumed by the grant it makes) (`expires_at`, epoch s; expiry is also checked on read) |

The binding page's session is not a table: it is the signed cookie `atb_session` (§6), whose lifetime is `SESSION_TTL_S` seconds (default `1800`; `86400` on AWS through Terraform `binding_session_ttl_s` and on EKS through the umbrella `values-eks.yaml`, D-H). Its expiry is signed into the cookie, so no row expires with it.

`line_id` is an HMAC of the E.164 number under a KMS key, so tables never carry a raw number as a key. It is stored as `ln_` + the hex digest re-lettered `a`–`p`, so no `line_id` can ever match the phone-number regex. The table list, keys, GSIs, TTL and PITR flags live as data in `packages/tower-consent/src/tower_consent/tables.py`; Terraform is generated from it. The encrypted `msisdn_enc` exists because carrier APIs need the number in the request and SNS needs it to text the line-holder; it is decrypted only inside the carrier call path and the SNS send path, in memory, and never logged or returned.

## 5. Resolve

The one function Tower and Alerts call:

```python
resolve(user_id, line: "self" | alias) -> ConsentView + line_id
```

- `"self"` → the Line whose `owner_user_id == user_id`; grant = `owner`.
- alias → the Grant where `grantee_user_id == user_id and alias == line and revoked_at is None`; grant = its type. If only *revoked* grants carry that alias, the result is `bound=True, grant="none"` with `revoked_at` and the `line_id` set, so the engine answers `NO_CONSENT` (not `NOT_BOUND`) and the refused attempt is audited on that line. The query filters on `alias` only, so this is still one request.
- An alias with **no grant at all** (unknown, another grantee's alias, or not a valid alias — the last with zero requests) → `bound=True, grant="none"`, no `line_id`. The engine answers `NO_CONSENT` with `next_step = ask_consent`: no bind token is minted, because it is not the caller's own line that is unbound, and there is no line to audit (D18, built 2026-10-09).
- `"self"` with no Line → `bound=False`: `NOT_BOUND` and a bind link. This is the only path that mints a bind token.

In short: `"self"` with no Line → `bound=False`; an alias with no live Grant → `bound=True, grant="none"`. Policy refuses before any carrier call in both cases.

One `GetItem` or one `Query`; this is the single DynamoDB read on the hot path.

## 6. What the resident sees

- On the binding page: the lines they've bound, a **Watching** card per owned line (profile and contacts, §9.3), who they've granted what, one button each to revoke. The page session is a signed cookie `atb_session` set by a successful bind; every grant/revoke form also carries a CSRF token. Its lifetime is `SESSION_TTL_S` (default `1800`, 30 min; local, kind and tests). On AWS Terraform sets `86400` (24 h, D-H in [`../bind-and-alert-flows.md`](../bind-and-alert-flows.md)) through the variable `binding_session_ttl_s`, so the line-holder can come back to revoke or read the audit during the demo day without binding again. `SESSION_TTL_S` is the only session-lifetime setting (decided 2026-10-11; build log, Prompt 20, C3, and 22). Magic-link re-entry is roadmap.
- By voice: "Alexa, who can see my line?" and "who checked my line this week?" → `watch_line(line="self", enable=null)`, which carries grants and recent checks (02 §2, 07 §4).

## 7. Tests

- Binding with Wi-Fi on → page refuses with the right message (detected by the carrier flow failing, not by the browser).
- Token reuse, token expiry, token for a different user → all refused.
- Grant then revoke → `resolve` flips to `none` within one read; an in-flight alert for that line is dropped (06 re-resolves before sending).
- Alias collision (two grants to the same user with the same alias) → rejected at grant time.
- An alias with no grant → `NO_CONSENT`, no bind token, no audit row, no carrier call, for every tool (`services/tower-mcp/tests/test_d18_unknown_alias.py`).
- Raw number never appears in any log line or any tool result (grep assertion across captured output).

## 8. Showcase on its own

Open the binding page on a phone with Wi-Fi off against the mock carrier; bind; grant `watch` to a second test user; revoke. Each step shows in a small admin view of the tables. What it proves: the one tap is real, the grant model is real, and nothing is typed. See `testing-and-showcase.md` §2.4.

**Admin routes (local only).** `binding_page/routes/admin.py`, served only when `TOWER_ENV=local` **and** `BIND_ADMIN=1`; otherwise every `/_admin/*` path answers 404, whatever its query or body.

| Route | Does |
|---|---|
| `GET /_admin/tables` | every row of the six tables, redacted (`msisdn_enc` shortened, TTL epochs as times, bind tokens cut); HTML, or JSON with `?format=json`. `?table=<name>` (one of `Users`, `Lines`, `Grants`, `Watches`, `Audit`, `BindTokens`, exact case) returns only that table; any other name is 422. The demo UI's pane 4 asks for `?table=Grants` only, so it never receives another line's Audit rows (11 §3, §8.5; decided 2026-10-11) |
| `GET /_admin/resolve?user=..&line=..` | `tower_consent.resolve` as JSON |
| `POST /_admin/bind-tokens {user_id}` | a fresh bind link |
| `POST /_admin/grants {owner_user_id, grantee_user_id, grant, alias, action}` | grant or revoke on the owner's line, idempotent (`changed`) |
| `POST /_admin/watch-settings {owner_user_id, profile, contacts, line_id?, reset?}` | the `/me` watch-settings save as the line-holder (§9.2) |

## 9. Watch settings: profile and contacts (resolves D8, D9; built 2026-10-09)

The line-holder chooses, on the binding page and on their own phone, **how their line is watched** (the profile) and **who is texted, in order** (the contacts). "Alexa, watch my line" (`watch_line(self, true)`) then turns watching on with what is stored. Nothing new crosses the Alexa+ edge. `watch_line`'s arguments and description (01 §2) do not change, and no phone number is typed, stored or spoken.

### 9.1 Where it is stored

On the line-holder's **own** Watch row: `Watches{line_id, watcher_user_id = owner_user_id}`. There is no new table and no new attribute.

| Field | Value |
|---|---|
| `profile` | `self` (fraud signals only) / `transplant` / `care`, as in §4 and 06 §2 |
| `escalation[]` | ordered `[{user_id, requires_ack}]`, 0–3 steps. Each `user_id` is a **contact**. `requires_ack` is derived, never chosen: `true` on every step that has a successor, `false` on the last |
| `enabled` | **not** set by the page. The first save creates the row with `enabled=false`. Later saves keep whatever voice last set |

A **contact** is a reference, never a number. It is a `user_id` that holds an **active `watch` grant on this line**. The contact's phone is the one Alerts already uses for any user (06 §3: `Users.alert_phone_enc`, else that user's newest bound line). It is carrier-verified because the contact bound it themselves.

**How a contact consents.** The same way as any grantee (§3). The contact binds their own line on their own phone over mobile data, creates an invite code, and hands it to the line-holder, who grants `watch` with it. That code is the contact's consent to be texted about this line. A `reachability` grant does not qualify: it carries no alerts (§3 table). A person with no Tower account cannot be a contact (see 9.6).

### 9.2 Routes (page session + CSRF, like every `/me` route)

| Route | Does |
|---|---|
| `GET /me` | Adds a **Watching** card per owned line (9.3) |
| `POST /me/lines/{line_id}/watch-settings` form `{profile, contact_1, contact_2, contact_3, csrf}` | Validate → `tower_consent.set_watch_settings` → audit → if the Watch is enabled, `POST {ALERTS}/internal/watch {line_id, watcher_user_id: owner, enable: true, profile}` (re-subscribes the kinds for the new profile, 06 §1). An Alerts failure is logged and swallowed, as in `watch_line` (D24): the polls cover it. Then 303 → `/me` |
| `POST /_admin/watch-settings` JSON `{owner_user_id, profile, contacts: [user_id…], line_id?, reset?}` | **Local only** (`TOWER_ENV=local` and `BIND_ADMIN=1`, else 404), like `/_admin/grants`. Same save path as the form (validation, audit row, Alerts call); a refusal is 422. Used by `ref-client demo` and the e2e test. `reset: true` deletes the owner's Watch row (no audit row: a demo reset, not a resident's choice) so a demo re-run starts clean |

*As built:* the form's radio values are behaviour keys (`fraud` → `self`, `reachable` → `transplant`, `daytime` → `care`) so the page source never carries the profile's internal name (§9.3); the route also accepts the profile names themselves. Contact fields are read as `contact_1…contact_N` in order, so a fourth one is refused (422) rather than silently dropped. Code: `tower_consent/watch_settings.py`, `binding_page/watching.py` (save + card), `binding_page/routes/watch_settings.py`, `binding_page/alerts.py` (`ALERTS_INTERNAL_URL` / `ALERTS_INTERNAL_BEARER`; unset → log-only stub).

`set_watch_settings(store, line_id, acting_user_id, profile, contacts, now) -> Watch` enforces the following. Every refusal writes nothing.

| Refusal | Status | Page message (template holds the words) |
|---|---|---|
| no session / bad CSRF | 401 / 403 | as for grants |
| acting user is not the line-holder (`NotLineOwner`, `LineNotFound`) | 403 | "Only the line-holder can change how this line is watched." |
| `profile` not in the enum | 422 | — |
| a contact without an active `watch` grant on this line, the owner as a contact, a duplicate, or more than 3 | 422 | "Contacts must be people you've shared this line with (watch)." |
| `transplant` or `care` with no contact | 422 | "Pick at least one person to text." |

`self` with no contacts is valid. It is how the line-holder clears the settings. The ordered contacts, with blanks dropped, become `escalation[]`.

**Audit.** Each successful save appends one row on the line: `tool=watch_line, trigger=binding, actor=owner, outcome=ok, reason_codes=[OK]`, with no contact ids in the row. `recent_checks` already ignores `watch_line` rows (07 §2), so a settings change is not a "check".

### 9.3 What the page shows

On each owned line, the **Watching** card shows the following. The page never shows the word "transplant", and it never shows a number.

- **Alerts: on / off** (read-only; from `Watch.enabled`). Below it: "Say 'Alexa, watch my line' to turn alerts on or off."
- **Profile**, as a radio button labelled by behaviour:
  - "Fraud only: SIM swap or call forwarding" (`self`)
  - "Must stay reachable: text if off the network 20 minutes, any hour" (`transplant`)
  - "Daytime check-in: text if off the network 4 hours, 8 am–10 pm" (`care`)
- **Who gets the text, in order**: three selects, each listing this line's active `watch` grantees the way the grant card shows them (§6). Under them: "If the first person doesn't reply OK within 15 minutes, the next one is texted."
- Any stored contact who no longer has an active grant is listed as "no longer shared — skipped". Normally there is none, because revoke removes it (9.4).

### 9.4 Revocation and changes

- **Revoking a contact's `watch` grant** (§3, the existing button) also removes that `user_id` from the line-holder's `escalation[]` in the same request, after `revoked_at` is set. This is an addition to `tower_consent.revoke`'s `watch` branch, next to D7's `disable_watch`, which it does not replace. If the second write fails, Alerts still skips the contact at send time (06 §11.2).
- **Changing settings while alerts are on** takes effect at the next evaluation. `last_state` (and so the unreachable clock) is never touched by a save (`upsert_watch` already guarantees it). An escalation already in flight keeps its own snapshot (06 §11.3).
- **Turning alerts off** stays voice (`watch_line(self, false)`). The settings are kept for next time. To withdraw the settings, choose `self` with no contacts, or revoke the contacts.

### 9.5 Failure behaviour

| Failure | Behaviour |
|---|---|
| Store down on save | 503 page, nothing half-written: the Watch is one `UpdateItem`, written before the audit row |
| Audit append fails after the write | The page shows an error (500). The row is retried once, then counted (`deps.metrics["audit_failed"]`; there is no `AUDIT_FAILED` reason code). The settings stand, and an enabled Watch is still re-subscribed: they gate nothing on the hot path |
| Alerts `/internal/watch` fails | Logged, swallowed. The profile's poll schedule finds the Watch (06 §1) |

### 9.6 Deliberately not done

- No contact typed as a phone number, and no contact without their own binding and grant.
- No settings by voice, and no `profile` argument on `watch_line`.
- No per-contact ack choice.
- No settings on a line the user doesn't hold (a grantee's own Watch stays `care`, 06 §11.1).
- No page switch for `enabled`.

### 9.7 Tests

In `services/binding-page/tests` and `packages/tower-consent/tests`, these are acceptance criteria 1–8 in 06 §11.5: `packages/tower-consent/tests/test_d9_watch_settings.py`, `services/binding-page/tests/test_d9_watch_settings_page.py`.
