# 08 — Mock Carrier

**Role:** a CAMARA-conformant carrier that the demo runs against by default. Deterministic, scriptable, and indistinguishable from a sandbox to everything above the gateway.
**Runs on:** one container (FastAPI). Locally in compose; on AWS as a Fargate task behind an internal ALB with TLS.
**Built to:** the vendored OpenAPI specs under `specs/camara/`. Conformance tests are generated from those specs, not written by hand.

---

## 1. What it implements

| API | Endpoints | Notes |
|---|---|---|
| OAuth 2 | `POST /oauth2/token` (client credentials; also the auth-code and CIBA grants), `GET /oauth2/authorize` (auth code, network-auth simulated), `POST /oauth2/bc-authorize` (CIBA, optional) | scopes per API; a bad scope → 403 `PERMISSION_DENIED` |
| SIM Swap | `POST /sim-swap/v*/check` → `{swapped}`; `POST /sim-swap/v*/retrieve-date` → `{latestSimChange}` (nullable, per spec) | `maxAge` honoured; > 2400 h → 400 |
| SIM Swap Subscriptions | `POST/GET/DELETE /sim-swap-subscriptions/v*/subscriptions` | CloudEvents to `sink`; `expiresAt` honoured |
| Call Forwarding Signal | `POST /call-forwarding-signal/v*/unconditional-call-forwardings` → `{active}`; `POST .../call-forwardings` → `["unconditional", …]` (a bare array per the spec; `["inactive"]` when none) | |
| Number Verification | `POST /number-verification/v*/verify` → `{devicePhoneNumberVerified}`; `GET .../device-phone-number` | requires an auth-code token that the mock issues only to a "mobile-data" client (see §3) |
| Device Reachability Status | `POST /device-reachability-status/v*/retrieve` → `{reachable, connectivity, lastStatusTime}` | |
| Device Reachability Status Subscriptions | `POST/GET/DELETE .../subscriptions` | events on transitions |
| Errors | CAMARA error envelope `{status, code, message}` for every non-2xx | codes from the specs (CAMARA Fall25, Commonalities 0.6): `INVALID_ARGUMENT`, `OUT_OF_RANGE`, `UNAUTHENTICATED`, `PERMISSION_DENIED`, `NUMBER_VERIFICATION.USER_NOT_AUTHENTICATED_BY_MOBILE_NETWORK`, `NOT_FOUND`, `IDENTIFIER_NOT_FOUND`, `MISSING_IDENTIFIER`, `UNNECESSARY_IDENTIFIER`, `TOO_MANY_REQUESTS`, … (full list in `specs/camara/README.md`; Commonalities 0.4's `DEVICE_NOT_FOUND` / `UNIDENTIFIABLE_DEVICE` no longer exist) |

Exact paths and version segments come from the spec files. A judge can diff the mock's served `/openapi.json` against `specs/camara/*.yaml`.

## 2. Scenario engine

State is a set of **lines**, each with attributes the APIs read:

```yaml
# scenarios/demo.yaml
clock: "2026-10-05T14:00:00Z"
lines:
  "+16135550101":                # Asish
    sim_change_at: "2026-09-01T10:00:00Z"
    call_forwarding: none
    reachable: true
    connectivity: DATA
    mobile_data_client_ids: [phone-asish]   # who may network-authenticate as this line
  "+16135550102":                # Mom
    sim_change_at: "2026-08-12T09:30:00Z"
    call_forwarding: none
    reachable: true
    connectivity: DATA
    mobile_data_client_ids: [phone-mom]
  "+16135550103":                # Asish's partner — first contact in the transplant story (06 §11.4)
    sim_change_at: "2026-07-20T16:00:00Z"
    call_forwarding: none
    reachable: true
    connectivity: DATA
    mobile_data_client_ids: [phone-partner]
  "+16135550104":                # the neighbour — second contact in the transplant story
    sim_change_at: "2026-06-03T11:15:00Z"
    call_forwarding: none
    reachable: true
    connectivity: DATA
    mobile_data_client_ids: [phone-neighbour]
timeline:                        # optional scripted events, relative to clock
  - at: "+00:12:00"
    line: "+16135550101"
    event: sim_swap
  - at: "+00:20:00"
    line: "+16135550101"
    event: cf_set
```

- **Clock** is controllable: `POST /_admin/clock {now | advance_s}`. `latestSimChange` and `lastStatusTime` are computed against it, so "twelve minutes ago" is reproducible on any day. On AWS it starts at wall time instead (`MOCK_CLOCK=wall`, §3).
- **Timeline** events fire as the clock advances, and fan out to any active subscriptions as CloudEvents.
- **Seeded**: `scenarios/demo.yaml` is checked in; `make up` loads it and `make seed` reloads it. The demo is the same every run.

## 3. Admin API (not part of CAMARA, clearly namespaced)

The whole `/_admin` surface is mounted only when `MOCK_ADMIN=1`.

| Endpoint | Purpose | Token (G1) |
|---|---|---|
| `POST /_admin/scenarios/load {name}` | reset state to a scenario | yes |
| `POST /_admin/lines/{line}/events {event}` | `sim_swap`, `cf_set`, `cf_clear`, `reachable`, `unreachable` — fires subscriptions. `{line}` is the E.164 or the line's opaque `ref` (`line:<16 hex>`); a `ref` request gets a reply without `msisdn` | yes |
| `POST /_admin/clock` · `GET /_admin/clock` | set or advance · read (Tower's `TOWER_CLOCK_URL`, Alerts' mock clock) | POST yes · GET no |
| `GET /_admin/state` · `GET /_admin/state?view=refs` | full dump (tests) · the same without a number: lines keyed by `ref` with no `msisdn`, subscriptions without `sink`, no deliveries, no sink inbox (the demo tools read this one) | no |
| `POST /_admin/faults {kind, n}` · `DELETE /_admin/faults` | inject `timeout`, `500`, `429` for the next *n* calls — for the error-mapping and circuit-breaker tests · clear them | yes |
| `GET /_admin/scenarios` · `GET /_admin/sink` | list scenario files · the loopback sink's CloudEvents | no |

**Admin token (G1, doc 11 §10).** With `MOCK_ADMIN_TOKEN` set, every POST and DELETE above needs `Authorization: Bearer <token>` (401 otherwise); the reads stay open because Tower and Alerts read the clock. Compose generates the token (`deploy/compose/.env`) and publishes the mock on `127.0.0.1` only; the seed, `make demo` (`ref_client.demo.Control`), the e2e helpers, the showcase scripts and the demo UI send it. Unset, the routes are open: the in-process tests and kind, where the network keeps `/_admin` private (ClusterIP + NetworkPolicy). **The Fargate task always has it** (from 2026-10-11, U2): Terraform generates `random_password.mock_admin_token` and passes it as the ECS secret `MOCK_ADMIN_TOKEN` (key `admin_token` in the mock's existing Secrets Manager registry), and `scripts/aws_seed.py`'s in-task load command reads it from the task's own environment, so no token travels on the ECS Exec command line. **While the demo admin path is open the task's token is `DEMO_ADMIN_BEARER`** (decision 1, 2026-10-11; 10 §2.1): the gate in front requires that bearer in the request's one `Authorization` header and API Gateway cannot rewrite that header, so the mock's own check (the second layer) compares the same value. The task's secrets name the registry version (`<arn>:admin_token::<version id>`), so opening, closing or changing the address list rolls the task onto the new token.

**Admin on AWS, the demo admin path (U2, 2026-10-11; 10 §2.1).** Off by default: the internal ALB forwards no `/_admin` path and the public API has no route to it. When the Terraform variable `demo_admin_cidrs` (`list(string)`, default `[]`) is non-empty:

| Layer | What is added | Name |
|---|---|---|
| ALB (internal) | two listener rules, priorities 5 and 6, forwarding only the paths the demo UI uses: `/_admin/state`, `/_admin/scenarios`, `/_admin/scenarios/*`, `/_admin/clock`, `/_admin/faults`; `/_admin/lines/*`. Never `/_admin/sink` | `modules/mock_carrier` `aws_lb_listener_rule.admin` (`count`) |
| Public HTTP API | `ANY /carrier-admin/_admin/{proxy+}` → the VPC link → the ALB, path overwritten to `/_admin/{proxy}`, `Host` to `mock_domain_name`, and the query parameter `view` overwritten to `refs`, so the full state (which holds the scenario's numbers) cannot be read through it | `modules/lambdas` `aws_apigatewayv2_integration.mock_admin`, `aws_apigatewayv2_route.admin["mock"]` |
| Gate | the Lambda REQUEST authorizer `admin_gate` on that route: allows only when `requestContext.http.sourceIp` is within `demo_admin_cidrs` **and** `Authorization` is `Bearer <DEMO_ADMIN_BEARER>` (decision 1); `X-Forwarded-For` is ignored; through CloudFront the source is CloudFront, so it is always 403 | `modules/lambdas` `aws_apigatewayv2_authorizer.admin_gate` |
| Output | `mock_admin_url` = `https://<api id>.execute-api.<region>.amazonaws.com/carrier-admin`, `null` when closed; `make outputs` writes it as `MOCK_URL`, and `DEMO_ADMIN_BEARER` (never `MOCK_ADMIN_TOKEN`) into `deploy/.env.aws` | `outputs.tf` |

Status codes seen by the laptop: list empty → 404 (no route: the request falls to the binding page's `$default`, which has no such path); no `Authorization` → 401 (API Gateway, reads included); address off the list or a wrong bearer → 403 (authorizer); `DEMO_ADMIN_BEARER` from a listed address → 200. The mock's G1 check behind it then sees the same bearer.

**Clock on AWS (`MOCK_CLOCK`, decision 4, 2026-10-11).** Tower and Alerts on AWS read the system clock, so a mock clock days behind it would make every "recent" swap look old (or a future one impossible). One setting:

| Setting | Default | Effect |
|---|---|---|
| `MOCK_CLOCK` | `scenario` | `scenario`: as above, the scenario file's `clock` (the demo's `2026-10-05T14:00Z`), moving only when told. `wall`: every scenario load (startup, `POST /_admin/scenarios/load`) starts the clock at wall time, offset 0; it then ticks with the wall, and `POST /_admin/clock` moves the offset (`advance_s` adds to it, `now` sets it). Relative timeline entries (`+00:12:00`) and a line's default `last_status_at` count from the load; absolute times in the file (`sim_change_at: 2026-09-01…`) stay as written. Timeline entries fire on the next clock move after they come due. Anything else fails at startup |

- Set to `wall` only on the Fargate task (`modules/mock_carrier`). Compose, kind and the tests keep `scenario`, so the local demo is unchanged.
- **Which reset puts it back.** The decision says "`make seed-aws` and `POST /internal/reset` reset it to wall time". `/internal/reset` is Alerts' (06 §3.2), and Alerts never calls the mock's admin. The reading taken: the mock itself restarts the clock on every scenario load in `wall` mode, and both reset paths begin with one: `make seed-aws` (`scripts/aws_seed.py`, in-task `POST /_admin/scenarios/load`) and the demo UI's AWS Reset (step 1, `POST {MOCK_URL}/_admin/scenarios/load`, before Alerts' `/internal/reset`). A task restart (e.g. after the admin token rotates) loads at wall time too.

**Simulated mobile-data attribution.** Real Number Verification works because the carrier sees the device on its own network. The mock simulates this with a header `X-Mock-Client-Id`: the binding page, when opened by a test "phone", sends `phone-asish`; a request without a matching id gets 403 `NUMBER_VERIFICATION.USER_NOT_AUTHENTICATED_BY_MOBILE_NETWORK` (the Fall25 code; it was 422 `UNIDENTIFIABLE_DEVICE` in Commonalities 0.4). This makes the "Wi-Fi off, one tap" story testable without a real network, and it is documented on the slide as a simulation.

**Assumed mobile data on AWS (`MOCK_ASSUME_MOBILE_DATA`, prompt 20, D-G).** The mock on Fargate cannot see the phone's network, and the binding page's `?as=` simulation only exists with `TOWER_ENV=local` (e2e §5). So on AWS the mock can be told to assume it:

| Setting | Default | Effect |
|---|---|---|
| `MOCK_ASSUME_MOBILE_DATA` | `0` | `1`: `GET /oauth2/authorize` without `X-Mock-Client-Id` attributes the request to the line whose `mobile_data_client_ids` contains `MOCK_ASSUME_CLIENT_ID`, as if the carrier saw that phone on mobile data |
| `MOCK_ASSUME_CLIENT_ID` | `phone-asish` | the client id assumed. Only one line can be "the phone" at a time, which is the AWS demo: Asish binds on camera and Mom is pre-seeded (D-F) |

- **Logged at startup** as `SIMULATION: MOCK_ASSUME_MOBILE_DATA=1 (client id phone-asish)`. With the flag off, nothing changes: the attribution tests above still hold.
- **One simulation at a time.** With the flag on, an authorize request that *also* carries `X-Mock-Client-Id` (which is what the binding page's `?as=` sends with `TOWER_ENV=local`) is refused: 400 `invalid_request`, "one simulation at a time". The two simulations never combine.
- An unknown `MOCK_ASSUME_CLIENT_ID` (no line has it) fails at startup, not at bind time.
- Set to `1` only on the Fargate task (Terraform `mock_assume_mobile_data`, default `true` while the backend is the mock). Compose, kind and the tests keep `0`. A real carrier or sandbox ignores all of this (rule 6).
- Tests: `services/mock-carrier/tests/test_assume_mobile_data.py`. Default off → 403 without the header, as above. On → authorize succeeds without `X-Mock-Client-Id`, and `phoneNumberShare` returns that line. On + header → 400. On + unknown client id → startup error. End to end with the binding page in AWS mode: `services/binding-page/tests/test_assumed_mobile_data_bind.py`. (Integration marker, not unit: they run the mock in-process over ASGI.)

**Clock and TTLs (06 §7 "Clocks").** The mock keeps its state in memory and writes nothing to DynamoDB, so it sets no TTL. Its scenario clock drives windows only; every DynamoDB TTL in the stack is wall-clock, and the simulated clock never sets one.

## 4. Subscriptions and webhooks

- On create: validate `sink`, store, return `201` with `subscriptionId`, `expiresAt`.
- On event: `POST sink` with a CloudEvents envelope per the spec; retry 3× with backoff; mark delivery in `/_admin/state`.
- Expiry honoured; `DELETE` honoured; `GET` lists.

## 5. Determinism and faults

- No randomness anywhere unless `MOCK_JITTER_MS` is set (for latency tests).
- Faults are injected explicitly, counted down, and visible in state.

## 6. Tests

- **Conformance:** request/response validation against the vendored specs for every operation (schemathesis or an equivalent), including error envelopes.
- **Scenario:** load `demo.yaml`, advance 12 min, `check` → `swapped: true`; `retrieve-date` → the expected timestamp.
- **Subscriptions:** create, fire an admin event, assert a CloudEvent at the sink with the right type and `subscriptionId`.
- **Attribution:** (flag off) `verify` without the client id → 403 `NUMBER_VERIFICATION.USER_NOT_AUTHENTICATED_BY_MOBILE_NETWORK`; with the wrong id → the same; with the right id → verified.
- **Faults:** `faults {timeout, 1}` → next call hangs > 400 ms; the Tower-side test asserts `STALE_DATA`.
- **Clock on AWS (§3, decision 4):** with `MOCK_CLOCK=wall` and an injected wall clock (no sleeps), a scenario load lands on wall time; after a Reset and ten wall minutes, firing `sim_swap` gives `latestSimChange` within 60 s of wall time; `POST /_admin/clock` moves it and a load puts it back; relative timeline entries count from the load; `scenario` mode never reads the wall and still starts at `2026-10-05T14:00Z` (`tests/test_wall_clock.py`). Static: the task sets `MOCK_CLOCK=wall` (`tests/aws/test_terraform_static.py`).
- **Admin token and refs (G1, G2):** with `MOCK_ADMIN_TOKEN` set, every mutating route is 401 without it or with a wrong one and 200 with it; the reads answer without it; `?view=refs` and an event fired by `ref` contain no E.164 (`tests/test_admin_token_refs.py`).
- **Admin on AWS (U2):** static (`tests/aws/test_terraform_static.py`): the ALB admin rules, the API route and integration exist only under `demo_admin_cidrs`, the integration overwrites `view` to `refs`, the rule paths exclude `/_admin/sink`, the task's `MOCK_ADMIN_TOKEN` comes from the registry secret pinned to its version, and is `DEMO_ADMIN_BEARER` while open; `aws_seed` builds its in-task command without a token literal. Nightly (`tests/aws/test_admin_gate.py`): closed → 404; open, from the listed address → 401 without `Authorization` on `DELETE /_admin/faults`, 403 with a wrong bearer, 200 with `DEMO_ADMIN_BEARER` (a mutating route that leaves the demo's clock and lines alone); through the CloudFront URL → 403. Unit: `tests/aws/test_admin_gate_handler.py` (the authorizer: allow/deny, IPv4/IPv6, address headers ignored, right address without or with a wrong bearer — the Alerts internal one included — → deny, with `DEMO_ADMIN_BEARER` → allow, malformed or unset → deny).

## 7. Showcase on its own

`make showcase-mock` then open `/docs` (Swagger UI generated from the spec). Load the demo scenario, call `check` from the UI → `false`; advance the clock 12 minutes → `true`. Then `retrieve-date` and show the timestamp move with the clock. What it proves: spec conformance, determinism, and that the mock is a thing a judge can poke at directly. See `testing-and-showcase.md` §2.8.
