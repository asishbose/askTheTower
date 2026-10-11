# Alexa+ simulator run — run sheet

> **Status: template, not run** (RUN-ALL step 15: no Alexa+ account in the autonomous build). Fill this in during
> the run of `registration.md` §7. `make showcase-alexa` prints the same list and captures one Tower-side
> transcript per tool call (`artifacts/transcripts/alexa-NN-<tool>.json`). Put the file name in the
> "Transcript" column.

Run date: TODO(human) · Path (local + tunnel / AgentCore): TODO(human) · Simulator locale: en-US ·
Linked user: `asish` (Tower `user_id` = its `sub`) · Lines bound: Asish's (self) and Mom's (alias "mom", `watch`
grant)

How to fill in:

- **Tool called / args** comes from the log pane. "—" means no call.
- **Alexa+ said** is word for word.
- **Verbatim?**
  - **V**: `summary` read word for word.
  - **P**: paraphrased, with the facts kept.
  - **P!**: paraphrased, and a fact changed or added (a finding).
  - **—**: no tool result to read.
- **Pass** means the call matches the expectation, as in the reference client's corpus scoring.

## A. Tool selection (testing-and-showcase §2.1)

Utterances are from `services/ref-client/corpus/phrasings.yaml`: demo-tagged first, then five per tool. Reset
the mock before each group: `POST /_admin/scenarios/load {"name":"demo"}`. That makes every `line_is_ok` answer
`OK` and every reachability answer reachable.

| # | Utterance | Expected | Tool called / args | Alexa+ said | Verbatim? | Pass | Transcript |
|---|---|---|---|---|---|---|---|
| 1 | my phone just lost signal, is my line ok? | `line_is_ok {line: self}` | | | | | |
| 2 | is anything forwarding my calls | `line_is_ok {line: self}` | | | | | |
| 3 | is mom's line ok | `line_is_ok {line: mom}` | | | | | |
| 4 | did my sim get jacked | `line_is_ok {line: self}` | | | | | |
| 5 | has somebody hijacked my number | `line_is_ok {line: self}` | | | | | |
| 6 | is mom's phone on | `is_reachable {line: mom}` | | | | | |
| 7 | is my phone on | `is_reachable {line: self}` | | | | | |
| 8 | is my phone reachable now | `is_reachable {line: self}` | | | | | |
| 9 | can mom's phone be reached right now | `is_reachable {line: mom}` | | | | | |
| 10 | does mom's phone have signal | `is_reachable {line: mom}` | | | | | |
| 11 | watch mom's line for me | `watch_line {line: mom, enable: true}` | | | | | |
| 12 | turn on alerts for my line | `watch_line {line: self, enable: true}` | | | | | |
| 13 | text me if anything happens to mom's sim | `watch_line {line: mom, enable: true}` | | | | | |
| 14 | stop watching mom's line | `watch_line {line: mom, enable: false}` | | | | | |
| 15 | turn off the alerts on my number | `watch_line {line: self, enable: false}` | | | | | |
| 16 | what's the weather in ottawa | no tool | | | — | | — |
| 17 | what's a good phone plan for a teenager | no tool (near miss) | | | — | | — |
| 18 | is the line ok | **clarifying question** ("yours or Mom's?"), no call yet | | | — | | |

Score: TODO(human) / 18. Compare with the reference client's corpus pass rate (prompt 11; `make corpus` →
`artifacts/corpus.md`). Acceptance requires the Alexa+ score to be ≥ that rate, or every miss listed below with
the description change made.

### Misses and description changes

Change tool descriptions only in `services/tower-mcp/src/tower_mcp/descriptions.py`, `components/01` §2 and the
corpus together. 08's drift test (`tests/test_descriptions.py`) fails otherwise. Re-run `make corpus` after any
change.

| # | What Alexa+ did | Description change (diff) | Re-run result |
|---|---|---|---|
| | TODO(human) | | |

### The clarifying question (row 18)

TODO(human): exactly what Alexa+ asked. Then answer "Mom's". Record whether the follow-up call has
`line: "mom"`, and whether Alexa+ asked or guessed `self` without asking. A guess is a miss. 01 §1 expects the
question.

## B. The three moments (testing-and-showcase §4 steps 4–6, as the deck phrases them)

Reset first. The admin lines are printed by `make showcase-alexa`; paste them into the second pane in order.

| Step | Utterance / admin call | Expected reason codes | Alexa+ said | Verbatim? | Transcript |
|---|---|---|---|---|---|
| M1.1 | "Is my line OK?" | `OK` | | | |
| M1.2 | admin: clock +720 s (the demo timeline swaps Asish's SIM) | — | — | — | — |
| M1.3 | "My phone just lost signal. Alexa, is my line OK?" | `SIM_SWAPPED_RECENT`, `next_step.call_carrier` | | | |
| M2.1 | admin: clock +480 s (timeline: `cf_set`) | — | — | — | — |
| M2.2 | "Is anything forwarding my calls?" | `SIM_SWAPPED_RECENT`, `CALL_FORWARDING_SET` | | | |
| M3.1 | "Is Mom's line OK?" | `OK` ("as it was") | | | |
| M3.2 | "Watch Mom's line for me." | `OK`, watching | | | |
| M3.3 | admin: `sim_swap` on Mom's line → Alerts' `SMS to=` line in the log pane (the buzz) | — | — | — | — |
| M3.4 | admin: Mom revokes the grant; `sim_swap` again → no SMS (`SUPPRESSED_REVOKED` in the audit) | — | — | — | — |
| M3.5 | "Is Mom's line OK?" | `NO_CONSENT` | | | |
| M3.6 | admin: re-grant (reset) | — | — | — | — |

Recording: `artifacts/alexa-simulator.mp4`. TODO(human): duration, and whether the reason codes are readable in
the log pane.

## C. Answers to components/01 §7

Copy both answers into `components/01` §4 / §7 when they are known, and delete the "Open" items there.

1. **The identity Alexa+ passes to a self-hosted MCP server.** TODO(human): the header, the token type and the
   claims present (names only, no values); whether it matched `auth.py` (bearer JWT, `user_id` = `sub`); and
   whether Tower needs its own account-linking endpoint. With Cognito as the IdP it does not.
2. **Whether Alexa+ reads `summary` verbatim or paraphrases.** TODO(human): count the V / P / P! rows above.
   Quote the M3.1 answer exactly, since it tests the fixed-person template issue in `registration.md` §7. If
   Alexa+ paraphrases, the reference client's transcript tests stay the regression guard (01 §7).

## D. Notes for the friction log

Copy anything that cost time or surprised you into `friction-log.md`, dated and reproducible.
