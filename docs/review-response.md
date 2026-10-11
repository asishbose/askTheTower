# Response to the 2026-09-28 / 10-02 review of "Ask the Tower"

Date: 2026-10-05 · Deadline: 23 Oct 2026, 12:00 PT (18 days)
Reviewed document: `ask-the-tower-review.md` (sections 1–7)

The review is largely correct, and the changes it recommends make the pitch stronger. This note records what was accepted (and applied to the deck on 2026-10-05) and sets out, in detail, the points where we disagree or the reviewer's framing needs correction. The pushbacks are the substance of this document; the accepted list is here so the two are read together.

---

## 1. Accepted and applied

| # | Review point | What changed in the deck |
|---|---|---|
| A1 | Alexa cannot read SMS; the OTP demo rests on a false premise | Demo moment 1 replaced with "My phone just lost signal — Alexa, is my line OK?" (SIM Swap). Problem slide card rewritten. |
| A2 | The OTP threat model is backwards (the attacker receives the code, not the victim) | Same fix as A1. The "a network fact stops the assistant from doing harm" framing is replaced by "the Echo is the one device that still works after a SIM swap." |
| A3 | Number Verification cannot bind a line from an Echo (Wi-Fi, not cellular) | Consent slide and care slides now state: one tap on the phone, over mobile data, via a link Alexa sends. "Nothing to tap" removed. |
| A4 | Alexa+ cannot speak first through the MCP add-on | All three care flows re-drawn: alerts to the second person go by SMS/push from Tower's backend; Mom's Alexa only answers when asked. |
| A5 | Bedrock in the hot path is redundant and slow; Alexa+ already phrases | Architecture and AWS slides: Bedrock moved off the request path. Kept for the Strands reference client only; alert and consent texts come from templates. |
| A6 | QoD boost is weak and barely live commercially | Demo moment 2 replaced with the call-forwarding alarm (Call Forwarding Signal API). QoD removed from the API table. |
| A7 | Transplant check interval (hourly) cannot detect a 20-minute outage | Flow now uses the Reachability subscription (event-driven) with a 5-minute poll as fallback; a note that "reachable ≠ the call rings" (Do Not Disturb) is added. |
| A8 | Location Verification listed as used but has no tool | Removed from the API table; replaced with Call Forwarding Signal. |
| A9 | Fire TV extensions don't fit the Alexa+ track | Moved off the roadmap slide's main cards. |
| A10 | Cost is a placeholder | Filled with an estimate (see tracks slide; assumptions in speaker notes). |
| A11 | "Subscriber side" should be "consumer-agent side"; Tower-the-company is the contracted API consumer and collects consent via auth-code / CIBA | Wording corrected on cover, idea, honest and consent slides. |
| A12 | Carrier SIM-lock features (AT&T Wireless Lock, T-Mobile SIM Protection, Verizon Number Lock) are prior art | Added as a row on the prior-art slide, with the note that Tower is detection-and-alert and complements a lock; Canadian carriers offer PIN only. |
| A13 | MCP Toolkit is US-only; use the web simulator | Noted on the tracks slide and in the closing ask. |
| A14 | "Twenty-nine days" is stale | Day count updated. |
| A15 | AgentCore Gateway does not support CIBA | Noted on the AWS slide as "custom code if CIBA is the carrier's consent flow". |

---

## 2. Pushbacks — where the review is wrong, overstated, or needs context

### P1. "Quality on Demand does nothing for an Echo" — the objection misreads the demo, but the demo was still worth dropping

**What the review says.** QoD boosts a cellular session; the Echo's traffic goes over home broadband; therefore "boost my upload" cannot work.

**Why that misreads the demo.** The boost was never for the Echo's traffic. The scenario was a subscriber at a laptop or phone on cellular, using the Echo only as the voice surface to *request* a QoD session on their own line. The Echo carries the command; the session applies to the phone's cellular bearer. CAMARA QoD takes a `device` (the phone, identified by MSISDN or IP) and an `applicationServer`; the Echo appears nowhere in the request. On that point the reviewer is simply wrong.

**Why we dropped it anyway.** Three reasons that are independent of the reviewer's one:

1. QoD requires a specific `applicationServer` (IPv4/IPv6 address or range). "My upload" doesn't name one. A real implementation would need the user to say *what* they're uploading to, or Tower would need to guess — neither works by voice.
2. Commercial availability is thin (the review cites Vodafone Germany, Sep 2026). In a sandbox the session is simulated, so the demo would show a lifecycle, not an effect.
3. The replacement — call-forwarding detection — is a better second moment: same safety pattern as the hero, a real CAMARA API, and it doesn't need an application server.

**Record for the pitch.** If a judge raises "QoD can't help an Echo", the correct answer is "it was never for the Echo", followed by "we dropped it because the API needs an application server address, which a voice request doesn't carry". Both halves are true; only the second is a concession.

---

### P2. "Find My already answers 'is Dad's phone on' and answers it better" — different mechanism, different reach; concede the demo slot, not the point

**What the review says.** Apple Find My, Verizon Family and T-Mobile FamilyMode already do this, with more detail.

**What is actually different.**

| | Find My / family locators | Device Reachability (CAMARA) |
|---|---|---|
| Requires | an app on *Dad's* phone, an Apple/Google account, the right ecosystem on both ends | nothing installed; any handset including a flip phone |
| Returns | a position on a map, battery, often history | a boolean: on the network or not |
| Works when | the phone is on and signed in to the app | the phone is attached to the network — app or no app |
| Consent model | account-level sharing, usually permanent | per-tool, per-line, revocable, audited |
| Privacy shape | retrieval (where he is) | verification (is he reachable) |

For the people the care slides are about — a parent with a six-year-old flip phone, a relative who will never install an app, a family split across Android and iPhone — "no app on his phone" is the whole difference. And "boolean, never a position" is the privacy posture the entire deck is built on. A family locator that answers more is also a family locator that *exposes* more.

**What we concede.** As a *demo moment*, "Is Dad's phone on?" is weak, because a judge's first association will be Find My and the distinction takes a paragraph to explain. So it comes out of the demo and stays on the care slides, where the consent flow is on screen and the distinction is visible.

**Record for the pitch.** "Find My needs an app and shows a location. This needs nothing on his phone and returns a yes or no. Those are the two reasons it exists."

---

### P3. "Name Nokia explicitly" — a deliberate omission, not an oversight

**What the review says.** Slide 6 should name Nokia's Network as Code MCP server and its simulators as the carrier backend.

**Why the deck doesn't.** The author's employer is named Nokia. Naming the employer's platform in a personal hackathon entry raises questions — IP, conflict of interest, whether the entry is read as a company project — that have nothing to do with the idea's merit and everything to do with how the entry is judged and whether it can be submitted at all. The deck therefore says "carrier API platforms are starting to publish their own [MCP servers]" and "carrier developer sandbox or CAMARA-conformant mock". That is accurate and it is not evasive; it is generic by intent.

**Where the reviewer has a point.** A prior-art slide is more convincing when it names the thing. The compromise:

- The **deck** stays generic.
- The **repo's prior-art document** (`docs/prior-art.md`, to be written) names every platform the review lists, including Network as Code, the community `camara-sdk` MCP server, the Vonage MCP servers and the CAMARA MCP position paper, with links — so a judge who reads the repo finds the full picture.
- The **demo backend** is the CAMARA-conformant mock, not any vendor's sandbox, which also removes the dependency the review flagged as "uncertain" (free-tier limits).

**Record for the pitch.** If asked directly: "Several carrier platforms now expose these APIs to agents, including through MCP; the repo lists them. What they don't have is the consumer's own agent, with the consumer's consent, on the consumer's assistant."

---

### P4. "The silence alarm measures the phone, not the person" — correct, and the slide already says so; the fix is a caveat, not a cut

**What the review says.** A phone on the nightstand stays reachable while she's on the floor; a flat battery triggers a false alarm; family apps already send low-battery alerts. Recommendation: cut it, or pair it with a signal that shows the *person* is active.

**Where we agree.** All three observations are true. "Reachable" is a property of the handset. That is exactly why the slide says "unreachable, not unwell" and why the red strip says never "fall", never "emergency", never an automatic 911.

**Where we differ.** The review treats "measures the phone" as a reason to cut; we treat it as the reason the wording is what it is. The alarm is honest about what it measures. The real cost is the false alarm from a flat battery, and that is a product tuning problem (threshold, daytime window, a "did you mean to switch it off?" check when she next asks Alexa anything) rather than a reason the idea doesn't belong.

**What changed.** The slide now carries the caveat in its own words — "a flat battery looks the same; pair with an activity signal before this ships" — and it is explicitly a roadmap item, not a demo moment. It will not appear in the video.

---

### P5. "Nothing runs against a real line, which weakens Impact" — true, allowed, and partially fixable

**What the review says.** Every network fact comes from a mock. Fine under the rules, but it weakens Impact and Technical Execution.

**Where we agree.** It does. A judge who sees a mock flip a boolean has seen a state machine, not a network.

**What the review under-weights.** The hackathon rules permit simulators and mocks, and the review itself confirms that. The mock is also what makes the demo *reproducible from the README* — a judge can run it — which is itself a scoring item. A sandbox-backed demo that works on the author's credentials and nobody else's is less verifiable, not more.

**What we'll do.** Build the mock to the CAMARA OpenAPI specs byte-for-byte (schemas, error codes, the subscription variants), ship conformance tests against the published specs, and say on the architecture slide — as it already does — that the gateway cannot tell which backend it is talking to. If a sandbox becomes available before the 23rd, the video shows one call against it and the rest against the mock; the README says which is which.

---

### P6. "Heavy AWS stack for three tools risks latency and time" — half right; the hot path is now three hops

**What the review says.** AgentCore Runtime, Gateway, Identity, Bedrock and DynamoDB is a lot of machinery for three tools, and the review's cited ~500 ms budget for an MCP response is at risk.

**Where we agree.** With Bedrock in the hot path, yes. Removed (A5).

**Where we differ.** After the change the request path is: Alexa+ → Tower (Runtime) → Gateway → carrier → back. Gateway and Identity are not extra hops in any meaningful sense; they replace a hand-written CAMARA client and a credential store that would otherwise have to exist anyway. DynamoDB is one read (consent) per call. The stack is deep on the slide because it is *named*, not because it is slow. The 500 ms figure could not be confirmed from the toolkit overview page; the review cites the client-lifecycle page for it. It should be treated as real until shown otherwise, and the build plan includes measuring the end-to-end path against the mock in the first week.

---

### P7. "The guard is only advisory — the LLM decides whether to call the tool" — true of every MCP tool; the fix is the one we already made

**What the review says.** Alexa+'s model decides whether to call `line_is_safe_for_otp`; nothing forces it to ask Tower first.

**Where we agree.** Entirely. This is a property of MCP, not of Tower. Tool descriptions can make a call *likely*; they cannot make it *certain*.

**What follows.** The hero moment can't be one where Tower silently intervenes in something Alexa+ was about to do. It has to be one where the *user* asks — "is my line OK?" — and the answer is the value. That is the new demo moment 1, and it is why the proactive path (A4) goes through SMS from Tower's backend rather than through Alexa+. The review's own fix 5 and fix 2 together resolve this; we've adopted both.

---

## 3. Items the review lists as uncertain — our position

| Item | Position |
|---|---|
| Nokia NaC free-tier limits | Moot for the demo: the backend is the mock. |
| Whether TELUS network APIs are live | Irrelevant to the demo; relevant to a go-to-market slide we are not making. |
| Whether Alexa+ can read phone notifications on iOS | Assume no. The deck no longer depends on it anywhere. |
| Aduna / AWS integration details | Not needed; noted as a possible future backend. |

---

## 4. Net effect on the pitch

The hero moment is now one that is unique to a home assistant — *the Echo still works after your phone goes dead* — instead of one built on a capability Alexa doesn't have. The second moment uses a real API with the same safety shape. The third shows consent for someone else's line and an alert arriving on a real phone. Bedrock is out of the hot path, binding is honest about the one tap it needs, and the prior-art slide names the carrier locks.

The review's own estimate was ~8/10 with fixes 1, 2 and 5. Those three are applied, along with twelve others.
