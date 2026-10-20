# Ask the Tower — demo deck: voice-over script

The deck for the live session: three slides, then the demo, then how it's built. A shorter, plainer rework of
`docs/Decks/architecture-prelude/` (which is kept as it was). This file is the source: edit it, then rebuild with

```bash
uv run python scripts/build_prelude_deck.py --deck architecture-demo --no-video   # --skip-tts to reuse the MP3s
```

Outputs: `docs/Decks/architecture-demo/ask-the-tower-architecture-demo.pptx` (narration embedded per slide),
`docs/Decks/architecture-demo/preview/slide-NN.png`, and `artifacts/voiceover/architecture-demo/` (the MP3s and
`build-summary.md`). The format is the one described at the top of `docs/Decks/architecture-prelude/voiceover-script.md`.
The narration is the speaker notes, word for word; this deck carries no `Verify:` lines and no section names.

Sources: as for the prelude deck, plus `docs/prior-art.md` (CAMARA is a Linux Foundation project) and
`docs/architecture/components/10-scheduler-and-infra.md` §2 (the two KMS keys).

## 01 · Ask the Tower

layout: title
subtitle: “Is my line OK?” for Alexa+, answered from the carrier network.

Narration (est. 22 s):

This is Ask the Tower. You ask Alexa Plus, "is my line OK?", and the answer isn't a guess or a web search. It comes
from the carrier's own network, over standard CAMARA APIs, and only for lines whose owners have said yes. First, a
quick look at why anyone would need to ask that question. Then the demo, and after it, how it's built.

## 02 · The problem: you find out too late

layout: flow
number: 971
caption: SIM-swap complaints to the FBI in 2025 · $17.4 million lost
note: The network knows at once. The person can't ask.

On screen:
- device-sim | SIM moved to another device
- phone-outgoing | Calls and texts reach the attacker
- building-bank | Bank codes go to the attacker

Narration (est. 27 s):

Here's the problem. A sim swap doesn't announce itself. At first it feels like bad signal: the phone goes quiet,
while the network has already logged that your number moved to another sim. Call forwarding is quieter still; your
calls just go somewhere else. The FBI logged nine hundred and seventy-one sim-swap complaints in 2025, with seventeen
point four million dollars lost, and those are only the reported cases. Older people are hit hard, and their
families usually hear about it too late. The network knows right away. The person whose line it is can't ask.

## 03 · What Tower does

layout: threeup
caption: CAMARA: one open set of carrier network APIs, under the Linux Foundation
badge: Consent first · bound with one tap · granted per tool · revocable

On screen:
- shield-check | line_is_ok | SIM moved? Calls forwarded?
- antenna-bars-5 | is_reachable | Is this phone on the network?
- bell | watch_line | Text a watcher when it changes

Narration (est. 38 s):

Tower is an MCP server with three tools, and its answers come from CAMARA. CAMARA is an open-source project under
the Linux Foundation, where carriers agree on one common set of network APIs, like sim swap and call forwarding, so
the same request works with any carrier that offers them. Line-is-OK asks two of those questions: was the sim
moved, and are calls being forwarded. Is-reachable asks whether the phone is on the network. Watch-line texts a
second person, say a son watching his mother's line, when either fact changes. All of it is consent first: a line
is bound with one tap on the phone, and each tool is granted per person, and can be revoked.

## 04 · Over to the demo

layout: handover
device: Alexa+ or the web chat
strip: Three moments, answered from the carrier
cta: Let's hear it

On screen:
- “Is my line OK?” | SIM moved: don't call from that phone
- “Is anything forwarding my calls?” | Forwarding set: call your carrier
- “Is Mom's line OK?” | As it was; a text if it changes

Narration (est. 30 s):

Now the demo, in three moments. First, a phone loses signal, and its owner asks whether the line is OK. The sim was
moved, and Alexa says not to call from that phone. Second, they ask whether anything is forwarding their calls.
Third, a son checks on his mother's line, which she chose to share with him. It's as it was, and he'll get a text if
that changes. Every answer you're about to hear came from a carrier API, passed a consent check, and was audited
before it was spoken. After the demo, we'll show how it's built.

## 05 · How it runs on AWS

layout: picture
image: redacted:12-aws-as-built
caption: accent = our code · grey = AWS managed · amber = carrier boundary

On screen:
- laptop | Alexa+ or web chat asks
- tower | Tower, our MCP server, on Runtime
- gateway | Gateway + Identity: CAMARA as MCP tools
- mock | Carrier: CAMARA mock on Fargate

Narration (est. 30 s):

Here's how what you just heard runs on AWS. A question comes in from Alexa Plus, or from our web chat page, signed
in with Cognito. It lands on Tower, our MCP server on AgentCore Runtime. Tower reads consent from DynamoDB, and
asks the carrier through AgentCore Gateway, which turns the CAMARA API specs into MCP tools and gets its carrier
credentials from AgentCore Identity. The carrier is our CAMARA-conformant mock, on Fargate. The proactive path,
the texts, runs on Lambda, EventBridge Scheduler and SNS. Here are the choices behind it.

## 06 · Stateless MCP on AgentCore Runtime

layout: decision
icon: key
visual: flow
decision: A stateless MCP server on AgentCore Runtime
why: Managed MCP hosting; all state in DynamoDB
tradeoff: No server session, so each client sends a session id

On screen:
- device-speaker | Alexa+ or web chat | aws
- key | Sign-in token from Cognito | aws
- cloud | Runtime checks it at the door | aws
- shield-check | Tower checks it again | ours

Narration (est. 26 s):

First, where Tower lives. It's an MCP server on AgentCore Runtime, which hosts it for us: stateless, with plain
JSON responses. Runtime checks the Cognito sign-in token at the door, and Tower checks it again itself, because it
never assumes that someone else did. All state lives in DynamoDB, so any copy of Tower can answer any request.
Being stateless has one cost: each client has to send its own session id, and you'll see why that matters in a
moment.

## 07 · The carrier only via Gateway

layout: decision
icon: route
visual: beforeafter
decision: Tower reaches the carrier only through Gateway
why: Standard CAMARA: an operator swaps in, Tower unchanged
rejected: A direct client, secret in Runtime config
tradeoff: One more network hop

On screen:
- circle-x | Tower holds carrier credentials
- circle-check | Gateway + Identity hold them
- route | 5 CAMARA targets become MCP tools

Narration (est. 22 s):

Second, Tower never talks to the carrier directly. AgentCore Gateway turns the CAMARA API specs into MCP tools, and
AgentCore Identity holds the carrier credentials, so Tower holds no carrier secret at all. Because these are
standard CAMARA APIs, a real operator's sandbox can replace our mock with a configuration change, and Tower doesn't
notice. The price is one more network hop.

## 08 · Consent and privacy by construction

layout: decision
icon: lock
visual: datapath
people: Mom | Son
decision: Privacy is enforced by construction
why: KMS, AWS's key service, holds the keys; no plain-text numbers
rejected: Trusting a typed number; redacting logs later
tradeoff: Binding needs mobile data, so not on Wi-Fi

On screen:
- device-mobile | Phone number
- key | KMS HMAC → line id
- lock | KMS encrypts the number
- eye | Never in a log

Narration (est. 38 s):

Third, privacy by construction. A line is bound with CAMARA Number Verification: you open a link on your phone, over
mobile data, and tap once, and the carrier itself confirms the number. After that, the number never sits anywhere in
plain text. KMS, AWS's Key Management Service, holds two keys for us. One encrypts the number before it's stored.
The other turns it into a keyed hash, an H-MAC, which is the only id our tables and logs use. Grants are per person
and per tool, and every check goes into a hash-chained audit before the answer leaves. The catch: binding needs
mobile data, so it won't work on Wi-Fi.

## 09 · Plain code decides, with fixed words

layout: decision
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

Narration (est. 27 s):

Fourth, no model decides anything that matters. The policy engine is plain Python: facts from the carrier in, one
outcome and a reason code out. Every decision it can make is written out as a table and tested. Each outcome comes
with a fixed sentence. After a sim swap it says: don't call from your phone. I can call your carrier's support line
for you from here. Because the phone may be the thing that was taken. We chose not to let a model reword safety
advice. The cost is wording that can sound stiff.

## 10 · Fast enough for voice

layout: decision
icon: stopwatch
visual: beforeafter
decision: Keep sessions warm; skip the carrier when we can
why: A spoken answer has to feel instant
rejected: A new session for every question
tradeoff: Warm sessions have to be kept alive

On screen:
- circle-x | New session every time: about 7 s
- circle-check | Warm session: under half a second
- database | Watched line: no carrier call

Narration (est. 27 s):

Fifth, speed. A spoken answer has to feel instant. At first, a question without a session id started a cold copy of
Tower, and took about seven seconds. With one session per client, and one warm connection to the Gateway, a typical
answer now comes back in under half a second. And if a line is already being watched, Tower answers from what it
already knows, and doesn't call the carrier at all.

## 11 · What AWS taught us

layout: cards

On screen:
- arrows-right | Gateway arguments are flat | No body wrapper: fields at the top
- shield-lock | No 3LO behind IAM | The binding page exchanges its own code
- alert-triangle | Bedrock quota is 0 | New account: a cross-account role serves it

Narration (est. 24 s):

Finally, what AWS taught us. Gateway passes tool arguments flat, not wrapped in a body. A Gateway behind I-A-M
refuses three-legged OAuth, so our binding page exchanges the sign-in code itself. And a brand-new account's daily
Bedrock quota is zero, so the web chat agent uses a role in another account. Everything is built with Terraform,
and torn down when we're done. Thank you.
