# 09 — Reference Client

**Role:** a voice-shaped client for Tower that isn't Alexa+. It is the demo path if Alexa+ web-simulator access slips, the integration-test harness for tool selection, and the one legitimate place Bedrock sits in this system.
**Runs on:** locally (CLI, or the HTTP app behind the web chat page on `127.0.0.1:8083`); on AWS as a second AgentCore Runtime (protocol HTTP) behind the web chat page (§6).
**Owns:** nothing. It calls Tower's MCP tools exactly as Alexa+ would.

---

## 1. Why it exists

Three reasons, all of them boring and all of them real:

1. **Fallback demo surface.** The MCP Toolkit is US-only; the simulator should work from Ottawa, but if it doesn't on the day, this client drives the same three moments from a terminal with speech-to-text in and text-to-speech out.
2. **Tool-selection regression.** Alexa+'s model chooses tools from descriptions. The reference client, with a different model, is a second opinion: if phrasings in the test corpus select the wrong tool here, the descriptions are ambiguous and will be ambiguous for Alexa+ too.
3. **AWS Builder mini.** It is a Strands agent on Bedrock that genuinely does something — not Bedrock in the hot path for show.

## 2. Shape

```
ref-client/
  agent.py           # Strands agent; system prompt from prompts/ref-client/
  mcp_client.py      # connects to Tower over Streamable HTTP with the local bearer
  voice.py           # optional: mic → transcribe → agent → polly → speaker
  corpus/            # phrasings.yaml — utterance → expected tool + args
  prompts/
    ref-client/
      system.md      # one file; the client has one role
```

**Diagrams:** the web chat lane appended to [`02-request-path`](../diagrams/02-request-path.drawio) (page → agent → Tower, §6) and the agent runtime, Cognito pool, S3/CloudFront and Lambda URL on [`08-agentcore-deployment`](../diagrams/08-agentcore-deployment.drawio) page 1.

**As the web chat page (prompt 20):** `src/ref_client/http.py` (FastAPI: `POST /invocations`, `GET /ping`, `GET /healthz`, `GET /config.js` locally, static mount of `services/web-chat/` at `/`) and `src/ref_client/auth.py` (bearer extraction and pass-through). The CLI stays: `python -m ref_client.run`.

Model: a small Bedrock model (Nova Micro / Haiku-class) at low temperature. It never sees carrier credentials, never calls carrier APIs directly, never decides policy — it only decides which Tower tool to call, like Alexa+.

**As built (prompt 11):** `services/ref-client/` with `src/ref_client/{agent,mcp_client,demo,transcript,corpus_runner,run,voice}.py`, `prompts/ref-client/system.md`, `corpus/phrasings.yaml`. The agent calls the **Bedrock Converse API with tool use directly** (boto3), not the Strands Agents SDK: `strands-agents` 1.58 requires `mcp<2.2` and Tower's FastMCP 4.0.11 needs `mcp` 2.3 in the same uv workspace lock. The loop is the one Strands' `BedrockModel` would run; swap back when Strands accepts `mcp>=2.3`. Default model `amazon.nova-micro-v1:0` (`BEDROCK_MODEL_ID`), temperature 0. `demo`: an `Advance` step marked `alerts=True` (the transplant story's +20 and +15) waits `DEMO_ALERTS_SETTLE_S` after the jump (2.5 s on ENV=local, 0 elsewhere) so Alerts acts on it before the next step changes the line (06 §11.4). Without AWS credentials the demo runs with a **scripted agent** (tool calls from the demo script, `summary` read verbatim) and says so; it proves Tower's side of the transcript, not tool selection.

## 3. The corpus

```yaml
- say: "my phone just lost signal, is my line ok?"
  expect: { tool: line_is_ok, args: { line: self } }
- say: "is anything forwarding my calls"
  expect: { tool: line_is_ok, args: { line: self } }
- say: "is mom's phone on"
  expect: { tool: is_reachable, args: { line: mom } }
- say: "stop watching mom's line"
  expect: { tool: watch_line, args: { line: mom, enable: false } }
- say: "what's the weather in ottawa"
  expect: { tool: null }
- say: "is the line ok"     # no owner named: ask which line ("is my line ok" means self — demo moment 1)
  expect: { clarify: true }
```

Forty-odd entries. The test asserts selection and arguments; it does not assert the model's wording.

## 4. Transcript capture

Every run writes a transcript (`utterance → tool call → structured result → spoken text`) to `artifacts/transcripts/`. The README embeds one. This is the evidence a judge can read without a device. Golden files (`tests/e2e/golden/`, one set for ENV=local, eks and aws) hold the tool calls and reason codes per utterance; `ref_client.transcript.compare` is the check. Between utterances the demo calls the mock's `/_admin/*` and, for moment 3's revoke, the binding page's local grant admin `POST /_admin/grants {owner_user_id, grantee_user_id, grant, alias, action}` (`BIND_ADMIN=1`).

The demo script fires mock events by the line's opaque `ref`, read from `GET /_admin/state?view=refs`, so it never handles a number, and sends `MOCK_ADMIN_TOKEN` as a bearer when it is set (08 §3). `run_demo(agent_for, control, *, reset=True, on_step=None, replay=())` (G4, doc 11 §10): `on_step` receives a `StepReport` per step (story, step id `<story>#<n>`, kind, narration, expected and actual reason codes, `ok`, `elapsed_ms`, `replay`, fired timeline events, the `Turn`); `reset=False` skips the scenario reload and only looks up the line refs. The CLI's behaviour is unchanged.

**Who may depend on it.** No product component — Tower, Alerts, the binding page, the mock carrier, `packages/` — imports `ref_client` or waits on it, so the product never has a model on its path (design rule 1). The one consumer is the demo UI (11), which is laptop-only demo tooling outside paths A/B/C and drives Tower through this client exactly as `make demo` does; nothing imports the demo UI in turn. `tests/test_fallback.py::test_nothing_depends_on_the_reference_client` checks both. The web chat page (§6) does not change this: its HTTP app lives inside `ref_client`, and `services/web-chat` is static files plus a header-forwarding proxy that call it over HTTP and import nothing. The page is an *entry* to Path A, like Alexa+, not a product component.

## 5. Not a second policy path

The client receives Tower's `summary` and may read it verbatim or rephrase; it may not add facts. The system prompt says so, and a test asserts no digits appear in its output that weren't in `summary` (no invented times or numbers). The client also enforces it: a model reply carrying a digit that no `summary` has is replaced by the summary verbatim, and the transcript records the replacement (`guard`).

## 6. Deployed as the web chat page (decision 2026-10-09, prompt 20)

The Alexa+ stand-in for the AWS demo and the prompt-15 fallback. The same agent, prompts, stories and golden transcripts, exposed over HTTP with a signed-in page in front. Flows and decisions: [`../bind-and-alert-flows.md`](../bind-and-alert-flows.md). The laptop demo UI (11) is unchanged and is not this page. It is never deployed and it holds the carrier controls; the chat page has none of them.

### 6.1 Endpoint contract (`ref_client/http.py`)

```
POST /invocations
  Authorization: Bearer <Cognito access token>            required; nothing else authenticates
  X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: <session>  AWS only; set by the page, forwarded by the proxy
  {"input": "<text, 1–500 chars>", "session_id": "<opaque, [A-Za-z0-9-]{33,128}>"}
→ 200 {"text": "...", "next_step": {...} | null, "tool_calls": [{"name": "...", "reason_codes": ["..."]}]}
GET /ping      → 200 {"status": "Healthy"}   (AgentCore Runtime HTTP contract)
GET /healthz   → 200 {"status": "ok"}        (compose, Helm)
GET /config.js → local only (TOWER_ENV=local): the page config rendered from env (§6.4); 404 otherwise
GET /          → services/web-chat static files when WEB_CHAT_DIR is set (local); AWS serves them from S3
```

Container port `REF_CLIENT_HTTP_PORT` (default `8080`, which Runtime requires); compose publishes it on `127.0.0.1:8083`. No streaming this week.

### 6.2 Rules, in the order the handler applies them

| # | Rule | Why |
|---|---|---|
| 1 | No `Authorization: Bearer <token>` (or a malformed one) → **401** before anything else: no Bedrock call, no Tower call. The agent does not verify the token. The Runtime's JWT authorizer and then Tower do (01 §4) | Verification is Tower's. The agent only refuses an obviously anonymous request, so a stray request costs no tokens |
| 2 | Invalid body → **422**. `input` matching the phone-number pattern (`\+?\d{10,15}`, or the shared rule `ref_client.privacy`: `+` and 8+ digits, grouped or not — decision 1, 2026-10-11; `transcript.has_number`) → **422** `no_numbers`, before the model and never logged | No number reaches a prompt (bind-and-alert-flows §2.3) |
| 3 | The agent calls Tower with the **same `Authorization` header, byte for byte**. `X-Tower-User` from the request is forwarded only when `TOWER_ENV=local` and dropped otherwise | One identity end to end: `user_id = sub` (D-A) |
| 4 | `next_step` in the response = the `next_step` of the **last tool result of the turn whose `kind` is not `none`**, copied verbatim. `null` if there is none. Never parsed from model text. A model reply containing a URL never produces a `next_step` | Rule 2: the model never produces a URL (D-C) |
| 5 | When `WEB_CHAT_BINDING_BASE_URL` is set, a `bind_line` whose `url` does not start with `WEB_CHAT_BINDING_BASE_URL + "/bind/"` → `next_step: null`, logged `NEXT_STEP_REJECTED`. The page applies the same check again | Defence in depth; no phishing-shaped output |
| 5b | Any `next_step` whose JSON matches the phone-number pattern (e.g. a 10-digit `carrier_support_number` copied through `call_carrier`) → `next_step: null`, logged `NEXT_STEP_REJECTED` (reason `number`). The page does not render `call_carrier`; the text already says to call the carrier | The privacy sweep holds over every response (as built, prompt 20 step 2) |
| 6 | `text` passes the §5 digit guard. `tool_calls` carries names and reason codes only, never `facts` | §5 |
| 7 | Tower answers 401 → **401** (the page signs in again). Tower unreachable or timed out → **502** `tower_unavailable`. Bedrock error → **503** `agent_unavailable`. The model is never asked to improvise an answer | Degrade toward silence |
| 8 | One structured log line per request: a hash of `session_id`, tool names, reason codes, status, latency. Never the input, the reply, the token or a number | Privacy invariants |

### 6.3 Where it runs

| | Local (`TOWER_ENV=local`) | AWS |
|---|---|---|
| Agent | compose service `web-chat` (the `ref-client` image, HTTP app), `127.0.0.1:8083 → 8080`. The one-shot `ref-client` tools service for `make demo` is unchanged | AgentCore Runtime `aws_bedrockagentcore_agent_runtime.ref_client` (module `agentcore_runtime`, protocol HTTP, its **own** IAM role: Bedrock invoke + logs only, no DynamoDB, KMS or Gateway — §5), `custom_jwt_authorizer` with the Cognito pool, `Authorization` allow-listed |
| Page | served by the agent at `/` (same origin, no CORS) | S3 (private) + CloudFront (OAC), module `web_chat`; `config.js` uploaded by `make web-chat-sync` |
| Page → agent | same origin | Lambda function URL (module `web_chat`, `auth_type NONE`, CORS = the CloudFront origin only): `services/web-chat/proxy/handler.py` forwards `Authorization`, `Content-Type` and the Runtime session header to the agent runtime's `/invocations` and nothing else. It holds no credential and logs no header or body |
| Sign-in | the stub in §6.4 | Cognito Hosted UI, authorization code + PKCE, app client `web-chat` without a secret (module `cognito`) |
| Agent → Tower | `TOWER_URL=http://tower-mcp:8000/mcp` (Tower's container port), static `TOWER_BEARER` + `X-Tower-User` | `TOWER_URL` = output `tower_mcp_url`; the user's Cognito token, passed through |

**As built (prompt 20 step 2).** `ref_client/http.py` (`create_app(settings, agents)`, `app_from_env()`, run by `ref-client serve`, the image's default `CMD`; the CLI is the same `ENTRYPOINT` with `demo`/`say`/`corpus`), `ref_client/auth.py` (`bearer_header`), `ref_client/phrasebook.py`. `TowerConfig.authorization` carries the caller's header verbatim; `TowerClient` raises `TowerUnauthorized` on a 401 (seen through a response hook, because the MCP client reports every non-2xx as a generic error). **Agent choice:** `REF_AGENT=bedrock` lets the model choose; `scripted` — and `auto` without AWS credentials, which is the compose `web-chat` service, since no credentials are passed into it — looks the input up in an exact-phrase table (the demo lines + corpus entries with one tool, lower-cased, punctuation dropped) and reads `summary` verbatim. It is a lookup, not a model; an unknown sentence gets no tool call. Terraform sets `REF_AGENT=bedrock` on AWS.

### 6.4 The page (`services/web-chat/`)

`index.html`, `app.js`, `styles.css` (dark, the demo UI's palette), no framework, no build step. Sign in → a textbox → `POST AGENT_URL` with the token → render `text`. When `next_step.kind == "bind_line"` and `next_step.url` starts with `BINDING_BASE_URL + "/bind/"`, show "Your line isn't connected yet", the link (copyable), and "tap it on your phone, then ask again". Any other URL is not shown, and the page logs `NEXT_STEP_REJECTED` to the console. Other `next_step` kinds are not rendered (the text covers them). No SMS button (D-D), no QR code (D-E), no number field.

`config.js` keys: `COGNITO_DOMAIN`, `CLIENT_ID`, `REDIRECT_URI`, `AGENT_URL`, `BINDING_BASE_URL` (template `config.js.example`; none is a secret). On AWS `make web-chat-sync` renders it from the Terraform outputs. Locally `GET /config.js` renders it from the agent's env with `COGNITO_DOMAIN` empty, which switches the page to the **sign-in stub**: pick Asish or Mom; the page then sends `X-Tower-User: user-asish | user-mom` and the local static bearer (`LOCAL_BEARER`, the same `TOWER_BEARER` the CLI and the demo UI already hold). That value only exists with `TOWER_ENV=local` and is published on `127.0.0.1` only. It is the same trust boundary as the demo UI (11).

### 6.5 Failure behaviour

| Failure | Result |
|---|---|
| No or malformed bearer | 401; zero model calls (asserted with a counting fake) |
| Expired token | Runtime authorizer (AWS) or Tower → 401; the page restarts sign-in |
| Tower down | 502 `tower_unavailable`; the page shows "Tower is unavailable, try again" |
| Bedrock down | 503 `agent_unavailable`; nothing is phrased without a tool result |
| `next_step.url` outside `/bind/` | dropped by the agent and the page; `NEXT_STEP_REJECTED` |
| Proxy cannot reach the runtime | 502 from the Lambda; no retry |

### 6.6 Tests

- `services/ref-client/tests/test_http.py` (integration): 401 without a bearer and no model call; the bearer reaches a fake Tower unchanged; `next_step` byte-identical to the tool result's; a model reply with a URL gives `next_step: null`; a foreign URL → `null` + `NEXT_STEP_REJECTED`; `X-Tower-User` dropped unless local; input with a number → 422 and no model call; privacy sweep over responses and captured logs.
- `services/web-chat/tests/test_static.py` (unit): no secret in `config.js.example`; `app.js` contains the `/bind/` prefix check; no phone-number match in any file. `tests/test_proxy.py` (unit): only the three headers are forwarded; nothing is logged.
- `tests/e2e/test_web_chat_story.py` (`ENV=local`): sign-in stub → "Is my line OK?" → `NOT_BOUND` + link → bind with the simulated phone → ask again → `OK`. It is compared to `tests/e2e/golden/web-chat/web-chat.json` on tool calls and reason codes (the endpoint returns tool names, so the golden's `line: self` is the default the comparator fills in). To start from "not connected" it deletes Asish's `Lines` row in DynamoDB Local (the compose seed bound it); the bind in the story writes it back and a failure re-binds it.
- Also: `services/ref-client/tests/test_auth_and_phrasebook.py` (unit); `services/mock-carrier/tests/test_assume_mobile_data.py`; `services/binding-page/tests/test_session_ttl.py`, `test_assumed_mobile_data_bind.py`; `tests/aws/test_web_chat_scripts.py` (cognito_user, aws_seed user map, web_chat_sync — offline); `tests/aws/test_terraform_static.py` (the names table); `deploy/helm/tests/ref-client` (the `web` Deployment).

### 6.7 Cost and what it deliberately does not do

Cognito is in the free tier at two users. The second Runtime is billed per active request. CloudFront, S3 and the Lambda URL cost cents. Tower's hot path is unchanged: one DynamoDB read plus the carrier calls. The model is in the client, before Tower, as with Alexa+. It does not send SMS, draw a QR code, stream, hold carrier credentials, read tables, or offer the demo UI's carrier controls.

## 7. Showcase on its own

`make showcase-ref`: the three demo moments driven from the terminal against local Tower + mock, with TTS if available. Then `make corpus`: the selection table with pass/fail. See `testing-and-showcase.md` §2.9.
