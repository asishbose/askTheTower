# Ask the Tower — Architecture

Your own agent on Alexa+ asking the carrier about your line, from the one device that still works after your phone goes dead. Consent first.

This folder is the engineering view of the pitch in `../Decks/`. Each component has its own document; the wiring document shows how they connect; the testing document shows how each is proven and demonstrated on its own before the end-to-end demo.

Team: Asish Bose and Badhrinath Padmanabhan (Canada).

Status: built — see artifacts/ (2026-10-07; evidence index in `docs/submission/checklist.md`). Designed 2026-10-05. Diagrams are the source of truth for shapes; the markdown is the source of truth for contracts.

## Read in this order

1. [`e2e-wiring.md`](e2e-wiring.md) — the three paths (request, proactive, binding), the contracts between components, and the two environments (local compose, AWS).
2. The component you're about to build, under `components/`.
3. [`testing-and-showcase.md`](testing-and-showcase.md) — how to run and demo that component alone, then the whole thing.
4. [`bind-and-alert-flows.md`](bind-and-alert-flows.md) — the two demo flows end to end (bind link, alert SMS) with the decisions of 2026-10-09 on the deployed chat page, Cognito and Mom's account.

## Components

| # | Component | Runs on | One line |
|---|---|---|---|
| 1 | [Alexa+ surface](components/01-alexa-surface.md) | Alexa+ (web simulator for the demo) | The MCP client. Asks; never speaks first. Phrases results itself. |
| 2 | [Tower MCP server](components/02-tower-mcp-server.md) | AgentCore Runtime | Three tools: `line_is_ok`, `is_reachable`, `watch_line`. Structured results only. |
| 3 | [Policy engine](components/03-policy-engine.md) | inside Tower | Deterministic. Consent and binding first; booleans in, one outcome out. |
| 4 | [Consent and line binding](components/04-consent-and-binding.md) | Lambda + DynamoDB | One tap on the phone over mobile data; grants per line per tool; revocable. |
| 5 | [Carrier gateway](components/05-carrier-gateway.md) | AgentCore Gateway + Identity | CAMARA OpenAPI → MCP tools; outbound OAuth; backend switch (mock / sandbox). |
| 6 | [Alerts service](components/06-alerts-service.md) | Lambda + EventBridge + SNS | Subscriptions and polls → policy → SMS to the consented second person. |
| 7 | [Audit log](components/07-audit-log.md) | DynamoDB + Observability | Every check, allow or refuse; "who checked my line this week?" |
| 8 | [Mock carrier](components/08-mock-carrier.md) | container (compose / Fargate) | CAMARA-conformant mock with a scenario engine and admin API. The demo default. |
| 9 | [Reference client](components/09-reference-client.md) | local / AgentCore | Strands agent on Bedrock; the demo path if Alexa+ access slips; the test harness. |
| 10 | [Scheduler and infrastructure](components/10-scheduler-and-infra.md) | AWS + compose + Helm | Tables, topics, schedules, secrets, Terraform, the one-command run. |

## Diagrams

| File | View |
|---|---|
| [`diagrams/01-component-map.drawio`](diagrams/01-component-map.drawio) | Every component, its runtime, and every edge between them |
| [`diagrams/02-request-path.drawio`](diagrams/02-request-path.drawio) | "Alexa, is my line OK?" — sequence, with the hot-path budget |
| [`diagrams/03-proactive-path.drawio`](diagrams/03-proactive-path.drawio) | Carrier event or poll → policy → SMS — sequence |
| [`diagrams/04-binding-flow.drawio`](diagrams/04-binding-flow.drawio) | One tap on the phone: Number Verification and the grant |
| [`diagrams/05-mock-carrier.drawio`](diagrams/05-mock-carrier.drawio) | Mock internals: scenario engine, clock, endpoints, admin API |
| [`diagrams/06-test-topology.drawio`](diagrams/06-test-topology.drawio) | Per-component harnesses, the showcase order, and what each proves |
| [`diagrams/07-alexa-and-mcp.drawio`](diagrams/07-alexa-and-mcp.drawio) | Where Alexa+ and the MCP server run, and every flow between them (request, account linking, binding, proactive) |
| [`diagrams/08-agentcore-deployment.drawio`](diagrams/08-agentcore-deployment.drawio) | Deploying on Amazon Bedrock AgentCore, in three pages: the account after `make deploy`, the install sequence step by step, and integrations and config. Companion: [`deployment-agentcore.md`](deployment-agentcore.md) |
| [`diagrams/09-refusal-paths.drawio`](diagrams/09-refusal-paths.drawio) | The five refusal exits of a tool call (NOT_BOUND, NO_CONSENT, STALE_DATA, CARRIER_ERROR, SERVICE_UNAVAILABLE): where each leaves, the code that decides it, what Alexa+ is handed |
| [`diagrams/10-watch-profiles.drawio`](diagrams/10-watch-profiles.drawio) | Transplant and care profiles over time: subscription, continuous-unreachable windows, first SMS, escalation and acknowledgement, revocation, care daytime window, 6 h rate limit |
| [`diagrams/11-demo-ui.drawio`](diagrams/11-demo-ui.drawio) | The demo control room (laptop only, built 2026-10-09), in two pages: its four panes and every endpoint each one calls under ENV=local, and what a laptop can still reach under ENV=aws. Companion: [`components/11-demo-ui.md`](components/11-demo-ui.md) |

**Explainer (for presenting, not reference):** [`explainer/`](explainer/README.md) — one ten-page file, [`explainer/ask-the-tower-explainer.drawio`](explainer/ask-the-tower-explainer.drawio), that tells the story in the deck's order for a judge, an engineer, a carrier partner or an executive, with a 60-second talk track per page and a path per audience. Generated by `diagrams/gen/gen_explainer.py`; PNGs in `explainer/png/`.

Open in [diagrams.net](https://app.diagrams.net) or the VS Code Draw.io Integration extension. 08 is generated by `diagrams/gen/gen_deploy.py`, 09 and 10 by `diagrams/gen/gen_flows.py`, 11 by `diagrams/gen/gen_demo_ui.py`, and the as-built branches appended to 03 and 04 by `diagrams/gen/gen_extend.py` (the original cells are untouched); all are checked by `diagrams/gen/check.py`. Never hand-edit their XML.

**Code vs docs:** [`code-vs-docs.md`](code-vs-docs.md) walks every step of 02, 03, 04, 09 and 10 against the code and lists each divergence with file:line and whether the code or the doc should change.

## Design rules, restated (from the deck and `../review-response.md`)

1. **Alexa+ phrases; Tower returns structured results.** No second model in the request path.
2. **Policy is deterministic code** with a network fact as input, tested as a table. The model never decides whether a call is allowed.
3. **Verification, not retrieval.** Every tool returns booleans and timestamps. No location, no content, no history beyond the audit.
4. **Consent is per line, per tool, revocable, audited.** Tower is the contracted API consumer; the person consents.
5. **Alexa+ cannot speak first through MCP.** The proactive path is SMS/push from Tower's backend.
6. **The mock is the demo default.** A carrier sandbox is a backend swap the gateway cannot see.
7. **Binding needs one tap on the phone over mobile data.** Said plainly everywhere.

## Vocabulary

- **line** — a phone number, referenced in tools as `self` or an alias (`mom`) that the consent store resolves.
- **line-holder** — the person who bound the line and owns its consent.
- **watcher** — a user granted `watch_line` on someone else's line; receives alerts.
- **check** — one evaluation of a line by the policy engine, from any trigger.
- **outcome** — `ok`, `changed` or `refuse`, with reason codes; the audit also records `suppressed`.
