# mock-carrier

A CAMARA carrier in one container, built to the vendored **CAMARA Fall25** specs in
[`specs/camara/`](../../specs/camara/README.md): SIM Swap, SIM Swap Subscriptions, Call Forwarding
Signal, Number Verification, Device Reachability Status and its Subscriptions — 15 operations, plus
OAuth 2, CloudEvents webhooks, a controllable clock, a scenario engine and an admin API. It is the
default backend for the demo and the fixture every other component tests against.

Design: [`docs/architecture/components/08-mock-carrier.md`](../../docs/architecture/components/08-mock-carrier.md).

What it is **not**: a model of any real carrier. It is spec-conformant, deterministic and fast; it
does not imitate carrier quirks or latency (set `MOCK_JITTER_MS` if you want some).

## Run

```bash
# from the repo root
MOCK_ADMIN=1 uv run python -m mock_carrier          # http://localhost:8443/docs
make showcase-mock                                   # same, and prints the §7 curl script
docker build -f services/mock-carrier/Dockerfile -t ask-the-tower/mock-carrier .
docker run --rm -p 8443:8443 -e MOCK_ADMIN=1 ask-the-tower/mock-carrier
```

`GET /healthz` answers when it is up. `/openapi.json` is the six vendored specs merged (plus
`/oauth2/*` and `/_admin/*`); `/openapi/<api>.json` serves each vendored file with two load-time
patches only (server URL and OIDC discovery URL — see `specs/camara/README.md`); `/docs` is Swagger UI.

## Endpoints

| Area | Paths (version segments come from the spec files) |
|---|---|
| SIM Swap | `POST /sim-swap/v2/check`, `POST /sim-swap/v2/retrieve-date` |
| SIM Swap Subscriptions | `POST/GET /sim-swap-subscriptions/v0.3/subscriptions`, `GET/DELETE …/subscriptions/{id}` |
| Call Forwarding Signal | `POST /call-forwarding-signal/v0.4/unconditional-call-forwardings` → `{active}`; `POST …/call-forwardings` → `["unconditional", …]` (a bare array per the spec; `["inactive"]` when none) |
| Number Verification | `POST /number-verification/v2/verify`, `GET /number-verification/v2/device-phone-number` |
| Device Reachability Status | `POST /device-reachability-status/v1/retrieve` → `{reachable, connectivity, lastStatusTime}` |
| Reachability Subscriptions | `POST/GET /device-reachability-status-subscriptions/v0.8/subscriptions`, `GET/DELETE …/{id}` |
| OAuth 2 | `POST /oauth2/token` (client credentials, auth code, CIBA), `GET /oauth2/authorize`, `POST /oauth2/bc-authorize` (only with `MOCK_CIBA=1`), `GET /oauth2/.well-known/openid-configuration` |
| Admin (simulation aid) | `/_admin/scenarios/load`, `/_admin/clock`, `/_admin/lines/{msisdn or ref}/events`, `/_admin/faults`, `/_admin/state[?view=refs]`, `/_admin/sink` — only with `MOCK_ADMIN=1`; POST/DELETE need `MOCK_ADMIN_TOKEN` when it is set (08 §3) |

Every non-2xx is the CAMARA envelope `{status, code, message}`; `x-correlator` is echoed.
Identifier rules follow the specs: two-legged token → `phoneNumber`/`device` required (else 422
`MISSING_IDENTIFIER`); three-legged token → must be absent (else 422 `UNNECESSARY_IDENTIFIER`);
unknown number → 404 `IDENTIFIER_NOT_FOUND` (422 `SERVICE_NOT_APPLICABLE` on subscription create,
which declares no 404). `maxAge` outside 1..2400 → 400 `INVALID_ARGUMENT`.

### OAuth clients

`config/clients.yaml` holds local placeholders (`tower`, `alerts`, `binding-page`, `limited`), not
secrets. A granted scope covers its sub-scopes (`sim-swap` covers `sim-swap:check`). Tokens are HS256
JWTs stamped by the **mock clock**, so advancing the clock past a token's 24 h lifetime expires it.

### Number Verification and `X-Mock-Client-Id` (simulation aid)

A real carrier attributes the request to a line because it arrives over that line's cellular data.
The mock simulates this: `GET /oauth2/authorize` with `X-Mock-Client-Id: phone-asish` (an id listed
in the line's `mobile_data_client_ids`) issues a code bound to that line. A token obtained without the
header, or with an id no line lists, gets 403
`NUMBER_VERIFICATION.USER_NOT_AUTHENTICATED_BY_MOBILE_NETWORK` (the Fall25 code; Commonalities 0.4's
`UNIDENTIFIABLE_DEVICE` no longer exists). The header does not exist on a real carrier.

### Subscriptions and CloudEvents

HTTP protocol only; `sinkCredential` `ACCESSTOKEN` only (forwarded as `Authorization: Bearer` to the
sink). Events are CloudEvents 1.0 (`application/cloudevents+json`) carrying `subscriptionId` — the
optional phone number/device is never echoed to the sink. Delivery: one try plus up to 3 retries with
exponential backoff on network errors, 5xx and 429; every delivery is recorded in `/_admin/state`.
`subscriptionExpireTime` (default +30 days), `subscriptionMaxEvents` and `initialEvent` are honoured;
expiry, max-events and delete send `subscription-ended`. A sink on host `sink.mock.local` is delivered
in-process to `GET /_admin/sink` (simulation aid for showcases).

### Scenarios, clock, faults

`scenarios/*.yaml` at the repo root (copied into the image): `demo` (08 §2 verbatim), `care` (Mom's
line dark for 4 h in the daytime window; variant `recovered`), `transplant` (dark for 59 min; variant
`blip`, reachable again at minute 10). `POST /_admin/scenarios/load {"name": "transplant", "variant":
"blip"}` resets everything. `POST /_admin/clock {"advance_s": 720}` fires due timeline events in order
and expires subscriptions. `POST /_admin/lines/{msisdn}/events {"event": "sim_swap"}` (`type` is an
accepted alias). `POST /_admin/faults {"kind": "timeout"|"500"|"429", "n": 2}` affects the next *n*
CAMARA calls (not `/oauth2`, not `/_admin`); `timeout` delays by `MOCK_FAULT_TIMEOUT_S` then answers.

No randomness unless `MOCK_JITTER_MS` > 0; no wall-clock reads outside `clock.py` (and only for a
scenario whose `clock` is `now`). The same scenario and admin calls give the same `/_admin/state`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MOCK_ADMIN` | off | `1` mounts `/_admin/*` (simulation aid) |
| `MOCK_ADMIN_TOKEN` | — | when set, `/_admin`'s POST/DELETE routes need `Authorization: Bearer <token>` (GETs stay open: Tower and Alerts read the clock). Compose generates it |
| `MOCK_CIBA` | off | `1` enables `/oauth2/bc-authorize` and the CIBA grant (auto-approved) |
| `MOCK_JITTER_MS` | `0` | uniform random delay 0..N ms per CAMARA call (the only randomness) |
| `MOCK_PORT` | `8443` | listen port |
| `MOCK_TLS_CERT` / `MOCK_TLS_KEY` | unset | serve HTTPS with these PEM files |
| `MOCK_SCENARIO` | `demo` | scenario loaded at start (`name` or `name:variant`) |
| `MOCK_BASE_URL` | `http://localhost:$MOCK_PORT` | used in the served specs' server URL and token `iss` |
| `MOCK_JWT_SECRET` | local dev value | HMAC key for the tokens the mock issues |
| `MOCK_SPECS_DIR`, `MOCK_SCENARIOS_DIR`, `MOCK_CLIENTS_FILE` | repo paths / `/app/...` in the image | data locations |
| `MOCK_TOKEN_TTL_S` | `86400` | token lifetime (mock clock) |
| `MOCK_FAULT_TIMEOUT_S` | `0.5` | delay of a `timeout` fault |
| `MOCK_WEBHOOK_BACKOFF_S`, `MOCK_WEBHOOK_TIMEOUT_S` | `0.2`, `2.0` | webhook retry backoff base / per-attempt timeout |
| `MOCK_LOOPBACK_SINK_HOST` | `sink.mock.local` | sink host delivered in-process |
| `MOCK_ASSUME_MOBILE_DATA` | off | `1`: `GET /oauth2/authorize` without `X-Mock-Client-Id` is attributed to the line of `MOCK_ASSUME_CLIENT_ID`, as if the carrier saw that phone on mobile data (AWS only, 08 §3). Logged at startup as `SIMULATION`; a request that also sends `X-Mock-Client-Id` gets 400 "one simulation at a time" |
| `MOCK_ASSUME_CLIENT_ID` | `phone-asish` | the client id assumed by `MOCK_ASSUME_MOBILE_DATA`; one that no line has fails at startup |

`.env.example` lists them with placeholders.

## Tests

```bash
uv run pytest services/mock-carrier -q          # unit + in-process integration + conformance
uv run python services/mock-carrier/scripts/conformance.py --url http://localhost:8443   # CLI, against a running mock
```

- `test_conformance.py` — schemathesis over each vendored spec, all checks, in-process; writes
  `artifacts/conformance-report.html`. Config in `schemathesis.toml` (two documented widenings).
- `test_scenario.py`, `test_subscriptions.py`, `test_attribution.py`, `test_faults.py`,
  `test_determinism.py`, `test_openapi.py` (served docs vs vendored files), `test_units.py`.

`mock_carrier.testing` has helpers other components reuse to drive the mock in-process: `Sink`
(an in-process CloudEvents receiver), `cc_token()`, `auth_code_token()`, `advance()`.

## Showcase

`make showcase-mock` starts the mock with `MOCK_ADMIN=1` and prints the curl script for
testing-and-showcase §2.8: `check` false → advance 12 min → true; `retrieve-date` moves with the
clock; a subscription to the loopback sink and a CloudEvent arriving; a timeout fault counting down.
Open `/docs` alongside. What it proves: spec conformance, determinism, subscriptions, faults.
