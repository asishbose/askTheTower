# Spike B — AgentCore Gateway against a CAMARA-shaped OAuth target

> **Status: NOT RUN against AWS** (autonomous build, 2026-10-06: no AWS credentials). The script and its
> offline self-test exist: `scripts/spikes/b_gateway_probe.py` (`stub`, `probe`, `selftest`; tested by `tests/spikes`).

## Assumptions being tested (the build already depends on them)

| Assumption | Where it is coded | If refuted |
|---|---|---|
| Gateway names tools `<target>___<operationId>`; with the target named after the spec, that is `sim-swap___checkSimSwap`. The older `<api>__<operationId>` convention and a unique suffix are also accepted. | `camara_client.gateway.resolve_tool_names`, `CARRIER_GATEWAY_TOOLS=discover` (build logs 06, 13) | Any unique suffix still works. Otherwise write `gateway-tools.json` by hand (`scripts/register_gateway.py`). |
| The request body arrives as one `body` argument (plus path params by name). | `camara_client.gateway.tool_arguments` | Flatten the body fields in `tool_arguments()`; `FakeGateway` in `camara_client.testing` must match. |
| Errors come back as `isError` with `{status, body}` or a CAMARA envelope. | `gateway._error_parts` | Map the real shape there; re-run `tests/aws -m nightly -k conformance`. |
| Identity does client credentials against the target's `/oauth2/token`. | `deploy/terraform` Identity provider | Cut line: `DirectClient` (05 §3, 00 cut line). |
| `sim-swap.yaml` (r3.3, `allOf` in schemas, no `oneOf` on `device`) is accepted as is. | `specs/camara/` | Note the construct and the workaround; never edit the vendored spec silently. |

## Steps

1. `uv run python scripts/spikes/b_gateway_probe.py stub --port 8766` on a throwaway public URL. Credentials come
   from `SPIKE_B_CLIENT_ID` / `SPIKE_B_CLIENT_SECRET` (env only).
2. Create a Gateway with one OpenAPI target from `specs/camara/sim-swap.yaml`, with `servers[0].url` = the stub URL
   + `/sim-swap/v2`, and outbound auth = an Identity OAuth provider (client credentials, token URL = stub
   `/oauth2/token`). _Console/CLI steps and minutes:_
3. `uv run python scripts/spikes/b_gateway_probe.py probe --url <gateway>/mcp --sigv4 --region <r> -n 20 --out artifacts/spikes/B-gateway-tools.json`
4. Check the stub log: is there one `token grant … via client_secret_basic|post` line, followed by checks?

## Results

- Tools generated (names, `body` vs flattened args): _
- OAuth call seen at the stub (method, scope): _
- Spec constructs rejected (exact error): _
- Round trip, 20 calls: p50 _ / p95 _ / p99 _ ms

Offline self-test, for comparison only (in-process stand-in, **not Gateway**), 20 calls:
`sim-swap___checkSimSwap`, `body` argument, 1 token grant, `{"swapped":false}`; p50 33.7 / p95 113.9 ms.

**Verdict:** _not run — Gateway stays the AWS path on the assumptions above; `DirectClient` is the tested path and the cut line._ Updates: `components/05` §3 (link to this page).
