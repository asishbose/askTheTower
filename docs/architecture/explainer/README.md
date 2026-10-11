# Ask the Tower: the explainer, in ten pages

[`ask-the-tower-explainer.drawio`](ask-the-tower-explainer.drawio) is one file with ten pages. It is meant for
presenting the project to a hackathon judge, an engineer joining the team, a carrier partner or an executive, with
nobody from the team in the room. Each page stands alone. Its title is the takeaway, its subtitle says who the page is
for, numbered callouts make it readable without a presenter, and the footer points to the next page and to the
engineering diagram it summarises. The page order follows the deck (`docs/Decks/`).

Diagrams 01–08 under [`../diagrams/`](../diagrams/) are the engineering reference. This set is the narrative
version of them. It uses the same palette: teal for the request path, amber for the proactive path, grey for mock,
binding and deferred work, and a dashed border for anything not executed yet.

**True as of 9 October 2026.** The facts come from `docs/architecture/`, `artifacts/test-report.md` (run
2026-10-07), the transcripts in `artifacts/transcripts/` and `docs/submission/build-log.md`. Nothing has been applied
in AWS, so every AWS, EKS, Alexa+ registration, Cognito and video item is dashed and marked "deferred". When the build
log changes, update `gen_explainer.py` and regenerate (see the end of this page).

| # | Page (title as drawn) | Best for | PNG |
|---|---|---|---|
| 1 | Your Echo can ask your carrier whether your line is safe — and only with the line-holder's consent | everyone | [p01](png/ask-the-tower-explainer-p01.png) |
| 2 | A SIM swap silences your phone first — the Echo on Wi-Fi is the one device still able to ask | judge, exec, partner | [p02](png/ask-the-tower-explainer-p02.png) |
| 3 | Three moments, one pattern: you ask, Tower asks the carrier, Alexa+ answers, a phone tells the rest | judge, exec | [p03](png/ask-the-tower-explainer-p03.png) |
| 4 | A question becomes one database read, two carrier calls and a rule — no model decides anything | judge, engineer | [p04](png/ask-the-tower-explainer-p04.png) |
| 5 | Alexa+ can't speak first, so alerts are texts — same rules, and consent is asked again first | judge, engineer | [p05](png/ask-the-tower-explainer-p05.png) |
| 6 | Nothing is checked until the line-holder taps once on their own phone — and they can take it back | partner, judge | [p06](png/ask-the-tower-explainer-p06.png) |
| 7 | Alexa+ talks, Tower decides in code, AgentCore hosts it — Bedrock is only the test client | engineer, AWS/Alexa+ judge | [p07](png/ask-the-tower-explainer-p07.png) |
| 8 | Same five containers, three places to run them — only the hosting changes | engineer, platform | [p08](png/ask-the-tower-explainer-p08.png) |
| 9 | 1,218 tests pass on a laptop and the mock matches the CAMARA specs; cloud numbers come next | judge, engineer | [p09](png/ask-the-tower-explainer-p09.png) |
| 10 | What is real, what is mock, and what is next — as of 9 October 2026 | everyone | [p10](png/ask-the-tower-explainer-p10.png) |

## Suggested paths

| Audience | Pages | Time |
|---|---|---|
| Executive / CTO | 1 · 2 · 3 · 10 | 4 min |
| Hackathon judge | 1 · 3 · 4 · 5 · 6 · 9 (add 7 for the AWS / Alexa+ track) | 6–7 min |
| Engineer joining | 4 · 5 · 7 · 8 · 9, then 10 | 6 min |
| Carrier partner | 1 · 2 · 6 · 4 · 10 | 5 min |

## Talk track: about 60 seconds per page

**1 — What it is.** "Your carrier already knows whether your SIM was just moved, whether your calls are being
forwarded and whether your mother's phone is on the network. Banks can ask those questions. You can't. Tower is the
piece in the middle. You ask your Echo, Tower checks that you're allowed to ask, puts the question to the carrier
through the standard network APIs, and Alexa+ reads back a plain yes or no. There's one rule: consent first. The person
whose line it is taps once on their own phone, and they can take it back whenever they like."

**2 — Why the Echo.** "This is the moment the product exists for. Someone talks the carrier into moving your number,
and your phone goes quiet. For the first hour that looks exactly like bad signal. Meanwhile the attacker is receiving
your bank codes. The Echo runs on home Wi-Fi, not on your SIM, so it's the one device in the house that can still
ask. The sentence on the slide is the real answer the demo speaks. The numbers at the bottom are reported cases, so
treat them as a floor."

**3 — The three moments.** "Every moment has the same four frames: what you say, what Tower checks, what Alexa+
answers and what happens on the phone. In the first, the SIM was moved. In the second, your calls are being forwarded.
The third is the product itself: Mom granted me a watch on her line, so when her SIM moves, my phone gets a text. If
she revokes the grant, nothing is sent, and her log records why. The chip is the reason code that comes out of the rule
table. The sentence is a fixed template for that code. One known issue: the sentence about Mom still says 'your'."

**4 — How a question travels.** "Seven hops, and no model makes a decision on any of them. Alexa+ picks the tool and
says who is asking and which line, never a phone number. Tower does one consent read. If the line isn't bound or
granted, it refuses before the carrier is ever asked. Otherwise it makes two carrier calls in parallel, applies
deterministic rules and writes the audit row, and only then does the answer leave. The budget is about 300 ms. On a
laptop we measured 237 ms at p95 for 'is my line OK?'. AWS hasn't been measured yet."

**5 — How an alert travels.** "Alexa+ can't start a conversation, so the warning goes out as a text. A carrier
event, or a scheduled check where the carrier has no event feed, triggers Alerts. Alerts fetches the facts and runs
the very same policy code that Tower runs, so the two paths can't disagree. Consent is checked again right before
sending. The text goes to the consented second person, never to the number that was just swapped, because the
attacker now holds that SIM. In the demo the text goes to a log, because real SMS needs the AWS deploy."

**6 — Consent, binding and privacy.** "Binding happens on the phone, over mobile data, with one tap. The carrier
recognises the data session, so nobody types a number, and the Echo can't do it because it's on Wi-Fi. Grants are per
line and per question. Revoking happens on the page, deliberately not by voice. Every check is logged, and the holder
can ask 'who checked my line this week?'. What goes in and what comes out are booleans: no location, no content, no
phone number, and no model that can override the rules."

**7 — The stack.** "From the top down. Alexa+ is the voice. The Tower MCP server is FastMCP, and on AWS it's hosted
on AgentCore Runtime. AgentCore Gateway and Identity turn the CAMARA specs into tools and hold the carrier
credentials. The carrier is a CAMARA-conformant mock. Alerts runs on Lambda, EventBridge and SNS, and state lives in
DynamoDB with KMS. The thick borders are the AgentCore pieces the track asks for. Bedrock sits off to the side: it
powers only the reference client we test with. Everything runs locally today. The AWS hosting is written and
validated but not deployed yet."

**8 — Where it runs.** "The same five images run in three places: Docker Compose on a laptop, AWS with AgentCore and
serverless, and Kubernetes. Only the hosting changes. The proof is that `make demo` prints the same transcripts in
each. Local and kind already match the golden transcripts four for four. AWS and EKS are the deferred half. The ECR
registry has its own Terraform root, so `make down` keeps the images and only `make down-all` deletes them."

**9 — How we know it works.** "1,218 automated tests pass on a laptop: 367 unit, 825 integration and 26 end to
end. Schemathesis generated 1,036 cases against the CAMARA specs, and the mock passed every one. The privacy greps
find no phone-number-shaped string and no health word in any log, answer or text. The latency gate fails the build
above 400 ms. The strip at the bottom is the showcase order, nine steps. What we don't claim: real-carrier timing, or
that Alexa+ always picks the right tool."

**10 — Real, mock, next.** "Here's the honest status as of 9 October. Real: the code, the tests, and end-to-end
runs on a laptop and on Kubernetes. The AgentCore deployment path is written but not applied. Mock: the carrier, the
mobile-data signal and, locally, the SMS. Next: a carrier sandbox, which is the ask on our close slide; Alexa+
registration; the AWS and EKS runs; the video; and the roadmap items from the deck: a device-swap guard, a
recycled-number check and passwordless login."

## Where the facts come from

| Page | Source |
|---|---|
| 1, 3 | the deck (slides 1, 4, 5), `artifacts/transcripts/moment-{1,2,3}.json` (the spoken sentences, verbatim), `components/03` §4 (reason codes) |
| 2 | deck slide 2 (the first hour); numbers from `docs/prior-art.md` "Numbers". **The deck has no numbers slide** (see `docs/submission/deck-consistency.md`) |
| 4 | `e2e-wiring.md` §3, `components/02` §4 (budget), `artifacts/latency-compose.md` (236.7 / 311.7 ms), `artifacts/latency-aws.md` (not measured) |
| 5 | `e2e-wiring.md` §4, `components/06`, `components/03` §3 (6 h repeat, 15 min escalation, 24 h distrust), doc 10 schedules |
| 6 | `e2e-wiring.md` §5 and §8, `components/04` §3, `components/07` §4 |
| 7 | `README.md` "Components", deck slide 8, `components/05`, `components/09` |
| 8 | `README.md` "Where it runs", `components/10` §5–§6, `deployment-agentcore.md`, `artifacts/clean-run.txt`, `artifacts/helm-kind.txt` |
| 9 | `artifacts/test-report.md`, `testing-and-showcase.md` §4–§5 |
| 10 | `docs/submission/build-log.md` (deferred list), README "Status and limits", deck slide 10 |

## Regenerating

The XML is generated. Never hand-edit it.

```bash
uv run python docs/architecture/diagrams/gen/gen_explainer.py            # write + check (overlaps, overflow, edges, labels, page bounds, ≤ 12 boxes per page)
uv run python docs/architecture/diagrams/gen/gen_explainer.py --check    # fail if the .drawio is stale
```

**How the PNGs were produced.** `make diagrams-png` (`scripts/diagrams_png.py`) only globs
`docs/architecture/diagrams/*.drawio` and renders only the first page of each file, so it can't export this
ten-page file from this folder. The PNGs in [`png/`](png/) were rendered by a throwaway script using the same
approach: the draw.io viewer (`viewer-static.min.js`, cached at `~/.cache/ask-the-tower/`) in headless Chromium via
Playwright, with the viewer's `page` index set to 0–9, at device scale 1.5 on a white background. To refresh them,
either export each page with the draw.io desktop CLI (`drawio --export --format png --page-index N`), or extend
`scripts/diagrams_png.py` to loop over pages and to accept this folder. That code change was deliberately not made
here.
