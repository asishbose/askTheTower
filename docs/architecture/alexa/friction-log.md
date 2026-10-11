# Alexa+ MCP Toolkit — friction log

The running log behind the Alexa+ MCP Toolkit section of `docs/submission/product-feedback.md`. Product feedback
is a required part of the submission. Each entry is dated and reproducible: what was done, what happened (exact
message), how long it cost, and the workaround. Live entries come from spike A (prompt 02) and the registration
run (`registration.md`). If access fails, the failure is the first entry (prompt 15, "If access is unavailable
from Canada").

> **Status:** no live entries yet. The autonomous build (2026-10-06) had no Alexa+ account. The entries below are
> preparation findings from wiring Tower for the toolkit, and are labelled so. None of them is a claim about the
> toolkit's behaviour.

## Live entries (registration and simulator run)

| Date | Step | What happened (exact message) | Cost (min) | Workaround / status |
|---|---|---|---|---|
| TODO(human) | access from a Canadian developer account (spike A) | | | |
| TODO(human) | registration (`registration.md` §4) | | | |
| TODO(human) | account linking (§5) | | | |
| TODO(human) | first tool call / identity shape (§6) | | | |
| TODO(human) | tool selection misses (`simulator-run.md` A) | | | |
| TODO(human) | `summary` verbatim vs paraphrase (`simulator-run.md` C.2) | | | |
| TODO(human) | any notification / proactive path the toolkit offers (noted only; nothing proactive is built on Alexa+ — rule 5) | | | |

## Preparation findings (2026-10-06, autonomous build; not toolkit observations)

1. **The inbound identity is undocumented until spike A runs.** Tower codes an assumption: a bearer JWT,
   `user_id` = `sub` (`auth.py`). The whole consent model hangs on that one field, and it is the first thing
   spike A must capture. Reproduce: `scripts/spikes/a_alexa_echo.py serve`, register it, then `analyse` the
   capture.
2. **The OAuth provider's token shape matters as much as Alexa's.** Account linking means Alexa+ forwards
   *our* identity provider's access token. With Amazon Cognito that token has `client_id` and no `aud`. A
   verifier that only checks audience refuses every call, and AgentCore Runtime's authorizer has separate
   `allowedAudience` / `allowedClients` lists for this reason. Tower now mirrors both (`TOWER_JWT_AUDIENCE`,
   `TOWER_JWT_CLIENT_IDS`). This cost one design pass, and no live minutes yet.
3. **Spoken templates have a fixed person** (open spec issue, `docs/submission/build-log.md`). Whether this
   shows up in the demo depends on whether Alexa+ reads `summary` verbatim. That makes the verbatim/paraphrase
   question a product decision, not just a curiosity.
4. **A link in a voice answer.** `NOT_BOUND` returns `next_step.bind_line` with a URL. The Echo can't open it,
   and binding needs one tap on the phone over mobile data (rule 7). What Alexa+ does with a URL in a tool
   result (reads it out, sends it to the app, drops it) decides how onboarding reads by voice.
5. **Local demo through a tunnel exposes the local bearer.** To be driven from the simulator, compose Tower has
   to be public. Local mode also accepts the static bearer, so the tunnel must be short-lived (`registration.md`
   §3). A toolkit-side allow-list or a "test server" mode would remove this.
