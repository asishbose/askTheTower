# Ask the Tower: demo video script (≤ 3:00)

This script follows testing-and-showcase.md §4, steps 1 to 9. It is a pitch, not a tutorial. The only command
typed on camera is `make up && make demo` (step 9).

**What actually runs today, so it is all this script shows:** the local compose stack (`make up`): the
mock carrier, Tower, the binding page, Alerts with its SMS **log sink**, and DynamoDB Local. In front of it is the
reference client with its **scripted agent**. With no Bedrock credentials, the tool call comes from the demo
script and Tower's `summary` is read word for word. Not shown, because none of them ran: the Alexa+ web simulator
(prompt 15, US-only, `artifacts/alexa-simulator.mp4` deferred), a real SMS on a real phone (needs SNS on AWS), and
anything deployed on AWS or EKS. Each place where the recording would change once one of these exists is marked
**[swap]**.

## Panes and rules

- **Pane A (left, ~60 %)**: the "assistant". Today this is the reference-client terminal (`make demo` with
  `DEMO_ARGS="--story moment-1 --story moment-2 --story moment-3 --pause"`), with a large font, the `you>` / `ref>`
  lines only, and the tool-call line (`→ line_is_ok(line=self) ['SIM_SWAPPED_RECENT']`) kept visible.
  **[swap]** Use the Alexa+ web simulator if access arrives.
- **Pane B (right, ~40 %)**: evidence. This is the Tower log tail (`make logs`, filtered to `tower-mcp` and
  `alerts`) and the mock's Swagger UI / clock (`http://localhost:8443/docs`). Show the binding page in a phone frame
  when a step calls for it.
- **Phone on camera**: step 3 (binding page). For step 6, see that step: locally the "buzz" is Alerts' SMS log
  line, and it is shown as that.
- **Never visible**: phone numbers, tokens, keys, account ids, LAN IPs. Don't show the mock showcase's curl
  listing (`make showcase-mock`), because it prints the fictional `+1613555…` demo numbers; use Swagger UI with the
  request bodies collapsed. Tower and Alerts logs carry `line_id`/`ln_…` and `chain:user-…` only (privacy-tested),
  so they can be shown. Crop the binding page's URL bar (it shows the host IP and a bind token).
- **Captions**: every spoken user utterance and every spoken answer is burned in. The video must make sense with
  the sound off.
- **Steps 4 to 6 are not cut, sped up or re-timed.** The visible Tower latency is real: compose p95 236.7 ms for
  `line_is_ok` ([`latency-compose.md`](../latency-compose.md)). The timings below give these steps 90 s for
  pauses and voice-over, not for waiting.

## Dry-run timings (machine time, this repo, 2026-10-07)

`make up` took 43 s with images cached. Then `scripts/showcase_order.py --fast --only N`, no pauses, measured
per step: 1 mock 9.6 s · 2 policy table 32.1 s (the pytest regeneration; on camera, show the finished
`artifacts/policy-table.md`) · 3 binding 9.4 s (prints the QR) · 4–6 moments 42.1 s (includes `make seed`) · 7
transplant + Alerts 60.6 s · 8 audit 27.3 s · 9 `make demo` 23.9 s. A clean machine took 106 s for
`time make up && make demo` ([`clean-run.txt`](../clean-run.txt)). The on-camera timings below are the
narrated cut. **TODO(human):** rehearse with a stopwatch and replace the "planned" column with what you measure.

## The organisers' five answers, where they land

| Question | Time | Where |
|---|---|---|
| What problem does it solve? | 0:00 | the hook |
| Who is it for? | 0:15 | "same attack, different ending" |
| How does it use the track's tool? | 0:45 | "Alexa+ MCP Toolkit" spoken + caption at the first tool call |
| The solution working | 0:45–2:15 | steps 4–6 |
| What the working app actually does | 2:15 | steps 7–9 |

## Script

| # | Planned | Pane on screen | Spoken (voice-over unless marked) | Caption burned in |
|---|---|---|---|---|
| hook | 0:00–0:15 | Black, then a phone face-down on a table | "Wei Shen's phone went quiet one morning. By the end of that day, three wire transfers had drained sixty-eight thousand dollars from her bank accounts. Nobody else knew until it was over." | "NBC 6 Miami, 2022: three wire transfers, $68,625, the day her phone went silent." |
| who | 0:15–0:22 | Echo on a kitchen counter | "Same attack, different ending. This is for the person whose line it is, and for the one person they trust." | "Ask the Tower: your carrier's network facts, for you" |
| 1 · mock | 0:22–0:30 | B: Swagger UI of the mock carrier, the clock at 14:00 | "This is a carrier, built to the public CAMARA spec. It's a mock, and here's its clock." | "Spec-conformant mock carrier: 15 CAMARA operations, 1,036 generated cases, 0 failing" |
| 2 · policy | 0:30–0:36 | B: `artifacts/policy-table.md` scrolling | "This is every decision the system can make. It's code, not a model." | "Policy = deterministic code, tested as a table" |
| 3 · binding | 0:36–0:45 | Phone on camera: binding page → Verify → "Line connected •••• 0101" | "One tap on the phone binds the line. Against the mock, a header stands in for mobile data." | "Local mock: the client-id header stands in for mobile data. Against a real carrier: one tap over mobile data, Wi-Fi off." |
| 4 · moment 1 | 0:45–1:10 | A: client; B: Tower log tail; mock clock visible | (A) **you>** "Is my line OK?" → **ref>** "Your line is as it was." · mock clock +12 min, the SIM moves · (A) **you>** "My phone just lost signal. Is my line OK?" → `line_is_ok(line=self) ['SIM_SWAPPED_RECENT']` → **ref>** "Your SIM was moved to another device at 10:12 today. If that wasn't you, call your carrier now." · VO at the first tool call: "Tower is an MCP server, the kind the Alexa+ MCP Toolkit connects to. Alexa+ asks; Tower asks the carrier." | At the tool call: **"Alexa+ MCP Toolkit → Tower MCP server (Streamable HTTP): line_is_ok"**. Second line, small: "shown: the reference client on the same MCP endpoint". **[swap]** with the simulator, drop the second line. Every utterance and answer is captioned. |
| 5 · moment 2 | 1:10–1:25 | A + B; mock admin fires `cf_set` (clock +8 min) | (A) **you>** "Is anything forwarding my calls?" → `['SIM_SWAPPED_RECENT', 'CALL_FORWARDING_SET']` → **ref>** "…All your calls have been forwarding since 10:20 today. If you didn't set that, call your carrier." | Utterance and answer. "Call Forwarding Signal API: unconditional forwarding set." |
| 6 · moment 3 | 1:25–2:15 | A + B; B switches to the Alerts log at the swap | (A) **you>** "Is Mom's line OK?" → `line_is_ok(line=mom) ['OK']` → **ref>** "Your line is as it was." (see note 1) · **you>** "Watch Mom's line for me." → **ref>** "Alerts are on for that line. You'll get a text if it's SIM-swapped or forwarded." · mock: SIM swap on Mom's line → B: `alerts.sms SMS to=chain:user-asish body='SIM moved to another device at 10:20 today. Not you? Call your carrier now.'` · VO: "Alexa can't speak first, so the watcher gets a text." · Mom revokes on the binding page · swap again → B: `outcome=suppressed codes=SUPPRESSED_REVOKED`, no SMS line · (A) **you>** "Is Mom's line OK?" → **ref>** "Mom hasn't shared that with you." · VO: "Revoked means nothing goes out, and the audit says why." | "Tower result for Mom's line: OK (no swap, no forwarding)". Note 1: "The spoken template is first-person ('Your line…'): a known open issue; the reason code is correct." At the SMS: "The watcher's text (local SMS log; a real SMS on AWS)". **[swap]** With SNS on AWS, cut to the watcher's phone buzzing on camera, SMS header masked. At the revoke: "Consent revoked → SUPPRESSED_REVOKED, no text". |
| 7 · transplant | 2:15–2:32 | B: `make showcase-alerts` output, mock clock in the corner | "Your own line, watched for a transplant call. Twenty minutes dark, and your partner gets a text. Fifteen more with no reply, and the second contact gets one." | At +20 min: the SMS log line `to=chain:user-partner body="asish's phone has been off the network since 10:01 today - that's the network, nothing more."`. At +35 min: `to=chain:user-neighbour …`. "Reachable means on the network, not that the phone will ring." |
| 8 · audit | 2:32–2:42 | Phone frame: Mom's "Activity on line" page, "Log verified". Or B: `make showcase-audit`, `verify → {"ok":true,"rows":11}` then the tamper row `"ok":false` | "Mom can see every check on her line, and the log proves nobody edited it." | "Hash-chained audit: verified. One edited row → named." |
| 9 · make demo | 2:42–2:52 | Full-screen terminal: `make up && make demo`, time-lapsed | "Everything you saw runs from one command. You can run this." | "`make up && make demo`, 106 s on a clean machine (sped up ×10)" |
| close | 2:52–2:58 | Architecture slide (deck slide 7) | "Three tools, standard carrier APIs, consent first." | "Built to deploy on Amazon Bedrock AgentCore Runtime (Terraform in repo; not yet deployed)". **TODO(human):** after `make deploy ENV=aws`, change it to "Running on Amazon Bedrock AgentCore Runtime". |

**Note 1: moment 3's sentence.** Tower's spoken templates have a fixed person (build-log "Open spec issues"):
`line_is_ok(mom)` → OK reads "Your line is as it was." The reason code and the facts (`line: mom`) are right; only
the sentence is in the wrong person. The script does not hide this. The caption says so, and the voice-over
doesn't repeat the sentence. If the humans pick option (b) (`{Name}'s line is as it was.`) before recording,
re-run `make demo`, drop note 1, and use "Mom's line is as it was." In the transplant story, the
`is_reachable(self)` answer ("That person's phone…") has the same problem, so step 7 shows the SMS lines, which
read correctly, and not that answer.

## Lines never to say, and claims not to make (§5 and the prompt's guardrails)

- Never say "SIM swap is growing in the US." The hook is one sourced story, not a trend.
- Say "spec-conformant mock", never "a real carrier". Say "measured against the mock", with the number on screen.
  There is no AWS latency yet ([`latency-aws.md`](../latency-aws.md) is NOT MEASURED).
- Don't say "Alexa+ picks the right tool". The corpus has not run against a model (`artifacts/corpus.md` is
  deferred).
- Don't say AgentCore is running it until `make deploy ENV=aws` has run.
- Don't say "the phone will ring". Reachable is a network fact.

## Hook source

NBC 6 Miami (NBC 6 Responds), "Woman Loses Life Savings in SIM Swap Scam":
<https://www.nbcmiami.com/responds/woman-loses-life-savings-in-sim-swap-scam/2845044/>. Search summary,
2026-10-07: her phone went silent, three wire transfers the same day totalled $68,625, and her bank denied the
claim. **TODO(human):** read the article itself to confirm the year (2022) and the wording before recording. The
deck has no "numbers slide" carrying this note (see `docs/submission/deck-consistency.md`).

## Recording checklist (TODO(human))

1. `make up`; check that the terminal profile has no history, hostnames or tokens on screen; set the font to 20 pt+.
2. Record pane A and pane B with `SHOWCASE_PAUSE=1 make showcase` (it pauses before each step; the reference
   client pauses before each moment). One take per step, then stitch.
3. Phone: open the binding page from the QR code (step 3). Crop the URL bar.
4. Burn in the captions above. Export at 1080p as `artifacts/video/ask-the-tower-demo.mp4`; it must be ≤ 3:00.
   Watch it once muted.
5. Scrub every frame for digits that look like a phone number, and for tokens and keys. Upload to YouTube or Vimeo
   as public and in English, with no copyrighted music. Put the link in `docs/submission/form.md`.
