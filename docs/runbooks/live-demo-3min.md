# The 3-minute live demo — run sheet

What the judges see: Asish asks his Echo (here: the web chat page, the same agent) whether his line is OK;
the carrier network answers through Tower; a SIM swap is caught in seconds; Mom's line is watched and the
watcher's phone buzzes; consent is revoked and the alert stops. Everything on AWS. One presenter, one
laptop, one phone.

Timing is for a rehearsed run. Say less than you think you need to: the answers do the talking.

---

## T-30 min — bring the stack up (not on camera)

Prerequisites: AWS stack deployed (Ops 1 → 9 green), phone number verified in the SNS sandbox, a direct
network if you can (no corporate proxy: the admin path registers your address, and the video must not stall
on a 403).

| # | Do | Done when |
|---|---|---|
| 1 | `make ops-ui` → http://127.0.0.1:8091/#ops | header shows the account and `config-check ok` |
| 2 | If the stack is down: **4 · Deploy** (10–20 min), then **5 → 9** | 9 prints the web chat URL |
| 3 | **Open the admin path** → at the plan gate type `deploy` → Run **4 · Deploy** | admin box reads **open** |
| 4 | **10 · Bind Asish for the stories** | `bound: asish, mom` |
| 5 | **11 · make demo on AWS** (dry run of the exact path the demo takes) | `demo ok: 3 stories` |
| 6 | Open the web chat URL in a browser tab, sign in as `asish` | "Is my line OK?" answers in about a second |
| 7 | Control room tab (`make showcase-ui ENV=aws` if you want it on a second port) → **Reset** | pill `ENV=aws · admin on`, feed empty |
| 8 | Phone: unlocked, on the desk, SMS notifications on, Do Not Disturb off | — |
| 9 | Screen: web chat tab left, Control room tab right (or one tab, Alt-Tab rehearsed); browser zoom 125 %; notifications off on the laptop | — |

If anything here fails, fix it now: **nothing is improvised on camera**. A 403 on 5 means your address moved
→ Open the admin path again.

---

## The 3 minutes — on camera

Narration is in *italics*. Clicks are in **bold**. Keep the web chat visible for 1 and 2; switch to the
Control room for the mock events and for 3.

### 0:00 – 0:25 · Setup line (web chat tab)

*"Ask the Tower is an MCP server Alexa+ calls to ask the carrier network one question: is this line OK?
I'm signed in as Asish. Let's ask."*

- Type **Is my line OK?** → *"Your line is as it was."* (≈1 s)
- *"That came from the carrier's SIM-swap and call-forwarding APIs, through Bedrock AgentCore Gateway,
  in under half a second of Tower time."*

### 0:25 – 1:05 · Moment 1 — the SIM swap (Control room, then web chat)

- Control room: **Moment 1** (or: fire **SIM swap on Asish's line**). The clock chip advances; the Stage
  beacon turns coral.
- *"Someone just moved Asish's SIM to another device — this is how account takeovers start."*
- Web chat: **My phone just lost signal. Is my line OK?**
- Answer: *"Your SIM was moved to another device at <time>. If that wasn't you, don't call from your phone —
  I can call your carrier's support line for you from here."*
- *"Note what it does not say: it doesn't tell him to call 611 — that phone is now the attacker's. The
  surface places the call."* Point at the `SIM_SWAPPED_RECENT` chip.

### 1:05 – 1:35 · Moment 2 — call forwarding

- Control room: **Moment 2** (fire **call forwarding set**).
- Web chat: **Is anything forwarding my calls?**
- Answer: swap + *"All your calls have been forwarding since <time>…"*
- *"Two facts, one answer, every time the same wording — the policy is deterministic, the reason codes are
  auditable, and the model never paraphrases the safety sentence."*

### 1:35 – 2:35 · Moment 3 — Mom's line, a watch, a revoke

- Web chat: **Is Mom's line OK?** → *"Your line is as it was."* (Mom granted Asish `watch`.)
- Web chat: **Watch Mom's line for me.** → *"Alerts are on for that line…"*
- Control room: **fire SIM swap on Mom's line**.
- **Hold the phone up** — it buzzes within ~5 s: *"SIM moved to another device at <time>. Not you? Call your
  carrier now."* Read it out. *"That's a real SMS from AWS — the watcher is told, not the compromised phone."*
- Control room: **Mom revokes** (grants pane → revoke `watch`). *"Mom changes her mind. Consent is hers."*
- Control room: **fire SIM swap on Mom's line** again. No buzz. Feed shows `SUPPRESSED_REVOKED`.
- Web chat: **Is Mom's line OK?** → *"Mom hasn't shared that with you."*
- *"Same question, no consent, no answer — and the suppressed alert is in the audit chain."*

### 2:35 – 3:00 · Close

- Control room: point at the chips: `OK → SIM_SWAPPED_RECENT → CALL_FORWARDING_SET → NO_CONSENT`.
- *"Three tools, consent by construction, no phone number ever stored or spoken, and a real operator can
  replace the mock behind the Gateway with no change to Tower. Thank you."*

---

## After the take

| Do | Why |
|---|---|
| Control room **Reset** | puts the grant back for a second take |
| **Close the admin path** (type `close`) | never leave it open |
| **Destroy the stack, keep ECR** (type `down`) when done for the day | ~$5–10/day while up |

---

## If it goes wrong on camera

| Symptom | Do |
|---|---|
| Web chat answers "I can't reach your carrier right now" | wait 5 s, ask again (a carrier call timed out; the next one is live). Twice in a row → Control room **Reset**, continue |
| Web chat takes 7–8 s to answer | the first call after a long idle is a cold start; ask once more, it's warm now. Keep the T-30 warm-up |
| No buzz on Moment 3 | keep talking, show the **SMS lane** in the Control room (it lists the same text); check the sandbox number only after the take |
| 403 / "can't reach carrier" from the Control room | your address changed → stop the take, Open the admin path again (≈4 min), restart from Reset |
| Moment 1 answers OK | mock clock isn't at wall time → **Reset** once, fire again |
| Asish `NOT_BOUND` | step 10 skipped → run 10, Reset |

Rehearse once end to end with the timer running before the real take. The whole run is three questions,
two fires, one revoke.
