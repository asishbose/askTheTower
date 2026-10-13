# ref-client

The reference client: a voice-shaped client for Tower that isn't Alexa+
(design: [`docs/architecture/components/09-reference-client.md`](../../docs/architecture/components/09-reference-client.md)).
It connects to Tower over MCP Streamable HTTP exactly as Alexa+ would, lets a Bedrock model choose Tower's tools
from their descriptions, and writes transcripts. Three jobs:

1. **Fallback demo surface** — `ref-client demo` drives the three demo moments and the transplant story from a
   terminal, with the mock carrier's admin calls and clock advances between utterances. The transplant story
   starts with a Settings step: `POST {BINDING_URL}/_admin/watch-settings` saves Asish's `transplant` profile
   with contacts `[user-partner, user-neighbour]` (06 §11.4); `Control.reset` deletes that Watch first. After
   the +20 and +15 jumps the story waits (poll 0.2 s, at most 10 s) until Alerts' sent list shows the partner's
   (`escalation[0]`) and then the neighbour's (`escalation[1]`) text; a missing one is a `DEMO MISMATCH`, no
   sent list is a skipped wait, printed as `[alerts] … skipped: <why>` (09 §4).
2. **Tool-selection regression** — `ref-client corpus` runs ~40 real phrasings (`corpus/phrasings.yaml`) and
   checks the model picked the expected tool and arguments. A miss is a description bug.
3. **The one place a model sits** — `src/ref_client/agent.py` is the only language-model call in the repository
   (`tests/test_only_llm_call.py` greps for any other).
4. **The web chat page's agent** — `ref-client serve` (the image's default command) exposes the same agent over
   HTTP behind `services/web-chat` (see [As the web chat page](#as-the-web-chat-page)).

What it doesn't do: hold carrier credentials, touch DynamoDB, or decide policy. Nothing in the system depends on
it (`tests/test_fallback.py`).

## Layout

| Path | |
|---|---|
| `src/ref_client/mcp_client.py` | `TowerClient`: Streamable HTTP, `Authorization: Bearer $TOWER_BEARER`, local `X-Tower-User`; tools discovered at connect |
| `src/ref_client/agent.py` | `BedrockAgent` (Converse API + tool use, temperature 0, digit guard) and `ScriptedAgent` (no model) |
| `src/ref_client/demo.py` | the four stories (showcase order §4 steps 4–7) and the mock / grant controls |
| `src/ref_client/transcript.py` | transcript shape, phone-number redaction before write (`redact`, `has_number`), `invented_digits`, `compare(golden, transcript)` |
| `src/ref_client/privacy.py` | the shared phone-number rule (decision 1): `+` and 8+ digits, or a bare 10+ digit run off the ISO-8601/epoch-ms allowlist; used by `tests/privacy/patterns.py` and `demo_ui.redact` |
| `src/ref_client/corpus_runner.py` | load the corpus, score, markdown table, `CORPUS_MIN_PASS` gate |
| `src/ref_client/run.py` | the CLI (`say`, `demo`, `corpus`, `serve`) |
| `src/ref_client/http.py` | the HTTP app behind the web chat page: `POST /invocations`, `/ping`, `/healthz`, local `/config.js`, the page at `/` (09 §6) |
| `src/ref_client/auth.py` | bearer extraction and pass-through (verification is Tower's) |
| `src/ref_client/phrasebook.py` | the scripted agent's exact-phrase lookup for free text (demo lines + corpus) |
| `src/ref_client/voice.py` | optional mic → whisper → agent → Polly → speaker, behind `REF_VOICE=1` |
| `prompts/ref-client/system.md` | the system prompt (one role, one file) |
| `corpus/phrasings.yaml` | the phrasing corpus |
| `../../tests/e2e/golden/*.json` | golden transcripts: tool calls + reason codes, hand-written from `artifacts/policy-table.md` |

## Run

```bash
make demo                      # = ref-client demo --env $(ENV): transcripts → artifacts/transcripts/<story>.json
make corpus                    # = ref-client corpus: table → artifacts/corpus.md (needs Bedrock)
make showcase-ref              # both; REF_VOICE=1 speaks the answers
uv run ref-client say "is my line ok" --user asish
uv run pytest services/ref-client -q
```

**Agents.** `--agent auto` (default) uses Bedrock when AWS credentials are configured; otherwise the demo runs
with the **scripted** agent — tool calls come from the demo script and `summary` is read verbatim — and says so on
its first line. The scripted run proves Tower's side of each transcript (tool call → reason codes), not tool
selection; `corpus` and `say` need the model (without credentials `corpus` prints SKIPPED and exits 0; `say`
exits 3). `--agent bedrock` never falls back.

**Exit codes.** 0 ok · 1 demo reason codes differ from the script / corpus below the gate · 2 Tower or a demo
control unreachable · 3 Bedrock unavailable (the message names the model, region and what to check).

**The demo's controls.** The script calls the mock's `/_admin/scenarios/load`, `/_admin/clock` and
`/_admin/lines/{msisdn}/events` (numbers are read from the mock's state by simulated client id and never printed),
and for moment 3 the binding page's local grant admin: `POST {BINDING_URL}/_admin/grants`
`{owner_user_id, grantee_user_id, grant, alias, action: "revoke"|"grant"}` (idempotent; `TOWER_ENV=local`,
`BIND_ADMIN=1`). Locally Tower follows the mock's clock (`TOWER_CLOCK_URL`).

**Re-running.** On compose, run the demo through `make demo`. It runs `make seed` first, which clears the
previous run's Watches and Alerts state. A Watch's `last_state` from the old timeline would otherwise outlive
the mock's scenario reset, and Tower's stored-state path would answer from it.

`--pause` waits for enter before each story ("press enter for step 4: …"). `make showcase` uses it for steps 4–6.

## Config

| Variable | Default | |
|---|---|---|
| `TOWER_URL` | `http://localhost:8080/mcp` | Tower's MCP endpoint (`mk/vars.mk`) |
| `TOWER_BEARER` | — | local static bearer, or the JWT on AWS |
| `MOCK_URL` / `BINDING_URL` | `http://localhost:8443` / `http://localhost:8081` | demo controls (local) |
| `MOCK_ADMIN_TOKEN` | — | bearer for the mock's mutating `/_admin` routes (08 §3); `make demo` reads it from `deploy/compose/.env`. The demo fires events by the line's `ref` (`/_admin/state?view=refs`), never by number |
| `DEMO_ALERTS_SETTLE_S` | `2.5` on ENV=local, else `0` | `demo`: pause after the transplant story's clock jumps so Alerts' scheduler sees each one before the line changes again (06 §11.4) |
| `ALERTS_URL` | `http://localhost:8082` | `demo`: Alerts' sent list (`GET /internal/sent?after=<n>`, 06 §3.1) for the transplant story's waits (09 §4) |
| `ALERTS_INTERNAL_BEARER` / `DEMO_ADMIN_BEARER` | — | `demo`: the sent list's bearer, `ALERTS_INTERNAL_BEARER` except on ENV=aws, where it is `DEMO_ADMIN_BEARER` (decision 1). `make demo` exports the local one from `deploy/compose/.env` (`INTERNAL_BEARER`). Without it the waits are skipped and the run says so |
| `BEDROCK_MODEL_ID` | `amazon.nova-micro-v1:0` | any Converse model with tool use |
| `AWS_REGION` | `us-east-1` | Bedrock (and Polly) region |
| `BEDROCK_ENDPOINT_URL` | — | override the Bedrock endpoint (tests point it at a closed port) |
| `BEDROCK_ASSUME_ROLE_ARN` | — | call the model as this role in another account (sts:AssumeRole, refreshed by botocore): for an account whose Bedrock daily token quota is 0. Only the model call crosses accounts |
| `REF_AGENT` | `auto` | `auto` · `bedrock` · `scripted` |
| `REF_VOICE` | `0` | `1` = speak with Polly; `say -` listens on the mic |
| `CORPUS_MIN_PASS` | `0.9` | corpus gate |
| `REF_ARTIFACTS_DIR` | `artifacts` | where transcripts / corpus.md go |
| `REF_SYSTEM_PROMPT`, `REF_CORPUS` | packaged files | override the prompt / corpus path |
| `REF_CLIENT_HTTP_PORT` | `8080` | `serve`: listen port (AgentCore Runtime requires 8080; compose publishes `127.0.0.1:8083`) |
| `TOWER_ENV` | `local` | `serve`: `local` forwards `X-Tower-User` and serves `/config.js` (the sign-in stub, with `TOWER_BEARER`); anything else does neither |
| `WEB_CHAT_BINDING_BASE_URL` | — | `serve`: a `bind_line` URL outside `<this>/bind/` → `next_step: null` + `NEXT_STEP_REJECTED` |
| `WEB_CHAT_DIR` | — | `serve`: serve the page from this folder at `/` (compose: `/app/web-chat` in the image) |
| `TOWER_TIMEOUT_S` | `10` | Tower call timeout |

**Cost.** Nova Micro on demand lists at $0.035 per million input tokens and $0.14 per million output tokens (us-east-1; not re-verified in the autonomous build). One model
demo run is about nine utterances × two Converse calls × ~1.2 k input tokens ≈ 22 k input + ~1 k output tokens,
**≈ $0.001 per demo run**; a 42-entry corpus run ≈ $0.004. These are estimates; the CLI prints the token counts
of a corpus run (`tokens in/out`) so the real figure can replace them.

## Showcase on its own

`make showcase-ref` against the local stack: the three moments and the transplant story printed line by line
(`you>`, the tool call and reason codes, `ref>`), then the corpus table. Transcripts in `artifacts/transcripts/`
are the evidence a judge can read without a device.

## Tests

| Test | Layer | |
|---|---|---|
| `test_transcripts.py` | integration (+ e2e Bedrock, skipped without credentials) | scripted demo against in-process Tower + mock + DynamoDB vs `tests/e2e/golden/*.json` |
| `test_no_invented_digits.py` | unit + integration (+ e2e Bedrock) | 09 §5 on every ToolResult of the golden run; the agent's guard |
| `test_corpus.py` | unit + integration (+ e2e Bedrock) | corpus shape, scoring, descriptions verbatim, live run gated at 90 % |
| `test_fallback.py` | unit + integration | Bedrock unreachable → clear error; Tower unaffected; nothing imports `ref_client` |
| `test_only_llm_call.py` | unit | no other model call in the repo |
| `test_demo_control.py` | unit | grant admin contract; CLI exit codes |
| `test_http.py` | unit + integration | 09 §6.2: 401 before any model/Tower call; bearer byte for byte to a real in-process Tower; `X-Tower-User` only locally; `next_step` verbatim, never from model text; foreign URL → `NEXT_STEP_REJECTED`; 401/502/503 mapping; privacy sweep over responses and logs |
| `test_auth_and_phrasebook.py` | unit | the bearer rule; the scripted lookup covers every demo line |

`REF_TRANSCRIPTS_WRITE=1 uv run pytest services/ref-client/tests/test_transcripts.py` rewrites
`artifacts/transcripts/*.json`. `REF_DDB_BACKEND=moto|local` picks the DynamoDB (default: DynamoDB Local when
`docker info` works, else moto).

## As the web chat page

The Alexa+ stand-in for the AWS demo (09 §6; flows in `docs/architecture/bind-and-alert-flows.md`). The same
agent, prompt and Tower tools, over HTTP, with `services/web-chat` in front.

```
POST /invocations   Authorization: Bearer <token>   {"input": "<1–500 chars>", "session_id": "<[A-Za-z0-9-]{33,128}>"}
  → 200 {"text": "...", "next_step": {...} | null, "tool_calls": [{"name": "...", "reason_codes": ["..."]}]}
  → 401 unauthorized (no/malformed bearer — before any model call — or Tower refused it)
  → 422 invalid_request | no_numbers (a phone-number-shaped run in the input; it never reaches the model or a log)
  → 502 tower_unavailable · 503 agent_unavailable
GET /ping → {"status": "Healthy"}   GET /healthz → {"status": "ok"}   GET /config.js (TOWER_ENV=local only)
```

The rules, in order (09 §6.2): refuse an anonymous request; validate; forward the caller's `Authorization` header
to Tower byte for byte (`X-Tower-User` too, locally only); `next_step` = the last non-`none` tool result's, copied
verbatim — never from model text; drop a `bind_line` outside `WEB_CHAT_BINDING_BASE_URL/bind/` and any step
carrying a phone-number-shaped value (logged `NEXT_STEP_REJECTED`, without the URL); one JSON log line per request
(hash of the session id, tools, reason codes, status, latency — never the input, reply, token or a number).

**Agent.** `REF_AGENT=bedrock` lets the model choose the tool. `scripted` (and `auto` without AWS credentials, as in
compose) looks the sentence up in `phrasebook.py` — the demo lines and the corpus, exact phrase after lower-casing
and dropping punctuation — and reads `summary` verbatim; an unknown sentence gets no tool call and says so.

**Local run.** `make up` starts the compose service `web-chat` (this image, `serve`, `127.0.0.1:8083 → 8080`,
`WEB_CHAT_DIR=/app/web-chat`); `make web-chat` opens it. On the host instead:

```bash
TOWER_URL=http://localhost:8080/mcp TOWER_BEARER=$(sed -n 's/^TOWER_BEARER=//p' deploy/compose/.env) \
  WEB_CHAT_BINDING_BASE_URL=http://localhost:8081 WEB_CHAT_DIR=services/web-chat REF_CLIENT_HTTP_PORT=8083 \
  uv run ref-client serve      # Bedrock when AWS credentials resolve
```

**AWS run.** Terraform runs this image as a second AgentCore Runtime (`aws_bedrockagentcore_agent_runtime.ref_client`,
protocol HTTP, its own role: Bedrock invoke, logs, ECR pull) with `TOWER_URL` = Tower's invocation URL,
`TOWER_ENV=aws`, `REF_AGENT=bedrock`, `WEB_CHAT_BINDING_BASE_URL` = the binding page, a Cognito JWT authorizer
and `Authorization` allow-listed. The page reaches it through the Lambda URL proxy (`services/web-chat/proxy/`).
Order: `deployment-agentcore.md` step 10 (`make cognito-users` → `make seed-aws` → `make web-chat-sync` →
`make web-chat-url`).

## As a library (the demo UI)

`run_demo(agent_for, control, *, reset=True, on_step=None, replay=())` is what `services/demo-ui` calls (doc 11, G4):
`on_step` receives a `StepReport` per step (story, `<story>#<n>`, kind, narration, expected vs actual reason
codes, `ok`, `elapsed_ms`, `replay`, the `Turn`); `reset=False` skips the scenario reload. No product component
imports `ref_client` (09 §4 "Who may depend on it").
