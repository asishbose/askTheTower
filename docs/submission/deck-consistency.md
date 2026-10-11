# Deck consistency — every number on the deck ↔ the artefact it comes from

Deck: `docs/Decks/Ask the Tower — Pitch.pptx` (16 slides). It is the humans' file: the build agent did **not**
edit it. Every row marked **OPEN** below names the slide and gives the exact edit to make. After you edit it,
re-export the PPTX to `docs/Decks/` and run `make deck-check` (exit 0 = no open automated rows) and
`make showcase-artifacts` (refreshes the generated table at the end of this page).

Status on 2026-10-07: **2 automated rows open** (team on the close slide, cost on the tracks slide) and
**3 manual rows open** (the demo slide's quoted answers differ from what the running system says). Both
automated rows are deck-side. Nothing in the code or the artefacts needs to change for them.

## Open: fix in the deck (TODO(human))

| # | Slide | The deck says | The artefact says | Exact fix |
|---|---|---|---|---|
| 1 | 16 · close | "Asish Bose Ottawa" (one name) | `README.md`, `docs/submission/form.md` and slide 1 credit **Asish Bose & Badhrinath Padmanabhan** (commit `19574ad` "Credit both team members") | Replace the name block on slide 16 with **"Asish Bose · Ottawa  &  Badhrinath Padmanabhan · Canada"**, written the same way as the cover slide ("Asish Bose & Badhrinath Padmanabhan Canada"). Checked by `make deck-check` (row `team / close slide`). |
| 2 | 15 · tracks | "COST TO BUILD AND RUN **about $12 for the month**, at on-demand pricing" and "No always-on instance". The note gives the $12 breakdown. | [`artifacts/cost.md`](../../artifacts/cost.md): this Terraform 24/7 costs **≈ $45–50/month** (the internal ALB and WAF dominate it). A showcase window (deploy, demo, `make down` within 48 h) costs **≈ $3–4 in total**. EKS costs **≈ $0.21/h** (≈ $5 per 24 h window). All of these are estimates and were not measured. | Replace the headline with **"about $3–4 per 48-hour showcase window; ≈ $45–50/month if left up"**. Replace "No always-on instance" with **"`make down` returns the account to zero"**. Replace the note's breakdown with: "Estimate, on-demand us-east-1, from artifacts/cost.md: fixed ≈ $40/month (internal ALB $17, Fargate mock $6, WAF $6, CloudWatch $5, public IPv4 $3.7, KMS $2); usage at demo volume < $10. Showcase window ≈ $3–4. EKS ≈ $0.21/h. Re-check with the Pricing Calculator; replace with Cost Explorer after a real deploy." Checked by `make deck-check` (row `cost`); the check passes only when the "about $N" figure falls inside cost.md's range, so a headline without "about $" also clears it. |
| 3 | 5 · demo, moment 1 | Answer: "Your SIM was moved to another device **at 2:14**. If that wasn't you, call your carrier now — **here's the number**." | [`artifacts/transcripts/moment-1.json`](../../artifacts/transcripts/moment-1.json): "Your SIM was moved to another device **at 10:12 today**. If that wasn't you, call your carrier now." The carrier number is in `next_step.carrier_support_number` (`611`) and is not spoken. | Change the quote to the transcript's sentence word for word, so that the video and the slide say the same thing. "Twelve minutes ago" is correct as it stands (`mock clock +12 min`). |
| 4 | 5 · demo, moment 2 | "Yes — all your calls have been forwarding **since 7:40 this morning**. If you didn't set that, call your carrier." | [`moment-2.json`](../../artifacts/transcripts/moment-2.json): "All your calls have been forwarding **since 10:20 today**. If you didn't set that, call your carrier." The answer opens with the moment-1 SIM sentence, because the swap is still inside the 72 h window. | Change the quote to "All your calls have been forwarding since 10:20 today. If you didn't set that, call your carrier." Optionally add, in small type: "(Tower also repeats the SIM warning from moment 1.)" |
| 5 | 5 · demo, moment 3 | "Yes, as it was. You'll get a text if that changes." | [`moment-3.json`](../../artifacts/transcripts/moment-3.json): `line_is_ok(mom)` → reason `OK`, spoken "**Your** line is as it was." This is the fixed-person template (build-log "Open spec issues"). The "text if that changes" half is a second call, `watch_line(mom)`: "Alerts are on for that line. You'll get a text if it's SIM-swapped or forwarded." | Either decide the open spec issue with option (b) (`{Name}'s line is as it was.`), then quote "Mom's line is as it was."; or keep the slide's paraphrase and say in the note that Alexa+ phrases the structured `OK` result. Don't put "Your line is as it was." on the slide as the answer about Mom. |

## Checked and consistent

| Slide | Number or claim on the deck | Source | Result |
|---|---|---|---|
| 1, 16 | Team: both names on the cover | README, form.md | ok (close slide: row 1 above) |
| 3 | Five CAMARA APIs (SIM Swap, Number Verification, Call Forwarding Signal, Device Reachability, + the two subscription APIs) | `specs/camara/` (6 vendored YAMLs); [`conformance-report.html`](../../artifacts/conformance-report.html): 15 operations, 1036 generated cases, 0 failing | ok |
| 4, 5, 9 | Three tools: `line_is_ok`, `is_reachable`, `watch_line` | `services/tower-mcp/src` | ok |
| 5 | "twelve minutes ago" | `moment-1.json` control step "mock clock +12 min" | ok |
| 5 | "Under three minutes" | [`artifacts/video/script.md`](../../artifacts/video/script.md): 2:58 planned | ok (planned; the recording is TODO(human)) |
| 6 | "Eighteen days, as of 5 October" | Deadline 23 Oct 2026 12:00 PDT (`form.md`, rules page fetched 2026-10-07) | ok (5 Oct + 18 d = 23 Oct) |
| 8 | "end-to-end latency against the mock, measured not assumed" (no figure on the deck) | [`latency.md`](../../artifacts/latency.md) (in-process: p95 249.9 / 173.3 ms) and [`latency-compose.md`](../../artifacts/latency-compose.md) (compose: p95 236.7 / 311.7 ms), both under the 400 ms gate. [`latency-aws.md`](../../artifacts/latency-aws.md) is **NOT MEASURED** | ok. Don't add an AWS figure to the deck until `make latency-aws` has run |
| 8 | "Bedrock, off the hot path" | `ref-client` is the only Bedrock caller (`services/ref-client/tests/test_only_llm_call.py`) | ok |
| 12 | "72-hour recency window" | `thresholds.yaml` `SWAP_WINDOW: 72h` | ok |
| 12 | "a daily 8am check" | `deploy/terraform/modules/scheduler/main.tf` `cron(0 8 * * ? *)` (self, care) | ok |
| 13 | "30-minute poll, 8am to 10pm", "Four hours" (roadmap) | scheduler `rate(30 minutes)` care, 08:00–22:00 in-handler; `UNREACHABLE_ALERT.care: 4h`; helm `*/30` | ok (roadmap slide; the 14-day window is not built and the slide says roadmap) |
| 13, 14 | "No reply in 15 min → next contact" | `ESCALATE_NEXT: 15m`; [`alerts-showcase.log`](../../artifacts/transcripts/alerts-showcase.log): neighbour texted at +36 min after the partner at +21 | ok |
| 14 | "Twenty minutes", "5-minute poll as fallback" | `UNREACHABLE_ALERT.transplant: 20m`; scheduler `rate(5 minutes)`; helm `*/5`; alerts-showcase.log: texts at 20 min dark | ok |
| 15 | "MCP Toolkit is US-only" | `docs/prior-art.md` (MCP Toolkit overview, fetched) | ok |
| 15 | "Teardown returns the account to zero" | `make down` / `FORCE=1 make down-eks`. Not run: `billing-zero.png` is TODO(human) | ok as a design claim; unproven until the screenshot exists |
| — | Table count (7 DynamoDB tables), p95 figures, test counts | not on the deck; README ↔ `test-report.md` checked by `make deck-check` (rows `tests`) | ok |
| — | The hook from the "numbers slide" note (Wei Shen, NBC 6 Miami 2022, $68,000) | **There is no numbers slide in this deck** (16 slides; none has that note). The video script uses the hook as prompt 16 gives it. | TODO(human): check the source before recording, and add the slide or drop the attribution. Never say "SIM swap is growing in the US". Slide 15's "SIM-swap fraud is real and rising" is not that sentence, but has no source on the deck either. |

## `make deck-check` output (generated)

<!-- deck-check:begin -->
Last run: 2026-10-07 00:39 UTC — 2 open.

| Check | Result | Detail |
|---|---|---|
| team | ok | cover slide: both named |
| team | OPEN | close slide: missing Badhrinath Padmanabhan |
| team | ok | README: both named |
| team | ok | form.md: both named |
| toolkit | ok | README first screen (12 lines) |
| toolkit | ok | form.md description, first two sentences |
| toolkit | ok | README Built with |
| toolkit | ok | form.md Built with |
| built-with | ok | README == form.md |
| tools | ok | is_reachable on the deck → Tower |
| tools | ok | line_is_ok on the deck → Tower |
| tools | ok | watch_line on the deck → Tower |
| apis | ok | SIM Swap → specs/camara/sim-swap* |
| apis | ok | Number Verification → specs/camara/number-verification* |
| apis | ok | Call Forwarding Signal → specs/camara/call-forwarding-signal* |
| apis | ok | Device Reachability → specs/camara/device-reachability-status* |
| cost | OPEN | slide 15: 'about $12' for the month vs artifacts/cost.md (estimate $45–50/month always-on; cost.md says the claim does not hold; not measured) |
| latency | ok | no latency figure on the deck ('measured not assumed' only) |
| latency | ok | README p95 249.9 ms in artifacts/latency*.md |
| latency | ok | README p95 173.3 ms in artifacts/latency*.md |
| latency | ok | README p95 236.7 ms in artifacts/latency*.md |
| latency | ok | README p95 311.7 ms in artifacts/latency*.md |
| tests | ok | Unit tests: README ('367', '0', '0') vs report ('367', '0', '0') |
| tests | ok | Integration tests: README ('825', '0', '1') vs report ('825', '0', '1') |
| tests | ok | End to end, ENV=local: README ('26', '0', '3') vs report ('26', '0', '3') |
| tests | ok | coverage packages/ 94.8 % in README |
| tests | ok | coverage services/ 90.6 % in README |
| tests | ok | conformance '15 operations, 1,036 generated cases, 0 failing' in README |
| dialogue | ok | README 'My phone just lost signal. Alexa, is my line OK?' on the demo slide |
| dialogue | ok | README 'Is anything forwarding my calls?' on the demo slide |
| dialogue | ok | README 'Is Mom's line OK?' on the demo slide |
<!-- deck-check:end -->
