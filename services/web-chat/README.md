# web-chat — the signed-in chat page in front of the reference client

The Alexa+ stand-in for the AWS demo and the fallback for the Alexa+ simulator. You sign in, type "Is my line OK?",
and read the answer. It is the reference client (09) — the same agent, prompts and tools — with a page in front.
Contract: [`docs/architecture/components/09-reference-client.md`](../../docs/architecture/components/09-reference-client.md) §6.

What is here:

| Path | What it is |
|---|---|
| `index.html`, `app.js`, `styles.css` | the page: plain HTML and one JS file, no framework, no build step, dark theme in the demo UI's palette |
| `config.js.example` | the page config template (`COGNITO_DOMAIN`, `CLIENT_ID`, `REDIRECT_URI`, `AGENT_URL`, `BINDING_BASE_URL`); none is a secret |
| `proxy/handler.py` | the Lambda function URL proxy (AWS): forwards `POST` bodies to the agent runtime with three headers only; standard library, zipped by Terraform |
| `tests/` | `test_static.py` (no secret, the `/bind/` check, no number in any file), `test_proxy.py` (three headers, no logs) |

This folder is not a Python package (it is excluded from the uv workspace); the agent is `services/ref-client`.

## What the page does

1. **Sign in.** AWS: Cognito Hosted UI, authorization code + PKCE, app client `web-chat` without a secret. The
   access token stays in `sessionStorage` for the tab. Local: a stub with two buttons, Asish and Mom (no password).
2. **Ask.** `POST AGENT_URL {"input", "session_id"}` with `Authorization: Bearer <token>`. Locally it also sends
   `X-Tower-User: user-asish | user-mom`; on AWS it sends `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`.
3. **Render.** The agent's `text`, as text. If `next_step.kind == "bind_line"` and `next_step.url` starts with
   `BINDING_BASE_URL + "/bind/"`, it shows "Your line isn't connected yet", the link (copyable) and "tap it on
   your phone, then ask again". Binding needs **one tap on the phone over mobile data** (Wi-Fi off). Any other URL
   is not shown, and the console logs `NEXT_STEP_REJECTED` (never the URL). Other `next_step` kinds are not rendered.
4. **Failures.** 401 → sign in again; 422 → "ask in words, no phone numbers"; 502 → "Tower is unavailable, try
   again"; 503 → "The agent is unavailable, try again".

What it does **not** do: send SMS (D-D), draw a QR code (D-E), take a phone number, build a URL, show a URL from
model text, stream, or offer the demo UI's carrier controls (doc 11). It holds no credential.

## Run locally

```bash
make up            # the stack, including the `web-chat` service (the ref-client image serving this page)
make web-chat      # prints (and opens, when a browser is around) http://127.0.0.1:8083/
```

Pick Asish, ask "Is my line OK?". In compose the agent is the scripted one (no AWS credentials are passed into the
container): it looks your sentence up in the demo script and the corpus (exact phrase, `ref_client/phrasebook.py`)
and reads Tower's `summary`. For Bedrock, run the agent on the host with `uv run ref-client serve` (services/ref-client
README, "As the web chat page"). The page config comes from the agent's `GET /config.js` (`TOWER_ENV=local` only,
published on 127.0.0.1 only), which also carries the local static bearer.

## Deploy (AWS)

Terraform creates the bucket, CloudFront, the Lambda URL proxy (`modules/web_chat`), the Cognito pool and app
client (`modules/cognito`) and the agent runtime (`modules/agentcore_runtime`, `enable_web_chat = true`). Then:

```bash
make cognito-users   # asish + mom in the pool → artifacts/cognito-users.json (gitignored); passwords from env or a prompt
make seed-aws        # writes their Cognito subs into Lines and Grants
make web-chat-sync   # renders config.js from the outputs, uploads this folder, invalidates CloudFront
make web-chat-url    # prints the page URL
```

Full order and preconditions: [`deployment-agentcore.md`](../../docs/architecture/deployment-agentcore.md) step 10.

## Config (`config.js`)

| Key | Local (`GET /config.js`) | AWS (`make web-chat-sync`) |
|---|---|---|
| `COGNITO_DOMAIN` | empty → sign-in stub | output `cognito_hosted_ui_url` |
| `CLIENT_ID` | empty | output `cognito_client_id` |
| `REDIRECT_URI` | empty | output `web_chat_url` |
| `AGENT_URL` | `/invocations` (same origin) | output `agent_url` (the Lambda function URL) |
| `BINDING_BASE_URL` | `WEB_CHAT_BINDING_BASE_URL` | output `binding_url` |
| `LOCAL_BEARER` | the local `TOWER_BEARER` | absent |

The proxy reads one env var, `AGENT_INVOKE_URL` (set by Terraform: the agent runtime's invocation URL).

## Test

```bash
uv run pytest services/web-chat -q
uv run pytest services/ref-client/tests/test_http.py -m "unit or integration" -q   # the agent endpoint
make test-e2e ENV=local                                                               # tests/e2e/test_web_chat_story.py
```

## Showcase

`make up && make web-chat`, sign in as Asish, ask "Is my line OK?". For the bind story: the e2e test
(`tests/e2e/test_web_chat_story.py`) shows the order — `NOT_BOUND` + link → one tap on the phone → ask again → `OK`.
