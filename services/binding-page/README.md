# binding-page

The one tap. A person opens a link on their phone over mobile data and taps **Verify**. The carrier's Number Verification asserts the number, and the line is bound to their account. Nobody types a number at any point. The same page then lets the line-holder share the line (`watch` or `reachability`, by invite code, under an alias), revoke a share in one tap, and read the audit log for their own line with its chain status.

Design: [`docs/architecture/components/04-consent-and-binding.md`](../../docs/architecture/components/04-consent-and-binding.md) §1–3, §6–8; [`e2e-wiring.md`](../../docs/architecture/e2e-wiring.md) §5 (Path C). Build prompt: [`prompts/09-binding-page.md`](../../prompts/09-binding-page.md).

What the page does not do:
- It never shows or accepts a full phone number. The "Line connected" page shows only the masked last four (`•••• 0101`).
- It has no revoke route that works without the page session, and there is no revoke by voice.
- Watchers cannot read the audit of a line they watch. That view is for the owner only.

## Pages and routes

| Route | What |
|---|---|
| `GET /bind/{token}` | "Turn Wi-Fi off, tap Verify". No input fields. |
| `POST /bind/{token}/verify` | Starts the carrier's auth-code flow. The OAuth `state` is signed with the token's `user_id`. |
| `GET /bind/callback` | Exchanges the code, runs Number Verification (`phoneNumberShare`), consumes the token, calls `bind_line`, then shows "Line connected" and sets the page session. |
| `GET /me` | My lines, a **Watching** card per owned line (alerts on/off, read-only; profile by behaviour; up to three contacts in order from the line's active `watch` grantees), grants I gave and received, and my invite code. Never the word "transplant", never a number. |
| `POST /me/lines/{line_id}/watch-settings` | `{profile, contact_1..3, csrf}`: the line-holder's profile and contacts (04 §9). Owner only (403); contacts need an active `watch` grant, no owner, no duplicates, at most three (422); `transplant`/`care` need a contact (422). One write on the owner's Watch, one audit row (`watch_line`/`binding`), and if alerts are on, `POST {ALERTS_INTERNAL_URL}/internal/watch` (failure swallowed). Store down → 503; audit failure → 500 with the settings kept. 303 → `/me`. |
| `POST /me/invite` | A fresh invite code (8 letters, 24 h, single use). |
| `POST /grants` | `{invite_code, kind, alias}` creates a grant on my line. An alias collision returns 409. |
| `POST /grants/{id}/revoke` | Revokes a grant. Needs the page session and a CSRF token. Revoking `watch` also disables the grantee's Watch on the line and removes them from the line-holder's contact chain (`tower_consent.revoke`); Alerts drops the line's subscriptions on its next poll (06 §4). |
| `GET /me/lines/{line_id}/audit` | The audit rows and chain status. Owner only; anyone else gets 403. |
| `GET /_admin/tables`, `/_admin/resolve`, `POST /_admin/bind-tokens` | The showcase's admin view. Served only with `TOWER_ENV=local` and `BIND_ADMIN=1`; returns 404 otherwise (whatever the query). `?format=json` for scripts; `?table=Grants` (any one table name, exact case; another name is 422) returns only that table — the demo UI's pane 4 uses it (04 §8). |
| `POST /_admin/grants {owner_user_id, grantee_user_id, grant, alias, action}` | Local admin, same gate. Grants or revokes on the owner's only line, or on `line_id` if one is given, through the same `tower_consent.grant`/`revoke` that `/me` uses. It is idempotent and answers `{line_id, grantee_user_id, grant, active, changed}`. The compose seed and `ref-client demo` (moment 3) use it. |
| `POST /_admin/watch-settings {owner_user_id, profile, contacts, line_id?, reset?}` | Local admin, same gate. The form's save path (same validation → 422, same audit row, same Alerts call); `reset: true` deletes the owner's Watch. `ref-client demo`'s transplant Settings step uses it. |
| `GET /healthz` | `{"ok": true}` |

**OAuth `state`.** `POST …/verify` makes a random nonce `n`. The browser goes to the carrier with `state = sign({u: user_id, n, exp +10 min})`. The bind token itself never leaves the site: it travels in an HttpOnly cookie `atb_flow = sign({t: token, n})` scoped to `/bind`. The callback requires both values with the same `n`, which ties the redirect to the browser that started the flow. It then consumes the token *for `u`*, so a token issued to another user is refused. The signatures are HMAC-SHA256 with `SESSION_SECRET`, re-lettered `a`–`p` so that no signed value can match the phone-number regex (`session.py`).

**Mobile data, local vs AWS (`mobile_data.py`).** Locally (`TOWER_ENV=local`) a bind link with `?as=phone-asish` makes the page call the mock carrier's authorize endpoint itself with `X-Mock-Client-Id: phone-asish`, because a browser cannot add a header to a redirect. Without `?as=` the header is missing, which simulates Wi-Fi being on, and the carrier refuses. On AWS the module does nothing: `?as=` is ignored, the browser goes to the carrier, and the carrier's own network attribution identifies the line.

## Run

```bash
make showcase-binding                      # self-contained: DynamoDB (Local/moto), mock carrier, page; QR code + script
uv run python -m binding_page              # just the page, configured from the environment (see below)
docker build -f services/binding-page/Dockerfile -t ask-the-tower/binding-page .   # from the repo root
uv run pytest services/binding-page -q     # tests (moto + DynamoDB Local when Docker is up; TOWER_DDB_BACKENDS=moto to narrow)
```

On AWS the same image runs as a Lambda container behind API Gateway (HTTP API). Set the handler to `binding_page.app.handler` (Mangum).

## Config

| Variable | Default | Meaning |
|---|---|---|
| `TOWER_ENV` | `local` | `local` turns on the mobile-data simulation and allows the admin view. Any other value (`aws`, `eks`) makes both inert. |
| `BASE_URL` | `http://localhost:8081` | Public base URL of the page, used for links and the QR code. |
| `BIND_REDIRECT_URI` | `<BASE_URL>/bind/callback` | The OAuth redirect URI registered with the carrier. The mock accepts `http://localhost…`. |
| `SESSION_SECRET` | required, at least 16 chars | HMAC key for the OAuth state, the flow cookie, the session cookie and CSRF tokens. |
| `SESSION_TTL_S` | `1800` | Page session lifetime (cookie `Max-Age` and the signed expiry). The session is set by a successful bind. On AWS, Terraform `binding_session_ttl_s` sets `86400` (24 h, D-H) so the line-holder can come back to revoke during the demo day (04 §6). |
| `BIND_ADMIN` | off | `1` serves `/_admin/*`, but only when `TOWER_ENV=local`. |
| `ALERTS_INTERNAL_URL`, `ALERTS_INTERNAL_BEARER` | unset | Alerts' `POST /internal/watch`, called after a watch-settings save on an enabled Watch. Unset → a log-only stub; the Alerts polls still find the Watch. |
| `PORT`, `HOST`, `LOG_LEVEL` | `8081`, `0.0.0.0`, `info` | uvicorn. Access logs are off because paths carry bind tokens. |
| `TOWER_DYNAMODB_ENDPOINT` (alias `DYNAMO_ENDPOINT`), `TOWER_TABLE_PREFIX`, `AWS_REGION` | unset | The consent and audit store. Leave the endpoint unset on AWS. |
| `TOWER_LINE_ID_KEY`, `TOWER_MSISDN_KEY` (local) / `TOWER_KMS_HMAC_KEY_ID`, `TOWER_KMS_KEY_ID` (AWS) | | `line_id` HMAC, `msisdn_enc`, and the audit-trim signer. |
| `CARRIER_CLIENT` | `direct` | `direct` means `DirectClient`; `gateway` means AgentCore Gateway + Identity. |
| `CARRIER_BASE_URL`, `CARRIER_AUTHORIZE_URL`, `CARRIER_TOKEN_URL`, `CARRIER_BACKEND` | see `camara_client.config` | The carrier endpoints. |
| `CARRIER_CLIENT_ID` | `binding-page` | The OAuth client. |
| `CARRIER_SCOPES` | `number-verification` | |
| `CARRIER_PROFILE` | `proactive` | 5 s timeout. Number Verification is never retried. |
| `CARRIER_SECRET_REF` / `CARRIER_CLIENT_SECRET` | `env:CARRIER_CLIENT_SECRET` | The client secret. On AWS, Identity holds it. |
| `CARRIER_GATEWAY_URL`, `CARRIER_GATEWAY_TOOLS` | | Used only when `CARRIER_CLIENT=gateway`. |

[`.env.example`](.env.example) has placeholders only.

## Showcase (`make showcase-binding`, testing-and-showcase §2.4)

The script reuses a running stack if `BINDING_URL` (default `http://localhost:8081`) already serves `/_admin`. Otherwise it starts its own stack:
- DynamoDB: `DYNAMO_ENDPOINT` if it answers, else a throw-away DynamoDB Local container, else moto in server mode.
- The mock carrier on `MOCK_PORT` (8443).
- The page on `PORT` (8081), with random keys for the run.

It then prints a terminal QR code for `BASE_URL/bind/<fresh token>?as=phone-asish`, followed by the steps:
1. "Wi-Fi on" refused.
2. The one tap.
3. Mom binds her line.
4. Asish creates an invite code.
5. Mom grants `watch` as "mom".
6. Show the tables and `resolve`.
7. Revoke, and `resolve` flips.
8. The audit view, "Log verified".

`BASE_URL` defaults to this machine's LAN address so that a phone on the same Wi-Fi can open the link. On WSL2 that is the VM's address: use mirrored networking or a `netsh portproxy`, or set `BASE_URL` yourself. `--print-only` prints the script without starting anything. `--smoke` starts the stack, checks that the link answers, and exits.

Screenshots of the five pages at phone size come from the Playwright test:
`BIND_SCREENSHOTS_DIR=artifacts/screenshots/binding-page uv run pytest services/binding-page/tests/test_playwright.py`. It is skipped, with the reason, when Chromium for Playwright isn't installed (`uv run playwright install chromium`).

## Reuse with your own CAMARA client

The page is the phone half of the consent kit ([`packages/tower-consent`](../../packages/tower-consent/README.md#reuse-with-your-own-camara-client)).
It speaks CAMARA Number Verification through `camara_client`, so pointing it at another carrier means configuring it, not changing code:

- **Carrier:** set `CARRIER_BASE_URL`, `CARRIER_AUTHORIZE_URL`, `CARRIER_TOKEN_URL`, `CARRIER_CLIENT_ID` and the secret (`CARRIER_SECRET_REF`) to your sandbox's values. Register `BIND_REDIRECT_URI` with that carrier. Set `TOWER_ENV` to anything but `local`, so the `X-Mock-Client-Id` simulation and `/_admin` are both off.
- **Store:** the same `TOWER_*` table and key variables as `tower-consent`. Your MCP server or app then calls `tower_consent.resolve` before each CAMARA request.
- **Issuing a bind link:** your service creates a single-use bind token for its `user_id` (`tower_consent.create_bind_token`, 10 minutes) and sends `BASE_URL/bind/<token>` to the user's phone. The person opens it over mobile data and taps once. Binding needs that one tap on the phone, over mobile data; the carrier's network attribution is what proves the number.
- **Run it** with `uv run python -m binding_page` or the image above. On AWS it runs as a Lambda container (`binding_page.app.handler`).
