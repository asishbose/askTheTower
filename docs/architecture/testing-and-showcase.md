# Testing and Showcase Architecture

How each component is proven on its own, how the whole is proven end to end, and the order in which to demonstrate them so a judge (or you, on the 22nd) can see each piece work before the full run.

Diagram: [`06-test-topology`](diagrams/06-test-topology.drawio)

---

## 1. Principles

1. **Every component has a standalone showcase** — a `make` target that runs it against its nearest neighbour only, with a script of what to do and what it proves.
2. **The mock is the fixture.** Scenarios and the admin API make every test and every demo deterministic. Nothing depends on a sandbox being up.
3. **Three test layers, cheapest first:** unit (pure code), integration (component pairs over their real interface — contract, conformance, privacy, latency), end to end (`make demo`, showcase, chaos). §3 lists the seven kinds inside them.
4. **The reference client is the harness** for anything voice-shaped, so tool-selection and phrasing are tested without Alexa+.
5. **Latency is measured, not assumed**, from week one, against the mock.

## 2. Per-component showcase

Each entry: *runs against* · *script* · *proves* · *artefact*.

### 2.1 Alexa+ surface — `make showcase-alexa`
- **Runs against:** web simulator → local Tower → mock.
- **Script:** five phrasings per tool from `corpus/phrasings.yaml`, two off-topic utterances, one ambiguous "is my line OK" with two lines bound.
- **Proves:** tool selection from descriptions; `summary` spoken; the clarifying question.
- **Artefact:** screen recording of the simulator; the transcript JSON.

### 2.2 Tower MCP server — `make showcase-tower`
- **Runs against:** mock, local bearer, DynamoDB Local.
- **Script:** MCP Inspector lists the three tools; call `line_is_ok` on the seeded `demo.yaml` state → `OK`; `POST /_admin/lines/<your number>/events {sim_swap}`; call again → `SIM_SWAPPED_RECENT` with the right time; call on an unknown alias → `NO_CONSENT`; call as a user with no bound line → `NOT_BOUND` with a binding URL.
- **Proves:** the envelope, the refusal paths, audit-before-response (kill test runs here), and the p95 number from 200 calls.
- **Artefact:** `artifacts/latency.md` — the p50/p95 table that goes in the README.

### 2.3 Policy engine — `make policy-table`
- **Runs against:** nothing. Pure.
- **Script:** the cross-product test prints the decision table.
- **Proves:** determinism, consent-before-facts, thresholds bounded by the API maxima, no digits in templates.
- **Artefact:** `artifacts/policy-table.md`, embedded in the README.

### 2.4 Consent and binding — `make showcase-binding`
- **Runs against:** binding page → mock (client-id header simulating mobile data), DynamoDB Local.
- **Script:** open the page on a phone; with the "Wi-Fi on" simulation → refused; with the right client id → bound; grant `watch` to a second user by invite code with alias `mom`; show the tables; revoke; show `resolve` flip.
- **Proves:** one tap, nothing typed, grants and revocation, HMAC `line_id` in every table.
- **Artefact:** screen recording; table dump with numbers redacted by construction.

### 2.5 Carrier gateway — `make showcase-gateway`
- **Runs against:** AgentCore Gateway → mock on Fargate (AWS) — or `DirectClient` → mock locally.
- **Script:** list Gateway's generated tools (CAMARA operations as MCP tools); call `sim_swap_check` → `false`; flip the mock; call → `true`; swap `backend` config to a second mock instance; call again — identical result, no code change.
- **Proves:** OpenAPI → tools; backend swap; conformance fixtures pass through both clients.
- **Artefact:** the tool list output; conformance report.

### 2.6 Alerts service — `make showcase-alerts`
- **Runs against:** mock subscriptions → Alerts → SNS → a real phone (AWS) or log (local).
- **Script:** `watch_line("mom", true)`; fire `sim_swap` on Mom's line in the mock; the watcher's phone buzzes; fire again within 6 h → audited, not sent; revoke the grant; fire → `SUPPRESSED_REVOKED`; `transplant` profile: 19 min dark → nothing, 20 → text; no acknowledgement, advance 15 min → the second contact is texted.
- **Proves:** the proactive path without Alexa; revocation is live; window arithmetic and escalation; the swapped line is not texted.
- **Artefact:** phone screenshot of the SMS; audit rows.

### 2.7 Audit log — `make showcase-audit`
- **Runs against:** DynamoDB (Local or AWS) after 2.2 and 2.6.
- **Script:** open the binding page as Mom → the log with chain verification green; tamper a row in the table → verification names it; by voice: "who checked my line this week?"
- **Proves:** append-before-release, chain integrity, resident-readable, no numbers.
- **Artefact:** screenshot; `verify` output.

### 2.8 Mock carrier — `make showcase-mock`
- **Runs against:** itself.
- **Script:** `/docs` Swagger UI; load `demo.yaml`; `check` → `false`; advance clock 12 min → `true`; `retrieve-date` shows the moved timestamp; create a subscription to a local sink; fire an admin event; show the CloudEvent arrive; inject a timeout fault and show it count down.
- **Proves:** conformance (`/openapi.json` diffs clean against `specs/camara/`), determinism, subscriptions, faults.
- **Artefact:** conformance report; a short clip of the clock trick.

### 2.9 Reference client — `make showcase-ref`
- **Runs against:** local Tower → mock; Bedrock for the agent model.
- **Script:** the three demo moments from the terminal (TTS optional); then `make corpus` → the selection table.
- **Proves:** a second model selects the same tools from the same descriptions; the fallback demo path exists.
- **Artefact:** transcripts in `artifacts/transcripts/`; corpus pass/fail table.

### 2.10 Infrastructure — `make showcase-infra`
- **Runs against:** a clean machine; then AWS.
- **Script:** `time make up && make demo`; `make deploy` with the plan on screen; `make down`; billing console at zero.
- **Proves:** the README is sufficient; cost claim.
- **Artefact:** the timing; a screenshot of the plan summary.

## 3. Test layers and where they run

The suite runs as **three layers**, one pytest marker each — every test carries exactly one of `unit`, `integration`, `e2e` (or `nightly`), and an unmarked test fails collection. `make test` runs them in order and writes one report, `artifacts/test-report.md` (`make test-report`); `make test-unit`, `make test-integration` and `make test-e2e ENV=local|eks|aws` run each alone. The **seven kinds** of test sit inside the layers:

| Layer (marker) | Kind | What | Runs in | Gate |
|---|---|---|---|---|
| **Unit** (`unit`) | Unit | policy table; phrasing templates; consent resolve; HMAC/encryption helpers; window arithmetic; cross-package agreement (`tests/unit/`) | CI on every push | 100 % pass |
| **Integration** (`integration`) | Contract | Tower ↔ consent lib; Tower ↔ `CarrierClient` (both impls); Alerts ↔ mock subscriptions / SNS (stub); binding page ↔ carrier auth; audit writer ↔ DynamoDB Local | CI (testcontainers) | 100 % pass |
| | Conformance | mock vs. vendored CAMARA specs (schemathesis); fixtures through `DirectClient` and `GatewayClient` | CI (Direct, fake Gateway), nightly (Gateway on AWS) | 0 failing operations |
| | Privacy | grep captured logs, metrics, tool results, SMS bodies for E.164 patterns and health words; metrics labels contain no `line_id` | CI | zero hits |
| | Latency | 200 calls per tool against the mock; p50/p95 recorded; carrier requests counted per path | CI, trend chart | p95 < 400 ms on the request path; exactly one fact set per live answer (`line_is_ok`: SIM Swap check + Call Forwarding, `is_reachable`: one reachability call), zero carrier requests on the stored-state path (02 §4) |
| **End to end** (`e2e`) | End to end | `make demo` — the three moments + transplant closing story via the reference client; the showcase steps; Alerts §2.6; binding §2.4 in a headless browser | CI (`ENV=local`), pre-submission (`eks`, `aws`) | transcripts match golden files (`tests/e2e/golden/`, one set for all three ENVs) |
| | Chaos | mock faults (timeout, 500, 429) × each tool; subscription expiry; Lambda cold start mid-window (nightly) | CI (`ENV=local`), nightly (AWS) | must degrade to `STALE_DATA`/`CARRIER_ERROR`, never a wrong outcome |

Coverage is gated per path on unit + integration together: ≥ 90 % of `packages/`, ≥ 80 % of `services/` (`[tool.att.coverage-gates]`, enforced by `make test-report`). Nightly only (`nightly`): `GatewayClient` conformance against the real AgentCore Gateway, the AWS latency run, the backend swap, chaos with Lambda cold starts, the kind install, and the demo UI's pixel baselines (`services/demo-ui/tests/test_visual.py`, local baselines; 11 §11).

## 4. The showcase order (the video, and the live run)

1. **Mock** (2.8) — 20 s: "this is a carrier, built to the public spec; here's the clock."
2. **Policy table** (2.3) — 15 s: "this is every decision the system can make."
3. **Binding** (2.4) — 30 s: the one tap on a phone — Wi-Fi off against a real carrier; against the mock, the client-id header stands in and the slide says so.
4. **Moment 1** — Alexa+ simulator: "My phone just lost signal. Alexa, is my line OK?" → mock clock advanced 12 min → the answer.
5. **Moment 2** — "Is anything forwarding my calls?" → `cf_set` on the mock → the answer.
6. **Moment 3** — "Is Mom's line OK?" → "as it was" → fire `sim_swap` on Mom's line → **the watcher's phone buzzes on camera** → revoke → fire → nothing, and the audit shows why.
7. **Transplant closing story** (2.6, `transplant` profile) — 20 s: 20 minutes dark on the clock → the partner's text; 15 more → the second contact.
8. **Audit** (2.7) — 15 s: Mom's view, chain verified.
9. **`make demo`** — 10 s: the same transcripts from the terminal. "You can run this."

Under three minutes if steps 1–3 are tight. Steps 4–6 are the demo; everything else is evidence.

## 5. What the tests deliberately don't claim

- That a real carrier behaves like the mock in timing. The mock is spec-conformant, not latency-realistic; the latency gate is for *our* code.
- That Alexa+ will always pick the right tool. The corpus makes it likely and measurable; MCP makes it the model's call.
- That "reachable" means the phone will ring. Do Not Disturb is invisible to the network; the setup flow says so.

## 6. Artefacts checklist for submission

- [ ] `artifacts/policy-table.md`
- [ ] `artifacts/latency.md`
- [ ] `artifacts/conformance-report.html`
- [ ] `artifacts/transcripts/*.json` (three moments + transplant)
- [ ] phone screenshot of the alert SMS
- [ ] audit screenshot with chain verified
- [ ] `time make up && make demo` output on a clean machine
- [ ] Terraform plan summary and the zeroed billing screenshot after `make down`
