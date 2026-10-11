# camara-client

The `CarrierClient` that Tower (02) and Alerts (06) call to ask a carrier about a line, over the CAMARA
APIs in `specs/camara/` (Fall25). Two implementations behind one protocol:

- **`DirectClient`**: CAMARA REST over httpx. Client-credentials tokens are cached per scope. Paths and
  versions come from the vendored specs. Used locally against the mock, and on AWS wherever Gateway is
  not used (CIBA, or the cut line).
- **`GatewayClient`**: the same operations as AgentCore Gateway MCP tools. Gateway holds the carrier
  credentials (AgentCore Identity), so Tower holds none. The tool names come from `gateway-tools.json`,
  written at deploy time. Without that file the client assumes `<api>__<operationId>`.

Both share one implementation of the operations (`core.py`) and the same middleware: breaker, then
timeout, then error map, then retry. Given the same carrier answer they return byte-identical results.
The tests prove this over 162 spec-derived fixture cases.

Design: [`docs/architecture/components/05-carrier-gateway.md`](../../docs/architecture/components/05-carrier-gateway.md).

## Use

```python
from camara_client import CarrierConfig, CarrierError, LineRef, make_client

client = make_client(CarrierConfig.from_env())  # DirectClient or GatewayClient per CARRIER_CLIENT
line = LineRef(line_id, e164)  # the caller decrypts msisdn_enc; repr hides e164
try:
    swap = await client.sim_swap_check(line, max_age_h=72)  # SimSwapResult(swapped=…)
    cf = await client.call_forwarding(line)  # CFResult(status=unconditional|conditional|none)
except CarrierError as e:
    e.reason_code, e.retryable  # NOT_BOUND / STALE_DATA / CARRIER_ERROR
```

| Method | CAMARA operation(s) |
|---|---|
| `sim_swap_check(line, max_age_h)` → `SimSwapResult` | `POST /sim-swap/v2/check` |
| `sim_swap_date(line)` → `datetime \| None` | `POST /sim-swap/v2/retrieve-date` |
| `call_forwarding(line)` → `CFResult` | `POST /call-forwarding-signal/v0.4/call-forwardings`; on 501 `…/unconditional-call-forwardings` |
| `number_verify(auth_code, *, redirect_uri, e164=None)` → `NumberVerifyResult` | code exchange, then `POST /number-verification/v2/verify` (or `GET …/device-phone-number` when `e164` is None) |
| `reachability(line)` → `ReachResult` | `POST /device-reachability-status/v1/retrieve` |
| `subscribe(kind, line, sink_url, ttl, *, now, sink_token=None)` → id | `POST /sim-swap-subscriptions/v0.3/subscriptions` or `…/device-reachability-status-subscriptions/v0.8/subscriptions` (one event type each) |
| `unsubscribe(id)` | `DELETE …/subscriptions/{id}`; 404/410 count as done |

`DirectClient.authorization_url(redirect_uri, state)` builds the Number Verification authorize URL for the
binding page. Call `GatewayClient.check_tools()` once at startup. It checks the tool map against the live
Gateway and pays the one-time MCP import and connection cost outside any request.

## Behaviour (05 §5)

| | request profile (Tower) | proactive profile (Alerts) |
|---|---|---|
| timeout per attempt | 300 ms | 5 s |
| retries | none, ever | one, for retryable errors (401, 409 ABORTED, 429 TOO_MANY_REQUESTS, 5xx, timeout, transport) |

- **Error map** (`errors.py`): every (status, code) in the Fall25 specs maps to exactly one reason.
  - 403 `NUMBER_VERIFICATION.USER_NOT_AUTHENTICATED_BY_MOBILE_NETWORK` and 404 `IDENTIFIER_NOT_FOUND` → `NOT_BOUND`.
  - 429 → `STALE_DATA`.
  - Everything else → `CARRIER_ERROR`.
  - Unknown codes fall back by status.
  - A timeout → `STALE_DATA`.
  - An open breaker → `STALE_DATA` (not retryable).
  - `CarrierError` carries the status and the code, never the carrier's message.
- **Breaker** (`breaker.py`): one per base URL.
  - 5 consecutive 5xx (501 excepted) open it for 60 s.
  - After 60 s it goes half-open and lets one trial call through. Success closes it; failure re-opens it.
  - A 4xx or 429 resets the count.
- **Privacy**: the E.164 lives only inside the request body. Logs carry `line_id`, the operation, the status and the code. `LineRef` and `NumberVerifyResult` hide the number from `repr`.

## Config (env, `CarrierConfig.from_env`)

| Variable | Default |
|---|---|
| `CARRIER_CLIENT` | `direct` · `gateway` |
| `CARRIER_BACKEND` | `mock` · `sandbox` (recorded only; nothing branches on it) |
| `CARRIER_BASE_URL` | `http://localhost:8443` |
| `CARRIER_TOKEN_URL` / `CARRIER_AUTHORIZE_URL` | `<base>/oauth2/token` / `<base>/oauth2/authorize` |
| `CARRIER_CLIENT_ID` | `tower` |
| `CARRIER_SECRET_REF` | `env:CARRIER_CLIENT_SECRET`. The caller resolves anything other than `env:` (Identity / Secrets Manager) and passes `make_client(config, secret=…)` |
| `CARRIER_SCOPES` | the API scopes of 05 §4 |
| `CARRIER_PROFILE` | `request` · `proactive` |
| `CARRIER_GATEWAY_URL`, `CARRIER_GATEWAY_TOOLS` | Gateway MCP endpoint; path to `gateway-tools.json`, or `discover` (resolve names from `tools/list` at first use) |
| `CARRIER_GATEWAY_AUTH` | `none` · `sigv4` (AWS: the Gateway's inbound auth is `AWS_IAM`; `camara_client.aws.SigV4Auth` signs each MCP request for `bedrock-agentcore`) |
| `CARRIER_GATEWAY_REGION` | defaults to `AWS_REGION` |
| `CARRIER_GATEWAY_SESSION` | `per-call` · `persistent` (one MCP session per process, opened by `check_tools()` at warm-up; Tower) |

## Fixtures

`src/camara_client/fixtures/` is generated from the specs by `scripts/gen_fixtures.py`, using the mock
carrier's own loader, so the client and the mock cannot disagree on a path. It holds:

- `operations.json`: the spec index the client reads at runtime. No YAML is needed in the image.
- One file per operation the client calls. Each file has every success example, every declared error
  code, and three out-of-spec cases (500, an unknown code, a non-JSON 502).

After changing `specs/camara/`, run `uv run python packages/camara-client/scripts/gen_fixtures.py`.
`test_error_map.py` fails if the files are stale.

## Tests

`uv run pytest packages/camara-client -q`. Everything runs in-process against the mock carrier
(`httpx.ASGITransport`). The one exception is `test_backend_swap.py::test_swap_over_real_sockets`,
which runs two uvicorn servers on ephemeral ports.

- `test_direct_mock.py`: every operation against the mock, plus the demo scenario's flips.
- `test_gateway_fake.py`: the 162 fixture cases through both clients, compared byte for byte. Also live mock flows through `FakeGateway` (`camara_client.testing`, an in-process FastMCP server that does what Gateway does).
- `test_error_map.py`, `test_timeouts.py`, `test_breaker.py`, `test_backend_swap.py`, `test_hygiene.py`: as named.

## Showcase

`make showcase-gateway` (`ENV=local`) runs in-process with nothing to start:

1. Lists the 15 CAMARA operations as MCP tools.
2. Calls `sim_swap_check` through the Gateway path, which returns false.
3. Moves the mock's clock past the scripted swap. The same call now returns true, identical to `DirectClient`.
4. Flips `CARRIER_BASE_URL` to a second mock running `transplant`. The next call goes there, with no code change.

`ENV=aws` uses `CarrierConfig.from_env()` against the real Gateway. Terraform sets `CARRIER_GATEWAY_AUTH=sigv4` and
`CARRIER_GATEWAY_TOOLS=discover`. AgentCore names tools `<target>___<operationId>`, and `resolve_tool_names` maps
them; `make register-gateway` writes `artifacts/gateway-tools.json` from the deployed Gateway. Unverified until
the first deploy: the argument shape (`{body, <path params>}`) and the error shape (`isError` + `{status, body}`).

## Not here

- `msisdn_enc` and KMS: the caller decrypts the number (tower-consent).
- CIBA.
