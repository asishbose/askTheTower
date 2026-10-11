# End-to-End Wiring

How the ten components connect, what crosses each edge, and the three paths a request can take. Read this before any single component document.

Diagrams: [`01-component-map`](diagrams/01-component-map.drawio) · [`02-request-path`](diagrams/02-request-path.drawio) · [`03-proactive-path`](diagrams/03-proactive-path.drawio) · [`04-binding-flow`](diagrams/04-binding-flow.drawio) · [`07-alexa-and-mcp`](diagrams/07-alexa-and-mcp.drawio) (where Alexa+ and the MCP server run, and every flow between them)

---

## 1. The map

```
                   ┌──────────────────────────── AWS ────────────────────────────────┐
  Alexa+ ──MCP──▶  │ Tower MCP server ──▶ Policy ──▶ Carrier gateway ──HTTPS──▶ Mock carrier │
  (simulator)      │   (AgentCore RT)      (lib)     (AgentCore GW+ID)          (Fargate)   │
                   │        │ ▲                              ▲                     │  ▲      │
  Ref client ──MCP─┤        │ └── consent lib ── DynamoDB ◀──┘                     │  │      │
  (Strands/Bedrock)│        └──── audit ─────▶ DynamoDB                            │  │      │
                   │                                                                │  │      │
  Phone ──HTTPS──▶ │ Binding page (Lambda) ── consent lib ── DynamoDB ── gateway ───┘  │      │
  (mobile data)    │                                                                   │      │
                   │ Alerts (Lambda) ◀──webhooks (CloudEvents)────────────────────────┘      │
  Phone ◀──SMS───  │   ▲      └── policy + consent libs ── DynamoDB                          │
  (watcher)        │   └──── EventBridge Scheduler (polls)                                   │
                   └──────────────────────────────────────────────────────────────────────────┘
```

Three libraries are shared as code, not called as services: **policy** (03), **consent** (04 client), **audit** (07 writer). Tower and Alerts import the same packages, so the request path and the proactive path can never decide differently.

## 2. Edges — what crosses each

| Edge | Protocol | Payload | Never |
|---|---|---|---|
| Alexa+ → Tower | MCP over Streamable HTTP, bearer | tool name, `line` alias, `enable`; user identity | a phone number, an SMS, anything from the phone |
| Tower → Alexa+ | MCP result | `summary`, `facts` (booleans, timestamps), `reason_codes`, `next_step` | a number, a location, a carrier error string |
| Web chat page → agent (09 §6) | HTTPS `POST /invocations`, `Authorization: Bearer <Cognito access token>`; on AWS through the Lambda URL proxy to the agent runtime | `{input, session_id}` → `{text, next_step, tool_calls[{name, reason_codes}]}` | a phone number (input with one → 422), `facts`, a URL not from Tower |
| Agent → Tower | MCP over Streamable HTTP, **the same bearer, byte for byte** (locally + `X-Tower-User`) | as Alexa+ → Tower | a token of its own, a number |
| Tower → DynamoDB | SDK | one `GetItem`/`Query` on consent; one `PutItem` on audit | — |
| Tower → Gateway | MCP tool call | `line_id` → decrypted number inside the call; `max_age` | credentials (Identity injects them) |
| Gateway → carrier | HTTPS, OAuth bearer | CAMARA request bodies | — |
| Carrier → Alerts | HTTPS webhook (CloudEvents) | subscription event with `subscriptionId`, type, device ref | — |
| Scheduler → Alerts | Lambda invoke | `profile` | — |
| Alerts → SNS → phone | SNS SMS | templated text, no number, no health words | the line's own number after a SIM swap |
| Phone → Binding page | HTTPS over mobile data | single-use token; carrier auth-code redirect | a typed phone number |
| Binding page → Gateway → carrier | as above | Number Verification `verify` | — |
| Tower / Binding page → Alerts | HTTP, bearer `INTERNAL_BEARER` | `POST /internal/watch {line_id, watcher_user_id, enable, profile}` — Tower on `watch_line`; the page after a watch-settings save on an *enabled* Watch (04 §9.2) | a phone number, a contact id |

## 3. Path A — the request ("Alexa, is my line OK?")

```
 1. Alexa+ picks line_is_ok from the description; sends {line:"self"} + identity
 2. Tower: identity → user_id (401 if absent)
 3. Tower: consent.resolve(user_id, "self") → {bound, grant:"owner", line_id}       [1 DynamoDB read]
       NOT_BOUND (only "self" with no Line) → return refusal with next_step.bind_line (binding URL); STOP
       an alias with no grant → NO_CONSENT, next_step ask_consent, no bind token, no audit row (D18); STOP
 4. Tower: if a Watch exists and Watches.last_state.at is within 10 min → facts from it (no carrier call; audit source=watch)
    else Tower → Gateway (parallel, 300 ms each): sim_swap_check(line_id, 72h), call_forwarding(line_id)
 5. Gateway: Identity injects the carrier bearer; HTTPS to mock or sandbox
 6. Tower: facts assembled; policy.evaluate_line(facts, consent, now) → outcome
 7. Tower: audit.append(...)                                                          [blocks the response]
 8. Tower → Alexa+: {summary, facts, reason_codes, next_step}
 9. Alexa+ speaks summary (or paraphrases it)
```

Budget: ≈300 ms p95 against the mock (02 §4). No model between steps 2 and 8.

**Web variant (prompt 20; 09 §6).** The web chat page and the reference-client agent stand in for Alexa+ in steps 1 and 9; steps 2–8 are identical.

```
 0. Page: Cognito Hosted UI sign-in (PKCE) → access token     (local: the sign-in stub, X-Tower-User + local bearer)
 1. Page → agent: POST /invocations {input, session_id} + Bearer   (no bearer → 401, no model call)
       AWS: page → Lambda URL (CORS, forwards 3 headers) → agent runtime (JWT authorizer)
 1b. Agent → Bedrock: picks line_is_ok(line="self")               (the model is in the client, as in Alexa+)
 1c. Agent → Tower: tools/call with the same bearer                → steps 2–8 unchanged
 9. Agent → page: {text, next_step copied verbatim from the tool result, tool_calls}
       page shows a bind link only under BINDING_BASE_URL/bind/; "tap it on your phone, then ask again"
```

Tower cannot tell the two entries apart, and does not need to: same tools, same identity (`sub`), same audit rows. Nothing is added to the hot path.

**Variants.** `is_reachable` is the same with one carrier call. `watch_line` skips 4–6: it writes a Watch, asks Alerts to (un)subscribe, audits, returns.

## 4. Path B — the proactive alert (carrier event or poll → SMS)

```
 1a. Carrier fires a SIM-swap CloudEvent to Alerts' sink URL for the line        (seconds)
 1b. or: EventBridge invokes Alerts with profile=care at the scheduled time        (fallback)
 2. Alerts: sink_token → line_id; load Watch (last_state, escalation, profile)
 3. Alerts → Gateway: fetch current facts (same calls as Path A step 4)
 4. Alerts: policy.evaluate_line(...) → outcome; compare with last_state → changed?
 5. Alerts: consent.resolve(watcher, alias) again                                   [revoked ⇒ SUPPRESSED_REVOKED; STOP]
 6. Alerts → SNS: templated SMS to escalation[0]; also to the line-holder's backup phone
       (never to the line's own number on a SIM swap)
 7. Alerts: Watches.last_state ← facts; audit.append(actor="system:alerts", trigger=event|poll)
 8. No ack in 15 min (where required) → escalation[1] ...
```

Alexa+ is not on this path at all. If Mom later asks her Alexa "is my line OK?", Path A runs and returns the same outcome from the same policy.

## 5. Path C — binding and granting (one tap on the phone)

```
 1. Any tool on an unbound line → Tower returns NOT_BOUND + binding URL; Alexa+ says "I've texted you a link"
       (the SMS goes via Alerts/SNS to the alert phone on the Users record; if none exists yet, Alexa+ reads a short code to type into the binding page instead)
 2. User opens the link on the phone; page says "turn Wi-Fi off"
 3. Page starts the carrier auth-code flow (network authentication: no password; the carrier recognises the data session)
 4. Carrier redirects back with a code; page → Gateway → number-verification/verify
 5. Carrier asserts the number; page stores Lines{line_id, owner_user_id, bound_at}
 6. Optional on the same page: grant `watch` or `reachability` to another user by invite code, with an alias; revoke any time
 6b. Optional: choose the line's watch profile (self / transplant / care) and up to 3 contacts in order, from the active `watch` grantees (04 §9; "watch my line" then uses them). Saved on the line-holder's own Watch, audited `watch_line`/`binding`; if alerts are already on, the page asks Alerts to re-subscribe for the new profile (`/internal/watch`)
 7. Page → Alerts: if a Watch was created, subscribe to the carrier's SIM-swap (and reachability) events
```

The mock simulates step 3's network attribution with a client-id header (08 §3). A browser cannot add a header to a redirect, so locally (`TOWER_ENV=local`, link carries `?as=phone-asish`) the page makes the authorize request itself with the header and continues at its own callback (`binding_page/mobile_data.py`). On a real carrier the attribution is the network's own; the parameter is ignored and the browser goes to the carrier.

## 6. Contracts, in one place

| Name | Owner | Shape |
|---|---|---|
| `ToolResult` | 02 | `{summary, facts, reason_codes[], next_step{kind,...}, checked_at}` |
| `Facts` | 03 | `{sim_swapped, latest_sim_change, call_forwarding, reachable, connectivity, last_status_time, fetched_at}` |
| `ConsentView` | 04 | `{bound, grant, revoked_at}` + `line_id` |
| `Outcome` | 03 | `ok \| changed \| refuse` + `reason_codes[]`; the audit additionally records `suppressed` |
| `Watch` | 04/06 | `{line_id, watcher_user_id, profile, last_state, subscription_ids[], escalation[{user_id, requires_ack}], enabled}` |
| `WatchSettings` | 04 §9 | `POST /me/lines/{line_id}/watch-settings {profile, contact_1..3}` → the owner's Watch `{profile, escalation}`; contacts are `user_id`s holding an active `watch` grant on the line, never numbers; `requires_ack` true on every step but the last; audited `watch_line`/`binding`; enabled Watch → `POST /internal/watch {profile}` |
| `AuditRecord` | 07 | `{line_id, ts, actor_user_id, tool, trigger, outcome, reason_codes, message_ref, policy_version, prev_hash}` |
| `CarrierClient` | 05 | the protocol in 05 §1; two implementations, one test suite |
| CAMARA payloads | 08 | the vendored specs; the mock serves `/openapi.json` to diff against them |
| Web chat → agent | 09 §6 | `POST /invocations` `{input: str 1–500, session_id: [A-Za-z0-9-]{33,128}}` + `Authorization: Bearer <Cognito access token>` → `200 {text, next_step: NextStep \| null, tool_calls: [{name, reason_codes[]}]}`; `401` no/malformed bearer (no model call) or Tower 401 · `422` invalid body or a number in `input` · `502 tower_unavailable` · `503 agent_unavailable`. `next_step` = the last non-`none` tool result's, verbatim; a `bind_line` outside `WEB_CHAT_BINDING_BASE_URL/bind/` → `null` + `NEXT_STEP_REJECTED` |
| Agent → Tower | 09 §6, 01 §4 | MCP `tools/call` with the caller's `Authorization` header unchanged (Tower verifies: `TOWER_JWKS_URL`, `TOWER_JWT_ISSUER`, `TOWER_JWT_CLIENT_IDS`; `user_id = sub`); `X-Tower-User` forwarded only with `TOWER_ENV=local` |

## 7. Environments

| | Local | AWS |
|---|---|---|
| Entry | reference client (09) CLI, web chat page on `127.0.0.1:8083` (sign-in stub), or MCP Inspector | Alexa+ web simulator, or the web chat page (CloudFront + Cognito → agent runtime, 09 §6) |
| Tower | container, local bearer | AgentCore Runtime |
| Carrier | `DirectClient` → mock container | Gateway + Identity → mock on Fargate (or sandbox by config) |
| Alerts | container, in-process scheduler, SMS → log | Lambda + EventBridge + SNS |
| Binding | container; phone on the same Wi-Fi *simulates* mobile data via the client-id header | Lambda + API Gateway; real phone, Wi-Fi off |
| Store | DynamoDB Local | DynamoDB |
| Demo UI ([11](components/11-demo-ui.md)): demo tooling, laptop only | compose container on `127.0.0.1:8090` (scripted agent), or `make showcase-ui` on the host on `127.0.0.1:8091` (Bedrock); all four panes | not deployed: the same container on the laptop with `deploy/.env.aws`. Conversation, audit feed and the `next_step` QR code work. The mock's `/_admin` and the binding page's `/_admin` cannot be reached, so the carrier controls and macros are off (local-only by decision), and pane 1 is Asish only. |

The component code is identical across the two columns; only wiring and hosting change. That is the claim the Helm charts exist to back. The demo UI sits outside every path in §3–§5. It is a client like the reference client, plus reads of `/_admin` and the audit, and it adds nothing to the hot path.

## 8. What never happens, anywhere on these paths

1. No model decides whether a call is allowed or what a fact means.
2. No subscriber phone number crosses the Alexa+ edge, or the web chat page → agent edge that stands in for it, in either direction.
3. No location is requested, returned, or stored.
4. No alert goes to the number that was just swapped.
5. No answer or alert is released before its audit row exists.
6. Alexa+ never speaks unprompted; every proactive message is an SMS or push from Alerts.
