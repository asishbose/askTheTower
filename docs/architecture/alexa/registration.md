# Alexa+ registration — runbook

> **Status: not run.** The autonomous build (RUN-ALL step 15) had no Alexa+ account and no AWS credentials, so
> this is the runbook with the exact steps to take. Nothing below has been observed yet. Replace each
> `TODO(human)` with what happened, and add a screenshot per step under `artifacts/screenshots/alexa/`. Redact
> client secrets, tokens and the tunnel hostname before saving.
>
> **Verdict:** ☐ simulator works · ☐ fallback (reference client). Fill in after step 4.

Inputs: `components/01-alexa-surface.md` (all of it), spike A (`docs/architecture/spikes/A-alexa-plus-toolkit.md`
and `scripts/spikes/a_alexa_echo.py`, prompt 02), and `testing-and-showcase.md` §2.1 and §4 steps 4–6.
Outputs: `simulator-run.md`, `friction-log.md`, `artifacts/alexa-simulator.mp4`,
`artifacts/transcripts/alexa-*.json`, and the Alexa+ MCP Toolkit section of
`docs/submission/product-feedback.md`.

## 0. Before you start

- [ ] **Spike A has run** (prompt 02). Its captured request (`artifacts/spikes/A-capture.jsonl`, analysed by
  `uv run python scripts/spikes/a_alexa_echo.py analyse artifacts/spikes/A-capture.jsonl`) shows the inbound
  identity. If it is **not** `Authorization: Bearer <JWT>` with a `sub`, stop here: change
  `services/tower-mcp/src/tower_mcp/auth.py` to the shape you saw, update `components/01` §4 and
  `tests/test_auth.py`, and run `uv run pytest services/tower-mcp/tests/test_auth.py -q`.
- [ ] If spike A could not get toolkit access from Canada, skip to **§8 Fallback**. That is a planned outcome.
- [ ] Docker, `make up` working, and a tunnel client (`cloudflared` or `ngrok`) on the machine.
- [ ] An AWS account for the identity provider (Amazon Cognito, step 1). This is the only AWS resource the local
  path needs.

## 1. Where Tower runs for the simulator

| Path | Tower URL Alexa+ calls | When |
|---|---|---|
| **Local + tunnel (planned for the run)** | `https://<tunnel-host>/mcp` → compose Tower on `localhost:8080` | Always works. The mock admin calls and `TOWER_CLOCK_URL` (local only) are what make the three moments repeatable. §2.1 says "web simulator → local Tower → mock". |
| AgentCore Runtime (prompt 13) | `terraform -chdir=deploy/terraform output -raw tower_mcp_url` | After `make deploy ENV=aws`. Runtime's custom JWT authorizer checks the same token first. The mock's admin is not public on AWS (`aws_seed.py` drives it over ECS Exec), so the moments are slower to stage. |

TODO(human): record which path was used and why.

## 2. Account linking: identity provider (Amazon Cognito)

Alexa account linking is OAuth 2.0 authorization-code: the user signs in to **our** identity provider, and Alexa+
stores the access token and sends it with every tool call. Tower verifies that token and uses `sub` as
`user_id` (RUN-ALL Decisions; `auth.py`). Cognito access tokens carry `client_id` and **no `aud`**, so Tower
checks `TOWER_JWT_CLIENT_IDS`, not `TOWER_JWT_AUDIENCE`. This mirrors Runtime's `allowed_clients`.

**On AWS the pool comes from Terraform (prompt 20).** `make deploy` creates it with `modules/cognito` (pool, Hosted UI domain and the `web-chat` app client; `deployment-agentcore.md` step 6), and `make cognito-users` creates `asish` and `mom`. Then only the Alexa client is made here, on that pool, because it needs a secret and the toolkit's redirect URLs:

```bash
POOL=$(terraform -chdir=deploy/terraform output -raw cognito_pool_id)
aws cognito-idp create-user-pool-client --region us-east-1 --user-pool-id $POOL --client-name alexa-link --generate-secret \
  --allowed-o-auth-flows code --allowed-o-auth-flows-user-pool-client --allowed-o-auth-scopes openid \
  --supported-identity-providers COGNITO --callback-urls "https://<alexa-redirect-url-1>" "https://<alexa-redirect-url-2>"
```

Hosted UI base for step 4: output `cognito_hosted_ui_url`. **Fallback, without the Terraform stack** (the local + tunnel path), create the pool by hand:

```bash
REGION=us-east-1
POOL=$(aws cognito-idp create-user-pool --region $REGION --pool-name ask-the-tower-alexa \
  --admin-create-user-config AllowAdminCreateUserOnly=true --query UserPool.Id --output text)
aws cognito-idp create-user-pool-domain --region $REGION --user-pool-id $POOL --domain ask-the-tower-<suffix>
# Callback URLs: the redirect URLs the Alexa+ toolkit shows in step 4 (fill in, then re-run update-user-pool-client).
CLIENT=$(aws cognito-idp create-user-pool-client --region $REGION --user-pool-id $POOL \
  --client-name alexa-link --generate-secret \
  --allowed-o-auth-flows code --allowed-o-auth-flows-user-pool-client \
  --allowed-o-auth-scopes openid --supported-identity-providers COGNITO \
  --callback-urls "https://<alexa-redirect-url-1>" "https://<alexa-redirect-url-2>" \
  --query UserPoolClient.ClientId --output text)
aws cognito-idp admin-create-user --region $REGION --user-pool-id $POOL --username asish \
  --temporary-password '<set-at-first-sign-in>' --message-action SUPPRESS
aws cognito-idp admin-get-user --region $REGION --user-pool-id $POOL --username asish \
  --query "UserAttributes[?Name=='sub'].Value" --output text      # → the user_id Tower will see
```

Endpoints for step 4: authorization `https://ask-the-tower-<suffix>.auth.$REGION.amazoncognito.com/oauth2/authorize`,
token `…/oauth2/token`. The client secret comes from
`aws cognito-idp describe-user-pool-client … --query UserPoolClient.ClientSecret`. Paste it into the toolkit
form only. Never put it in the repo, `.env.example`, a screenshot or the video.

**One linked account, not two.** In steps 4–6 only Asish speaks to Alexa+. Mom is the line-holder who granted
`watch`, and she is a consent-store user (`user-mom`, bound by the seed); she never needs an Amazon account.
So the developer's own Amazon account is linked to Cognito user `asish`. If the simulator lets you link a second
account, a Cognito user `mom` would let Mom ask about her own line. That is not part of the demo, so it is not
needed.

TODO(human): whether the simulator allows a second linked account (decides 01 §4's note).

## 3. Tower configured for the token, and the tunnel

`deploy/compose/.env` (gitignored). The keys are listed in `.env.example`:

```bash
TOWER_JWKS_URL=https://cognito-idp.us-east-1.amazonaws.com/<POOL>/.well-known/jwks.json
TOWER_JWT_ISSUER=https://cognito-idp.us-east-1.amazonaws.com/<POOL>
TOWER_JWT_AUDIENCE=
TOWER_JWT_CLIENT_IDS=<CLIENT>
TOWER_CALL_LOG=1
```

```bash
make up                                            # stack + seed (user-asish, user-mom, Mom's grant)
docker compose -f deploy/compose/docker-compose.yml -f deploy/compose/docker-compose.alexa.yml up -d tower-mcp
uv run python services/tower-mcp/scripts/showcase_alexa.py link --sub <sub from step 2>
cloudflared tunnel --url http://localhost:8080     # → https://<random>.trycloudflare.com
curl -s https://<tunnel-host>/healthz              # {"status":"ok",...}
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://<tunnel-host>/mcp   # 401 (no bearer)
```

`link` makes the linked identity (the `sub`, mapped by `safe_user_id` exactly as Tower maps it) the holder of
Asish's demo line and Mom's `watch` grantee under the alias "mom". It uses the compose seed's one-tap binding
flow. Afterwards the reference client's `make demo` (as `user-asish`) needs `make down && make up` again.

**Exposure while the tunnel is up.** Local mode also accepts the static `TOWER_BEARER` + `X-Tower-User` from
anyone who has the bearer. `make up` generates the bearer randomly per machine. Don't paste it anywhere, and
stop the tunnel as soon as the run is done.

AgentCore path instead: Tower's issuer, JWKS and the `web-chat` client already come from `modules/cognito`. Add the
`alexa-link` client: `tower_jwt_allowed_clients = ["<CLIENT>"]` in `deploy/terraform/envs/aws.tfvars`, then
`make deploy ENV=aws`. Terraform passes `[web-chat client] + that list` to the Runtime authorizer and to Tower as
`TOWER_JWT_CLIENT_IDS`. Only with a hand-made pool also set `tower_jwt_discovery_url`, `tower_jwks_url` and
`tower_jwt_issuer` (they override the module's).

## 4. Register Tower with the Alexa+ MCP Toolkit

Follow the toolkit's current onboarding docs; they change. Record every screen. The values to enter:

| Field (name as the toolkit shows it — TODO(human)) | Value |
|---|---|
| Server URL / endpoint | `https://<tunnel-host>/mcp` (or `tower_mcp_url`) |
| Transport | Streamable HTTP (stateless, JSON responses) |
| Authorization | OAuth 2.0 account linking, authorization-code grant |
| Authorization URI / Access token URI | the Cognito endpoints of step 2 |
| Client ID / secret | `<CLIENT>` / the secret (form only) |
| Scope | `openid` |
| Redirect URLs shown by the toolkit | add them to the Cognito app client (`aws cognito-idp update-user-pool-client … --callback-urls …`) |
| Tools | discovered from `tools/list`: `line_is_ok`, `is_reachable`, `watch_line`. The descriptions are 01 §2 verbatim. Don't edit them in the console; change `descriptions.py` + 01 §2 + the corpus together. |

| # | Step taken | Result / exact error | Minutes | Screenshot |
|---|---|---|---|---|
| 4.1 | TODO(human) | | | `artifacts/screenshots/alexa/04-1.png` |
| 4.2 | | | | |

If a step is region- or partner-gated, write the exact message and the account's country here and in
`friction-log.md`, then go to §8.

## 5. Link the account

In the simulator (or the Alexa app), enable the server/add-on and complete account linking: Cognito hosted UI →
sign in as `asish` → back to Alexa. TODO(human): where the linking prompt appears, how many taps, and any error.

## 6. Verify the identity Tower receives

```bash
make showcase-alexa          # prints the script and tails Tower; each call is captured
```

Say "is my line ok" in the simulator.

- **Expected:** a `↳ line_is_ok {"line": "self"} → OK: Your line is as it was.` line, and
  `artifacts/transcripts/alexa-01-line_is_ok.json` with `"user_id": "<sub>"`.
- **401 / "bearer refused: <ErrorType>" in the log:** Tower logs only the error class, never the token. Typical
  classes:
  - `InvalidIssuerError`: fix `TOWER_JWT_ISSUER`.
  - `InvalidAudienceError` / "client_id not allowed": fix the aud / client-id lists.
  - `PyJWKClientError`: the JWKS URL.
  - `MissingRequiredClaimError`: the token is not the expected shape. Re-run spike A's echo server behind the
    same registration to see what is actually sent.
- **`NOT_BOUND` with a bind link:** the `sub` differs from the one passed to `link`. Re-run `link` with the
  `user_id` in the transcript.

TODO(human): the identity as observed (header, token type, claims present; no values), and whether it matched
`auth.py`. If it differs, update `auth.py`, `components/01` §4 and `tests/test_auth.py` in the same change.

## 7. Run the script and record

Follow `simulator-run.md` (§2.1: 5 × 3 tools, 2 off-topic, 1 ambiguous; then the three moments with the admin
pane). Record the screen with two panes: the simulator, and the terminal running `make showcase-alexa`, where
the reason codes are visible. Save the recording to `artifacts/alexa-simulator.mp4`. Then fill the utterance
fields of `artifacts/transcripts/alexa-*.json` from the run sheet.

### What Alexa+ will be given to say (read before judging "verbatim or paraphrase")

- `summary` comes from Tower's templates (`tower_policy.phrase`, 03 §4), keyed on reason codes. There is no
  model, so the same state always gives the same sentence. Times are spoken-friendly ("10:12 today").
- **Open spec issue: the templates have a fixed person** (`docs/submission/build-log.md`, "Open spec issues").
  `line_is_ok(mom)` → OK returns "Your line is as it was."; a SIM swap on Mom's line returns "Your SIM was
  moved…"; `is_reachable(self)` → UNREACHABLE returns "That person's phone…". The reason codes and facts are
  right; only the person in the sentence is wrong.
  - If Alexa+ reads `summary` verbatim, moment 3 will say "Your line is as it was" about Mom. Write it down
    exactly as heard.
  - If Alexa+ rephrases from `facts` (`"line": "mom"`), it may fix the person itself. Either way it is evidence
    for the decision left open there (keep, or add self/other template variants). Don't change the templates
    during the run.
- `next_step.kind = "bind_line"` carries a URL. Alexa+ can't open it on the Echo, and binding needs one tap on
  the phone over mobile data (rule 7). Note what Alexa+ does with it: reads it, offers to send it, or drops it.
- `facts` never hold a number or a location. If Alexa+ invents either, that is a finding for the friction log.

## 8. Fallback: no access

If registration fails (region or partner gating, or the toolkit is unavailable to the account):

1. Record the exact point of failure in §4's table: the step, the message verbatim, the date, and the account
   country.
2. The showcase's steps 4–6 use the reference client, already recorded in prompt 12: `make demo` →
   `artifacts/transcripts/moment-*.json`. The recording is the terminal run.
3. The close slide's ask stays as it is.
4. `friction-log.md` leads with the failure. The product-feedback section says what was tried and where it
   stopped.

## 9. Teardown

```bash
# stop the tunnel (Ctrl-C), then:
docker compose -f deploy/compose/docker-compose.yml -f deploy/compose/docker-compose.alexa.yml down -v
aws cognito-idp delete-user-pool-domain --region $REGION --user-pool-id $POOL --domain ask-the-tower-<suffix>
aws cognito-idp delete-user-pool --region $REGION --user-pool-id $POOL
```

Those two commands are for the hand-made fallback pool. The Terraform pool, its users and the `alexa-link` client go with `make down ENV=aws`.

Unlink the account in the Alexa app, or disable the add-on in the toolkit console.
