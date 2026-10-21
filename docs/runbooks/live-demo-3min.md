# The 3-minute live demo — run sheet

What the judges see: Asish asks his Echo (here: the reference client in the Control room, the same model and the same Tower) whether his line is OK;
the carrier network answers through Tower; a SIM swap is caught in seconds; Mom's line is watched and the
watcher's phone buzzes; consent is revoked and the alert stops. Everything on AWS. One presenter, one
laptop, one phone.

Timing is for a rehearsed run. Say less than you think you need to: the answers do the talking.

---

## T-30 min — bring the stack up (not on camera)

Prerequisites: AWS stack deployed (Ops 1 → 9 green), phone number verified in the SNS sandbox, the root
`.env` with `REF_AGENT=bedrock`, `BEDROCK_MODEL_ID=us.amazon.nova-micro-v1:0` and
`BEDROCK_ASSUME_ROLE_ARN` set (the Control room calls the model itself), a direct network if you can (no
corporate proxy: the admin path registers your address, and the video must not stall on a 403).

| # | Do | Done when |
|---|---|---|
| 1 | `make showcase-ui ENV=aws` (capital ENV) → http://127.0.0.1:8091/#ops | pill reads `bedrock · ENV=aws`; header shows the account and `config-check ok` |
| 2 | If the stack is down: **4 · Deploy** (10–20 min), then **5 → 9** | 9 prints the web chat URL |
| 3 | **Admin path → Open** (the box after step 9; it plans and deploys in one go) | admin box reads **open** |
| 4 | **10 · Bind Asish for the stories** | `bound: asish, mom` |
| 5 | **11 · make demo on AWS** (dry run of the exact path the demo takes) | `demo ok: 3 stories` |
| 6 | Switch to the **Control room** tab (same UI; it reloaded itself after admin-open) | pill reads `bedrock · ENV=aws · admin on` |
| 7 | Control room: **Reset (make seed)** | mock clock at wall time; Mom's cards show no swap; grants pane shows Mom's `watch` grant to Asish |
| 8 | Warm-up: persona **Asish**, Say **Is my line OK?** → `OK` | the first answer after a cold start can fail once (`CARRIER_ERROR`); ask again until `OK` |
| 9 | Phone: unlocked, on the desk, SMS notifications on, Do Not Disturb off | — |
| 10 | Screen: one tab, the Control room; browser zoom 110–125 %; notifications off on the laptop | — |

If anything here fails, fix it now: **nothing is improvised on camera**. A 403 from the Control room means
your address moved → Admin path → Open again, then Reset. Do not restart the UI between the warm-up and the
take (a restart cold-starts Tower).

---

## The 3 minutes — on camera (one tab: the Control room, every step by hand)

One tab, three panes: **Conversation** (persona dropdown, the Say box, the answers), **Carrier controls**
(Reset, the per-line event buttons, the line cards), **Binding** (grant/revoke). Persona stays **Asish** for
every question. Type the question into the Say box and press **Say** (or Enter); the answer appears under the
box within a few seconds: the tool call, the reason codes, the model's sentence, `next_step`.

Narration is in *italics*; keep it short, the answers do the talking.

| # | Clock | Pane | Do | Expect |
|---|---|---|---|---|
| 0 | before | Carrier controls | **Reset (make seed)**; then warm-up: Say `Is my line OK?` until it answers `OK` | clock at wall time, Mom's cards clean, Asish answered `OK` once |
| 1 | 0:00 | Conversation | Say **Is my line OK?** *"Ask the Tower is an MCP server Alexa+ calls to ask the carrier network one question: is this line OK? I'm Asish."* | `line_is_ok(line=self)` · `OK` · "Your line is as it was." |
| 2 | 0:15 | — | *"That came from the carrier's SIM-swap and call-forwarding APIs, through Bedrock AgentCore Gateway. The model chose the tool; the sentence is Tower's."* | — |
| 3 | 0:25 | Carrier controls | **Asish row → sim_swap** *"Someone just moved Asish's SIM to another device — this is how account takeovers start."* | Asish's card: SIM change = now; Stage beacon coral |
| 4 | 0:35 | Conversation | Say **My phone just lost signal. Is my line OK?** | `SIM_SWAPPED_RECENT` · "Your SIM was moved to another device at <time>. If that wasn't you, don't call from your phone — I can call your carrier's support line for you from here." · `next_step: call_carrier` |
| 5 | 0:55 | — | *"It doesn't tell him to call 611 — that phone is now the attacker's. The surface places the call: that's the `call_carrier` next step."* | — |
| 6 | 1:05 | Carrier controls | **Asish row → cf_set** | Asish's card: forwarding set |
| 7 | 1:10 | Conversation | Say **Is anything forwarding my calls?** | `SIM_SWAPPED_RECENT`, `CALL_FORWARDING_SET` · the swap sentence, then "All your calls have been forwarding since <time>. If you didn't set that, I can call your carrier's support line for you from here." |
| 8 | 1:25 | — | *"Two facts, one answer, the same wording every time — the policy is deterministic, the reason codes are auditable, and the model never paraphrases the safety sentence."* | — |
| 9 | 1:35 | Conversation | Say **Is Mom's line OK?** *"Mom granted Asish a watch on her line — consent first, and it's hers."* | `line_is_ok(line=mom)` · `OK` |
| 10 | 1:45 | Conversation | Say **Watch Mom's line for me.** | `watch_line(line=mom, enable=True)` · "Alerts are on for that line. A text goes out if it's SIM-swapped, forwarded, or off the network too long." |
| 11 | 1:55 | Carrier controls | **Mom row → sim_swap**, then **hold the phone up** | phone buzzes within ~5 s: "SIM moved to another device at <time>. Not you? Call your carrier now." Live feed → **sms** shows the same text |
| 12 | 2:05 | — | Read the text out. *"A real SMS from AWS — the watcher is told, not the compromised phone."* | — |
| 13 | 2:10 | Binding | **Mom revokes Asish's watch** *"Mom changes her mind. Consent is hers."* | status "revoked"; grants pane shows no `watch` |
| 14 | 2:15 | Carrier controls | **Mom row → sim_swap** again | no buzz; Live feed → **audit** shows `SUPPRESSED_REVOKED` |
| 15 | 2:25 | Conversation | Say **Is Mom's line OK?** | `NO_CONSENT` · "Mom hasn't shared that with you." |
| 16 | 2:30 | — | *"Same question, no consent, no answer — and the suppressed alert is in the audit chain."* | — |
| 17 | 2:40 | Conversation | Point down the pane: `OK → SIM_SWAPPED_RECENT → CALL_FORWARDING_SET → NO_CONSENT`, and at the `bedrock` pill. *"The model chose every tool call you saw. Three tools, consent by construction, no phone number ever stored or spoken, and a real operator can replace the mock behind the Gateway with no change to Tower. Thank you."* | — |

Seven questions, four buttons, one revoke. Steps 3, 6, 11 and 14 are the only carrier events; everything
else is typed.

---

## After the take

| Do | Why |
|---|---|
| Binding pane **Mom grants it again**, then Control room **Reset** | puts the grant and the clock back for a second take |
| **Close the admin path** (type `close`) | never leave it open |
| **Destroy the stack, keep ECR** (type `down`) when done for the day | ~$5–10/day while up |

---

## If it goes wrong on camera

| Symptom | Do |
|---|---|
| Answer is "I can't reach your carrier right now" (`CARRIER_ERROR`) | ask again (the carrier call timed out; the next one is live). Twice in a row → Control room **Reset**, continue |
| Say takes 7–8 s or the status reads `cannot connect to Tower … MCPError` | Tower cold-started or its idle session was retired; the UI retries once on a fresh session. Ask once more; keep the T-30 warm-up |
| Status reads `Tower refused the bearer (401)` | the Cognito token expired; the UI signs in again on the next Say. Ask once more |
| No buzz on Moment 3 | keep talking, show the **SMS lane** in the Control room (it lists the same text); check the sandbox number only after the take |
| 403 from a Carrier button or Reset | your address changed → stop the take, Admin path → Open again (≈4 min), restart from Reset |
| Moment 1 answers OK | mock clock isn't at wall time → **Reset** once, fire again |
| Asish `NOT_BOUND` | step 10 skipped → run 10, Reset |

Rehearse once end to end with the timer running before the real take. The whole run is three questions,
two fires, one revoke.
