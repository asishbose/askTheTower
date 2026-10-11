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

- **Clock** is controllable: `POST /_admin/clock {now | advance_s}`. `latestSimChange` and `lastStatusTime` are computed against it, so "twelve minutes ago" is reproducible on any day.
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

**Admin token (G1, doc 11 §10).** With `MOCK_ADMIN_TOKEN` set, every POST and DELETE above needs `Authorization: Bearer <token>` (401 otherwise); the reads stay open because Tower and Alerts read the clock. Compose generates the token (`deploy/compose/.env`) and publishes the mock on `127.0.0.1` only; the seed, `make demo` (`ref_client.demo.Control`), the e2e helpers, the showcase scripts and the demo UI send it. Unset, the routes are open: the in-process tests, kind and the Fargate task, where the network keeps `/_admin` private (ClusterIP + NetworkPolicy; internal ALB that never forwards `/_admin`).

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
- **Admin token and refs (G1, G2):** with `MOCK_ADMIN_TOKEN` set, every mutating route is 401 without it or with a wrong one and 200 with it; the reads answer without it; `?view=refs` and an event fired by `ref` contain no E.164 (`tests/test_admin_token_refs.py`).

## 7. Showcase on its own

`make showcase-mock` then open `/docs` (Swagger UI generated from the spec). Load the demo scenario, call `check` from the UI → `false`; advance the clock 12 minutes → `true`. Then `retrieve-date` and show the timestamp move with the clock. What it proves: spec conformance, determinism, and that the mock is a thing a judge can poke at directly. See `testing-and-showcase.md` §2.8.
