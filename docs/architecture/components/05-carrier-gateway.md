# 05 — Carrier Gateway

**Role:** everything between Tower and a carrier: the CAMARA API surface as tools, the outbound credentials, and the switch between the mock and a sandbox.
**Runs on:** AgentCore Gateway (OpenAPI → MCP tools) + AgentCore Identity (outbound OAuth). Fallback: a hand-written `camara-client` package with the same interface.
**Invariant:** Tower never holds a carrier credential and never knows which backend answered.

---

## 1. The interface Tower sees

```python
class CarrierClient(Protocol):                                     # all async; `line` = LineRef(line_id, e164)
    async def sim_swap_check(line, max_age_h) -> SimSwapResult      # {swapped: bool}
    async def sim_swap_date(line) -> datetime | None                # latestSimChange
    async def call_forwarding(line) -> CFResult                     # unconditional | conditional | none
    async def number_verify(auth_code, *, redirect_uri, e164=None) -> NumberVerifyResult  # verified, e164
    async def reachability(line) -> ReachResult                     # reachable, connectivity, lastStatusTime
    async def subscribe(kind, line, sink_url, ttl, *, now) -> subscription_id
        # kind: sim-swap | reachability-data | reachability-sms | reachability-disconnected (one event type per CAMARA subscription)
    async def unsubscribe(subscription_id)
```

`LineRef` carries the `line_id` (logged) and the E.164 the caller decrypted from `msisdn_enc` (never logged, hidden from `repr`); the client never touches `msisdn_enc` or a key. `now` is passed in because nothing in `packages/` reads a clock.

Two implementations: `GatewayClient` (calls the AgentCore Gateway's MCP tools) and `DirectClient` (calls the CAMARA REST endpoints). Same tests run against both.

## 2. CAMARA APIs used

| API | Operation | Used by |
|---|---|---|
| SIM Swap | `POST /sim-swap/v*/check`, `POST /sim-swap/v*/retrieve-date` | `line_is_ok`, Alerts poll |
| SIM Swap Subscriptions | `POST /sim-swap-subscriptions/v*/subscriptions` | `watch_line`, Alerts |
| Call Forwarding Signal | `POST /call-forwarding-signal/v*/call-forwardings` (one call gives unconditional / conditional / none); `POST …/unconditional-call-forwardings` when the carrier answers 501 | `line_is_ok`, Alerts poll |
| Number Verification | `POST /number-verification/v*/verify` | binding (04) |
| Device Reachability Status | `POST /device-reachability-status/v*/retrieve` | `is_reachable`, Alerts |
| Device Reachability Status Subscriptions | `POST /device-reachability-status-subscriptions/v*/subscriptions` | `watch_line` (transplant/care profiles) |

Paths and versions are pinned by the spec files vendored under `specs/camara/` (one YAML per API, from the CAMARA meta-release the mock is built to). A version bump is a deliberate change to those files, with the conformance suite re-run.

## 3. AgentCore Gateway

- Gateway is given the vendored OpenAPI specs and produces one MCP tool per operation. AgentCore names them `<target>___<operationId>` (one target per spec file); `GatewayClient` resolves the names from `tools/list` (`CARRIER_GATEWAY_TOOLS=discover`) and signs its MCP requests with SigV4 (Gateway inbound auth `AWS_IAM`).
- Tower's `GatewayClient` calls those tools; Gateway makes the HTTPS call to whichever base URL is configured — the mock or a sandbox.
- **Outbound auth** via AgentCore Identity: client-credentials for the service-level APIs (SIM Swap check, Call Forwarding, Reachability); **auth-code** for user-consented operations where the carrier requires it. The review notes CIBA is not supported by Gateway — if a sandbox demands CIBA, the `DirectClient` path handles that one flow with custom code and the rest stays on Gateway.
- Week-one check: Gateway against the mock's OAuth shape. If anything doesn't fit, `DirectClient` is the production path and Gateway becomes a documented experiment — the architecture above it is unchanged either way. Spike B (`../spikes/B-agentcore-gateway.md`, not yet run against AWS) lists the tool-name, argument and error-shape assumptions `GatewayClient` makes and what to change if Gateway refutes each.

## 4. Backend switch

```yaml
carrier:
  backend: mock | sandbox
  base_url: https://mock-carrier.local:8443
  oauth:
    token_url: .../oauth2/token
    client_id: <from Identity>
    scopes: [sim-swap, call-forwarding-signal, device-reachability-status, number-verification]
```

One config block. The gateway can't tell which it's talking to, and that is the sentence on the architecture slide.

## 5. Timeouts, retries, errors

| | |
|---|---|
| Per-call timeout | 300 ms on the request path; 5 s on the proactive path |
| Retries | none on the request path (a retry blows the budget); one on the proactive path |
| CAMARA error mapping | 401/403 → `CARRIER_ERROR` (never surfaced as text), except 403 `NUMBER_VERIFICATION.USER_NOT_AUTHENTICATED_BY_MOBILE_NETWORK` → `NOT_BOUND`; 404 `IDENTIFIER_NOT_FOUND` → `NOT_BOUND` (the line isn't on this carrier); codes per the Fall25 specs (Commonalities 0.6), which replaced 0.4's `DEVICE_NOT_FOUND` and `UNIDENTIFIABLE_DEVICE`; 429 → `STALE_DATA` with last-known; 5xx → `CARRIER_ERROR` |
| Circuit breaker | per base URL: after 5 consecutive 5xx (501 `NOT_IMPLEMENTED` excepted — a permanent answer, not an outage) the breaker opens for 60 s, then one half-open trial; the request path returns `STALE_DATA` from Watch state. A per-call timeout is also `STALE_DATA` (02 §6: last-known if a Watch exists, else `CARRIER_ERROR`) |

## 6. Privacy on the wire

- The E.164 number is decrypted from `msisdn_enc` only inside the client call (and, in Alerts, the SNS send), in memory, per request.
- Logs carry `line_id` (HMAC), never the number.
- Subscription `sink` URLs are per-line, unguessable, and carry no number.

## 7. Tests

- **Conformance:** the same request/response fixtures, generated from the vendored specs, run against `DirectClient → mock` and `GatewayClient → Gateway → mock`. Both must produce identical `CarrierClient` results.
- **Error mapping table:** every CAMARA error code in the specs maps to exactly one reason code.
- **Backend swap:** flip `backend` with the server running; the next call succeeds and the audit shows no difference.
- **Credential hygiene:** capture Tower's process environment and logs; assert no `client_secret`, no bearer, no number.

## 8. Showcase on its own

`make showcase-gateway`: list the tools Gateway generated from the specs (the judge sees CAMARA operations as MCP tools), call `sim_swap_check` through it against the mock, flip the mock's scenario, call again. What it proves: OpenAPI → tools with no hand-written client, and the backend-swap claim. See `testing-and-showcase.md` §2.5.
