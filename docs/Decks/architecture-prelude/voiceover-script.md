# Ask the Tower — architecture prelude: voice-over script

The narrated deck that plays before the live demo video. This file is the source: edit it, then rebuild the deck,
the previews, the audio and the video with

```bash
uv run python scripts/build_prelude_deck.py      # --no-video for quick iterations, --skip-tts to reuse the MP3s
```

Outputs: `docs/Decks/architecture-prelude/ask-the-tower-architecture-prelude.pptx` (narration embedded per slide),
`docs/Decks/architecture-prelude/preview/slide-NN.png`, `artifacts/voiceover/slide-NN.mp3`,
`artifacts/voiceover/architecture-prelude.mp4`, and `artifacts/voiceover/build-summary.md` (measured durations,
the voice used, the visuals per slide, every `TODO(verify)`).

Format, per slide: `## NN · Title`, then `key: value` lines (`layout` is one of `title`, `flow`, `threeup`,
`picture`, `decision`, `cards`, `evidence`, `handover`; `section` names the footer; `icon`/`visual` pick the
pictures), then `On screen:` items (`a | b | c`; what each part means depends on the layout — see
`scripts/build_prelude_deck.py`), then `Narration (est. N s):` — the speaker notes and the text spoken, word for
word — and optionally `Verify:` lines, which go into the notes but are not spoken. Icon names are Tabler outline
icons (fetched into `icons/`). Estimated seconds are the last measured durations (Polly standard Matthew at
118 %, 0.5 s padding); `artifacts/voiceover/build-summary.md` has the current ones.

Sources: `README.md`, `docs/architecture/**` (02 §4–5, 03 §phrasing, 05, 07, 11 §Ops, `diagrams/12-aws-as-built`,
`explainer/README.md` page 2), `packages/tower-policy/src/tower_policy/phrasing.py`,
`packages/camara-client/src/camara_client/gateway.py`, `services/ref-client/src/ref_client/mcp_client.py`,
`deploy/terraform/main.tf`, `PREREQUISITES.md` §4, `docs/submission/product-feedback.md`,
`artifacts/test-report.md`, `artifacts/latency-aws.md`, `artifacts/junit-e2e-aws.xml`.

## 01 · Ask the Tower

layout: title
section: Alexa+ MCP track
subtitle: “Is my line OK?” for Alexa+, answered from the carrier network.
kicker: Asish Bose & Badhrinath Padmanabhan · Canada

Narration (est. 25 s):

This is Ask the Tower, built by Asish Bose and Badhrinath Padmanabhan. Before the demo video, we want to spend a
few minutes on how it's built, because most of the interesting work is in the parts you can't hear. The idea is
simple. You ask Alexa Plus, "is my line OK?", and the answer isn't a guess or a web search. It comes from the
carrier's own network, over standard CAMARA APIs, and only for lines whose owners have said yes. Let's start with
why anyone would need to ask that question at all.

## 02 · The problem: you find out too late

layout: flow
section: The problem
number: 971
caption: SIM-swap complaints to the FBI in 2025 · $17.4 million lost
note: The network knows at once. The person can't ask.

On screen:
- device-sim | SIM moved to another device
- phone-outgoing | Calls and texts reach the attacker
- building-bank | Bank codes go to the attacker

Narration (est. 27 s):

Here's the problem. A SIM swap doesn't announce itself. At first it feels like bad signal: the phone goes quiet,
while the network has already logged that your number moved to another SIM. Call forwarding is quieter still; your
calls just go somewhere else. The FBI logged nine hundred and seventy-one SIM-swap complaints in 2025, with seventeen
point four million dollars lost, and those are only the reported cases. Older people are hit hard, and their
families usually hear about it too late. The network knows right away. The person whose line it is can't ask.

## 03 · What Tower does

layout: threeup
section: What Tower does
badge: Consent first · bound with one tap · granted per tool · revocable

On screen:
- shield-check | line_is_ok | SIM moved? Calls forwarded?
- antenna-bars-5 | is_reachable | Is this phone on the network?
- bell | watch_line | Text a watcher when it changes

Narration (est. 30 s):

Tower is an MCP server with exactly three tools. Line-is-OK asks two CAMARA questions: was the SIM moved, and are
calls being forwarded. Is-reachable asks whether the phone is on the network. And watch-line asks our Alerts
service to text a second person, say a son watching his mother's line, when either fact changes. Alexa Plus can't
speak first through MCP, so the proactive part is an SMS, not a voice. All of it is consent first: a line is bound
once, with one tap on the phone, and each tool is granted per person, and can be revoked. Here's how it runs on AWS.

## 04 · Architecture, as deployed on AWS

layout: picture
section: Architecture
image: redacted:12-aws-as-built
caption: accent = our code · grey = AWS managed · amber = carrier boundary

On screen:
- laptop | Alexa+ or web chat asks
- tower | Tower, our MCP server, on Runtime
- gateway | Gateway + Identity: CAMARA as MCP tools
- mock | Carrier: CAMARA mock on Fargate

Narration (est. 33 s):

This is the deployment as it actually runs. A question comes in from Alexa Plus, or from our
web chat page, signed in with Cognito. It lands on Tower, our MCP server on AgentCore Runtime. Tower reads consent
from DynamoDB, with numbers protected by KMS, and asks the carrier through AgentCore Gateway, which turns the CAMARA
OpenAPI specs into MCP tools and gets its OAuth tokens from AgentCore Identity. The carrier is our CAMARA-conformant
mock, on Fargate behind an internal load balancer. The amber line is the proactive path: Lambdas, EventBridge Scheduler, and SNS. Now, the six decisions behind it.

## 05 · Decision 1: stateless MCP on AgentCore Runtime

layout: decision
section: Decisions
icon: key
visual: flow
decision: A stateless MCP server on AgentCore Runtime
why: Managed MCP hosting; all state in DynamoDB
rejected: Our own containers on EKS, ~$150 a month
tradeoff: No server session id, so clients must send one

On screen:
- device-speaker | Alexa+ or web chat | aws
- key | Bearer JWT from Cognito | aws
- cloud | Runtime authorizer checks it | aws
- shield-check | Tower verifies it again | ours

Narration (est. 33 s):

Decision one: where Tower lives. We host it as an MCP server on AgentCore Runtime: Streamable HTTP, stateless, with
plain JSON responses. Runtime's J-W-T authorizer checks the Cognito token at the edge, and we pass the Authorization
header through so Tower verifies it again itself. Tower never assumes that someone else checked. All state lives in
DynamoDB, so any instance can answer. We rejected running our own containers on EKS. The Helm charts exist and pass
on kind, but left up, EKS costs about a hundred and fifty dollars a month. Being stateless has a cost, and you'll
see it on the latency slide.

## 06 · Decision 2: the carrier only via Gateway

layout: decision
section: Decisions
icon: route
visual: beforeafter
decision: Tower reaches the carrier only through Gateway
why: Standard CAMARA: an operator swaps in, Tower unchanged
rejected: A direct client, secret in Runtime config
tradeoff: One more hop, and a session to keep warm

On screen:
- circle-x | Tower holds carrier credentials
- circle-check | Gateway + Identity hold them
- route | 5 CAMARA targets become MCP tools

Narration (est. 29 s):

Decision two: Tower never talks to the carrier directly. AgentCore Gateway turns our vendored CAMARA OpenAPI specs
into MCP tools, and AgentCore Identity holds the outbound OAuth credentials, so Tower holds no carrier secret at
all. And because these are standard CAMARA APIs, an operator's sandbox replaces our mock as a configuration change
that Tower can't even see. The alternative, a direct client with the secret in Runtime config, is still there as
our cut line, but we didn't need it. The price is one more network hop, and a Gateway session that we have to keep
warm.

## 07 · Decision 3: consent and privacy by construction

layout: decision
section: Decisions
icon: lock
visual: datapath
decision: Privacy is enforced by construction
why: Number Verification binds the line: one tap, mobile data
rejected: Trusting a typed number; redacting logs later
tradeoff: Binding fails on Wi-Fi

On screen:
- device-mobile | Phone number
- key | HMAC → line id
- lock | KMS → ciphertext
- eye | Never in a log

Narration (est. 29 s):

Decision three: privacy by construction. A line is bound with CAMARA Number Verification. You open a link on your
phone, over mobile data, and tap once, and the carrier itself confirms the number. From then on, the tables key the
line by an H-MAC, the number is stored only as KMS-encrypted ciphertext, and a test greps every log and transcript
for anything shaped like a phone number. Grants are per line and per tool, and can be revoked. Every check, allowed
or refused, goes into a hash-chained audit before the answer leaves. The trade-off is real friction: binding won't
work on Wi-Fi.

## 08 · Decision 4: deterministic policy, fixed words

layout: decision
section: Decisions
icon: list-check
visual: table
decision: Plain code decides, then picks a fixed sentence
why: Safety advice must never drift
rejected: A model decides, or paraphrases the advice
tradeoff: Fixed wording can sound stiff

On screen:
- SIM_SWAPPED_RECENT | Your SIM was moved to another device at ‹time›. If that wasn't you, don't call from your phone — I can call your carrier's support line for you from here.
- CALL_FORWARDING_SET | All your calls have been forwarding since ‹time›. If you didn't set that, I can call your carrier's support line for you from here.
- NO_CONSENT | ‹Name› hasn't shared that with you.

Narration (est. 29 s):

Decision four: no model decides anything that matters. The policy engine is plain Python: facts from the carrier
in, one outcome and a reason code out, like SIM swapped recent, or call forwarding set. Every decision it can make
is printed as a table and tested. Each outcome carries a summary sentence written to be read out word for word.
After a SIM swap it says: don't call from your phone. I can call your carrier's support line for you from here.
Because the phone may be the thing that was taken. We rejected letting a model paraphrase safety advice. The cost is
wording that sometimes sounds stiff.

Verify:
- TODO(verify) Whether Alexa+ reads `summary` verbatim or paraphrases is still open (01 §open questions); the web chat agent is instructed to read it verbatim (`services/ref-client/prompts/ref-client/system.md`).

## 09 · Decision 5: latency by design

layout: decision
section: Decisions
icon: stopwatch
visual: budget
decision: Budget the call; skip the carrier when we can
why: A voice answer has to feel instant
rejected: A fresh Gateway session per call, ~460 ms
tradeoff: One warm session per process to keep alive

On screen:
- fixed | 360 | Fixed cost: network, authorizer, store, audit
- carrier | 168 | Carrier calls, 300 ms timeout each
- total | 528 | line_is_ok p95 on AWS
- target | 400 | Target p95
- stored | 0 | Watched line, fresh state: no carrier call

Narration (est. 31 s):

Decision five: latency. A voice answer has to feel instant, so each carrier call gets a hard three hundred
millisecond timeout. If Alerts is already watching a line, Tower answers from the
stored state and skips the carrier entirely. On AWS we found two hidden costs. Opening a
fresh Gateway session per call took about four hundred and sixty milliseconds, so Tower keeps one session open, and
a warm call takes about one hundred and fifty. And because Tower is stateless, any request without a session id
landed on a cold micro-VM: about seven seconds. One session id per client brought that to about point four seconds.

Verify:
- The budget bar is derived from `artifacts/latency-aws.md`: fixed ≈ 360 ms = `watch_line` p95 (no carrier call); carrier ≈ 168 ms = `line_is_ok` p95 (528 ms) minus that. TODO(verify) the split inside the fixed cost (network, authorizer, store, audit) is not measured.
- TODO(verify) "0.4 s" is the measured p50 of `line_is_ok` in `artifacts/latency-aws.md` (429.6 ms), taken with the session id in place; the repo has no before/after pair measured in one run. The "≈ 7 s" is from Tower's log on 2026-10-14, quoted in `services/ref-client/src/ref_client/mcp_client.py`.

## 10 · Decision 6: operating it, and what AWS taught us

layout: cards
section: What AWS taught us
strip: Terraform via a 14-step Ops pipeline · admin path: one /32, then closed

On screen:
- arrows-right | Gateway arguments are flat | No body wrapper: fields at the top
- shield-lock | No 3LO behind IAM | The binding page exchanges its own code
- stopwatch | 7 s → 0.4 s | Client session id: no cold start per call
- alert-triangle | Bedrock quota is 0 | New account: a cross-account role serves it

Narration (est. 32 s):

Decision six is how two people operate this, and it's also where AWS taught us the most. Everything is Terraform,
run from an Ops tab that walks the AWS pipeline as fourteen steps, one click each. For the live demo, an admin path to the mock opens for exactly one address, behind a bearer token, and closes
right after. Lessons from the first apply: Gateway passes tool arguments flat, not wrapped in a body. A Gateway with
an IAM authorizer refuses three-legged OAuth targets, so the binding page exchanges the code itself. And a new
account's daily Bedrock token quota is zero, so the web chat agent uses a cross-account role.

Verify:
- TODO(verify) That the demo admin path was closed after the recorded demo: `deploy/terraform/envs/aws.tfvars` has `demo_admin_cidrs = []` after `make admin-close` (deployment-agentcore.md step 12b is still marked TODO(human)).

## 11 · Evidence

layout: evidence
section: Evidence
badge1: ok | 3 Moments pass end to end on AWS
badge2: ok | Chaos on AWS: 9/9, never wrong
badge3: warn | line_is_ok p95 528 ms; target 400

On screen:
- Unit | 999
- Integration | 1236
- E2E local | 30
- E2E on AWS | 13

Narration (est. 34 s):

So does it work? Nine hundred and ninety-nine unit tests and one thousand two hundred and thirty-six integration
tests pass, including CAMARA conformance with zero failing operations, and privacy tests with zero hits. On AWS, the
demo stories match the golden transcripts end to end, and chaos on every tool, timeouts, server errors and rate
limits, never gives a wrong answer. Latency, honestly: our target is four hundred milliseconds at the ninety-fifth
percentile. Is-reachable makes it, at three eighty-five. Line-is-OK misses, at five twenty-eight. Watch-line, which
never calls the carrier, still takes about three hundred. That's a fixed cost on every call.

Verify:
- TODO(verify) Where the ~300 ms fixed cost goes (laptop → Runtime network, the authorizer, consent read, audit write): `artifacts/latency-aws.md` measures client-side wall time only; the per-step split needs a `LOG_LEVEL=debug` run, which is not recorded in the repo.
- Counts: `artifacts/test-report.md` (2026-10-14): unit 999/999; integration 1236 passed, 1 skipped; e2e local 30 passed, 3 skipped; e2e aws 13 passed, 20 skipped, 0 failed.

## 12 · Over to the demo

layout: handover
section: Demo
strip: Here is what Asish hears on his Echo…
cta: Let's hear it

On screen:
- “Is my line OK?” | SIM moved: don't call from that phone
- “Is anything forwarding my calls?” | Forwarding set: call your carrier
- “Is Mom's line OK?” | As it was; a text if it changes

Narration (est. 29 s):

That's the architecture. Now the demo. Here is what Asish hears on his Echo, in three moments. First, his phone
loses signal and he asks Alexa whether his line is OK. His SIM was moved, and Alexa tells him not to call from that
phone. Second, he asks whether anything is forwarding his calls. Third, he checks on Mom's line, which she chose to
share with him. It's as it was, and he'll get a text if that changes. Every answer you're about to hear came from a
carrier API, passed a consent check, and was audited before it was spoken. Let's watch.

Verify:
- TODO(verify) The demo footage is on an Echo: `docs/architecture/alexa/registration.md` still says "Status: not run" (the toolkit is US-only; the fallback is the reference client / web chat). Change "on his Echo" if the video shows the simulator or the web chat page.
