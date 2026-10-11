# tower-mcp

The Tower MCP server — the thing Alexa+ connects to. Three tools over MCP Streamable HTTP:

| Tool | Does | Carrier calls |
|---|---|---|
| `line_is_ok(line="self")` | SIM swapped recently? Call forwarding set? | SIM Swap check (72 h) + Call Forwarding, in parallel, 300 ms each — or none, when a fresh Watch state (≤ 10 min) exists |
| `is_reachable(line="self")` | Is the line attached to the network? | one Device Reachability Status call |
| `watch_line(line="self", enable=true\|false\|null)` | Alerts on/off (writes the Watch, tells the Alerts service), or with `null` a status: watching, `profile` (stored, also while off), grants, who checked. `enable=true` keeps the stored profile and contacts (the line-holder sets them on the binding page, 04 §9) | none |

Every result is the same envelope (`schemas.ToolResult`): `summary` (spoken text, only from
`tower_policy.phrase`), `facts` (booleans, enums and timestamps only), `reason_codes`, `next_step`
(`none | call_carrier | bind_line | ask_consent`) and `checked_at`. Refusals (`NOT_BOUND`, `NO_CONSENT`,
`SERVICE_UNAVAILABLE`, `CARRIER_ERROR`, `STALE_DATA`) use the same envelope. Every answer is written to the
audit log **before** it is returned; if the audit write fails, the tool refuses.

Design: [`docs/architecture/components/02-tower-mcp-server.md`](../../docs/architecture/components/02-tower-mcp-server.md);
tool descriptions: [`01-alexa-surface.md` §2](../../docs/architecture/components/01-alexa-surface.md) (copied verbatim into
`src/tower_mcp/descriptions.py`; `tests/test_descriptions.py` fails if they drift).

What it is **not**: a model. There is no LLM SDK in this service (a test asserts it). It never returns a phone
number, a location, a carrier error string or a stack trace. Policy decisions come from `tower-policy`.

## Layout

| File | Role |
|---|---|
| `server.py` | FastMCP app, stateless Streamable HTTP at `/mcp` (JSON responses), `/healthz`, tool registration |
| `auth.py` | middleware: bearer → `user_id` (see Identity below) |
| `deps.py` | wiring built once at startup: settings, thresholds, carrier client (`make_client`), store, cipher, clock, Alerts client |
| `tools/` | one handler per tool; `common.py` holds resolve → audit → envelope and per-step timings |
| `schemas.py` | `ToolResult`, per-tool facts, `NextStep` |
| `next_step.py` | `bind_line` (one-time bind token + `BINDING_BASE_URL`), `call_carrier` (`CARRIER_SUPPORT_NUMBER`) |
| `errors.py` | every exception → a refusal code or a bare "internal error" |
| `seed.py` | the demo seed (Asish's line, Mom's line, Mom's `watch` grant to Asish as "mom") for showcase/latency |

## Identity (final per RUN-ALL Decisions; to be confirmed by spike A / the registration run)

`Authorization: Bearer <JWT>` verified against `TOWER_JWKS_URL` (`exp` and `sub` required; issuer checked when set;
`aud` against `TOWER_JWT_AUDIENCE` and `client_id` against `TOWER_JWT_CLIENT_IDS` when set, each a comma-separated
list — Cognito access tokens carry `client_id` and no `aud`); `user_id` = `sub`. A subject that is not an opaque id (spaces, > 128 chars, or a run of 10+ digits)
is mapped to `u_` + 40 letters of its SHA-256, so nothing that looks like a phone number reaches the audit log.
With `TOWER_ENV=local` only, the static `TOWER_BEARER` plus `X-Tower-User: <user_id>` is accepted. Anything else → 401.
Recorded in `docs/architecture/components/01-alexa-surface.md` §4.

## Run

```bash
# from the repo root
make showcase-tower          # mock + DynamoDB Local (moto server without Docker) + Tower; prints the script below
uv run python -m tower_mcp   # Tower alone, configured from the environment (see .env.example)
uv run pytest services/tower-mcp -q

docker build -f services/tower-mcp/Dockerfile -t ask-the-tower/tower-mcp .
docker run --rm -p 8000:8000 --env-file services/tower-mcp/.env ask-the-tower/tower-mcp
```

`GET /healthz` answers when it is up. On a WSL2 `/mnt/c` checkout the first start takes ~20 s (importing
`fastmcp`); in the container it is ~2 s.

## Configuration

Environment only (`deps.py`; placeholders in [`.env.example`](.env.example)).

| Variable | Default | Meaning |
|---|---|---|
| `TOWER_ENV` | `local` | `local` enables the static bearer + `X-Tower-User`; anything else requires a JWT |
| `TOWER_BEARER` | — | the local static bearer (ignored unless `TOWER_ENV=local`) |
| `TOWER_JWKS_URL`, `TOWER_JWT_ISSUER`, `TOWER_JWT_AUDIENCE`, `TOWER_JWT_CLIENT_IDS` | — | inbound JWT verification (lists comma-separated; see Identity) |
| `TOWER_CALL_LOG` | `0` | local only: `1` logs each tool call + result as one `tower_mcp.calls` line (Alexa+ transcripts); ignored outside `TOWER_ENV=local` |
| `TOWER_HOST`, `TOWER_PORT` | `0.0.0.0`, `8000` | listen address (AgentCore Runtime expects 8000 and `/mcp`) |
| `TOWER_TZ` | `America/Toronto` | zone for spoken times ("10:12 today") |
| `TOWER_THRESHOLDS` | packaged `thresholds.yaml` | policy thresholds file |
| `TOWER_CLOCK_URL` | — | local demo only: follow the mock carrier's clock (`<mock>/_admin/clock`) |
| `DYNAMO_ENDPOINT` (or `TOWER_DYNAMODB_ENDPOINT`), `TOWER_TABLE_PREFIX`, `AWS_REGION` | — | consent / watch / audit tables; endpoint unset on AWS |
| `TOWER_LINE_ID_KEY`, `TOWER_MSISDN_KEY` (local) or `TOWER_KMS_*` (AWS) | — | `tower_consent.crypto_from_env` |
| `CARRIER_CLIENT`, `CARRIER_BACKEND`, `CARRIER_BASE_URL`, `CARRIER_CLIENT_ID`, `CARRIER_SECRET_REF`, `CARRIER_GATEWAY_*` | see `camara_client.config` | carrier client (`direct` to the mock locally, `gateway` on AWS) |
| `BINDING_BASE_URL` | `http://localhost:8081` | `next_step.url` = `<base>/bind/<token>` |
| `CARRIER_SUPPORT_NUMBER` | — | the carrier's public support line for `next_step.call_carrier` (config, not anyone's line) |
| `ALERTS_INTERNAL_URL`, `ALERTS_INTERNAL_BEARER` | — | Alerts' `POST /internal/watch`; unset → a log-only stub records the call |
| `LOG_LEVEL` | `info` | `debug` logs per-step timings (`resolve`, `load`, `carrier`, `policy`, `audit`) with `line_id` only |

## Connecting from Alexa+

Alexa+ is an MCP client: it reads the three tool descriptions, picks a tool, calls `/mcp` over Streamable HTTP
with the account-linked user's bearer token, and speaks the result (01 §1–§4). The full runbook, with the exact
commands, is [`docs/architecture/alexa/registration.md`](../../docs/architecture/alexa/registration.md). In short:

1. **Identity provider for account linking.** An OAuth 2.0 authorization-code provider (the runbook uses an
   Amazon Cognito user pool with an `alexa-link` app client). Alexa+ stores the access token it issues and sends
   it on every tool call; Tower's `user_id` is that token's `sub`.
2. **Tower verifies the token.** Set `TOWER_JWKS_URL`, `TOWER_JWT_ISSUER` and `TOWER_JWT_CLIENT_IDS` (Cognito:
   `client_id`, no `aud`) or `TOWER_JWT_AUDIENCE`. Locally: put them in `deploy/compose/.env` and recreate Tower
   with the overlay —
   `docker compose -f deploy/compose/docker-compose.yml -f deploy/compose/docker-compose.alexa.yml up -d tower-mcp`
   (the overlay also turns on `TOWER_CALL_LOG`). On AWS: `tower_jwt_*` / `tower_jwks_url` in
   `deploy/terraform/envs/aws.tfvars`; AgentCore Runtime's JWT authorizer checks the same token first.
3. **A public HTTPS URL.** Locally a tunnel to `localhost:8080` (`cloudflared tunnel --url http://localhost:8080`)
   → `https://<host>/mcp`; on AWS `terraform -chdir=deploy/terraform output -raw tower_mcp_url`.
4. **Register** that URL in the Alexa+ MCP Toolkit with OAuth account linking (authorize/token URLs, client id
   and secret of step 1 — typed into the console only, never committed), and link the account in the simulator.
5. **Map the linked user to the demo lines:**
   `uv run python services/tower-mcp/scripts/showcase_alexa.py link --sub <sub>` binds Asish's demo line to that
   identity and gives it Mom's `watch` grant ("mom"), through the binding page's one-tap flow.
6. **Run** `make showcase-alexa`: it prints the §2.1 script (5 phrasings × 3 tools, 2 off-topic, 1 ambiguous) and
   the three moments with the mock admin curl lines, tails Tower's log, and writes one Tower-side transcript per
   call to `artifacts/transcripts/alexa-NN-<tool>.json`. Record what Alexa+ said in
   [`simulator-run.md`](../../docs/architecture/alexa/simulator-run.md), friction in
   [`friction-log.md`](../../docs/architecture/alexa/friction-log.md).

What Alexa+ can't do here: speak first (alerts go by SMS from the Alerts service), open the `bind_line` link on
the Echo (binding is one tap on the phone, over mobile data), or decide anything — `summary` comes from Tower's
templates. While a local tunnel is up the static local bearer is reachable too, so keep the tunnel short-lived.
Status: not yet run against Alexa+ (no account in the autonomous build); `make showcase-alexa
SHOWCASE_ARGS=--print-only` prints the script alone.

## Showcase (`make showcase-tower`, testing-and-showcase §2.2)

The script starts the mock (`MOCK_ADMIN=1`, port 8443), DynamoDB on 8001 and Tower on 8000 with a fresh
random bearer, seeds the demo and prints:

1. **MCP Inspector:** `npx @modelcontextprotocol/inspector` → transport "Streamable HTTP", URL
   `http://localhost:8000/mcp`, headers `Authorization: Bearer <printed>` and `X-Tower-User: user-asish` →
   List Tools shows the three tools with the 01 §2 descriptions.
2. `line_is_ok {"line":"self"}` → `["OK"]`, "Your line is as it was."
3. Advance the mock clock 12 min and fire `sim_swap` on Asish's line (curl lines printed) → `["SIM_SWAPPED_RECENT"]`,
   "Your SIM was moved to another device at 10:12 today…", `next_step.kind = "call_carrier"`.
4. Fire `cf_set` → `["SIM_SWAPPED_RECENT","CALL_FORWARDING_SET"]`.
5. `line_is_ok {"line":"bob"}` → `["NOT_BOUND"]`, `next_step.kind = "bind_line"` with a binding URL.
6. `line_is_ok`/`is_reachable` on `mom`; `watch_line {"line":"self"}` as `user-mom` → the grant to "mom" and who checked.
7. Reset the mock: `POST /_admin/scenarios/load {"name":"demo"}`.
8. Latency: `uv run python scripts/latency.py --out artifacts/latency.md` (or `TOWER_LATENCY_WRITE=1 uv run pytest
   services/tower-mcp/tests/test_latency.py`), 200 calls per path. The committed
   [`artifacts/latency.md`](../../artifacts/latency.md) is **in-process, not representative**; the deployed
   number comes from `ENV=aws make latency-aws`.

`--print-only` prints the script without starting anything; `--ddb moto` forces the moto server.
