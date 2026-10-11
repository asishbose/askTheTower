# 02 — Tower MCP Server

**Role:** the thing Alexa+ connects to. Three tools, a thin handler per tool, everything else delegated.
**Runs on:** AgentCore Runtime (MCP server hosting). Locally: a container.
**Language:** Python, FastMCP, Streamable HTTP transport.

---

## 1. Shape

```
tower-mcp/
  tower/
    server.py          # FastMCP app, transport, auth middleware
    tools/
      line_is_ok.py
      is_reachable.py
      watch_line.py
    policy/            # component 03 (imported, pure) — includes phrasing.py, shared with Alerts
    consent/           # client for component 04
    carrier/           # client for component 05 (Gateway or direct)
    audit/             # writer for component 07
    schemas.py         # pydantic models for every request/result
  tests/
  Dockerfile
  helm/
```

One process, stateless. All state lives in DynamoDB (consent, watches, audit) or at the carrier.

*As built (prompt 08):* the package is `services/tower-mcp/src/tower_mcp/` (`server.py`, `auth.py`, `deps.py`, `schemas.py`, `descriptions.py`, `next_step.py`, `errors.py`, `tools/`); policy, consent, carrier and audit are the shared workspace packages (`tower-policy`, `tower-consent`, `camara-client`, `tower-audit`), not sub-folders. Transport is stateless Streamable HTTP with JSON responses (no SSE stream, no session id) at `/mcp`; `/healthz` alongside.

## 2. The tools

### `line_is_ok(line="self")`

```
1. user_id ← request identity           (401 if absent)
2. line_id ← consent.resolve(user_id, line)
       → NOT_BOUND / NO_CONSENT short-circuits with a structured refusal
3. facts  ← Watches.last_state if a Watch exists and last_state.at is within FRESH (10 min)
          else carrier.sim_swap_check(line_id, max_age=72h)
             + carrier.call_forwarding(line_id)    (parallel, 300 ms budget each)
4. outcome ← policy.evaluate_line(facts, consent, now)
5. audit.append(user_id, line_id, "line_is_ok", outcome)   ← before returning
6. return Result(summary=phrasing(outcome), facts, reason_codes, next_step)
```

### `is_reachable(line)`

Same skeleton; one carrier call (`device_reachability`); `policy.evaluate_reachability(...)` → `ok` (reachable), `changed` with `UNREACHABLE`, or a refusal.

### `watch_line(line, enable | null)`

With `enable` true/false: writes a Watch record (component 04 store), registers or removes carrier subscriptions via component 06 (as built: `POST {ALERTS_INTERNAL_URL}/internal/watch {line_id, watcher_user_id, enable, profile}`; Alerts owns the subscriptions). With `enable` null: a status query. Either way it returns the standard envelope with `facts = {watching, since, notify_via, profile, grants: [{alias, grant}], recent_checks: {by_actor: {...}, outcomes: {...}}}` — this is how "who can see my line?" and "who checked my line this week?" are answered by voice. No carrier *check* is made here — watching is a state change or a question about state, never a question to the carrier.

*Built 2026-10-09 (D8, D9):* `enable=true` keeps the caller's stored profile and escalation exactly as stored — for the line-holder, what they saved on the binding page (04 §9); with no Watch the defaults are `self` for one's own line and `care` for an alias, chain `[caller]`. A grantee's call reads only their own Watch row, never the line-holder's settings. `profile` in `facts` is the stored value, also while watching is off (`null` with no Watch). With `transplant` or `care` the `enabled` summary is the fixed "Alerts are on for that line. A text goes out if it's SIM-swapped, forwarded, or off the network too long." Contacts never appear in a result. The arguments and description do not change. See 06 §11.1.

## 3. Result contract

```json
{
  "summary":      "string — spoken-friendly, from templates",
  "facts":        { ... tool-specific, booleans and timestamps only ... },
  "reason_codes": ["OK" | "SIM_SWAPPED_RECENT" | "CALL_FORWARDING_SET" | "UNREACHABLE"
                   | "NOT_BOUND" | "NO_CONSENT" | "STALE_DATA" | "CARRIER_ERROR"],
  "next_step":    { "kind": "none" | "call_carrier" | "bind_line" | "ask_consent", ... },
  "checked_at":   "RFC 3339"
}
```

Refusals use the same envelope. `NOT_BOUND` carries `next_step.kind = "bind_line"` with the binding URL, so Alexa+ can say "I've texted you a link to connect your line".

**Never in a result:** a subscriber's phone number, a location, a carrier error string, a stack trace. (The carrier's public support number in `next_step` is fine — it's not anyone's line.)

## 4. Budget

| Step | Target p95 |
|---|---|
| identity + consent resolve (1 DynamoDB read) | 20 ms |
| carrier calls (parallel, via Gateway; 300 ms hard timeout each) — skipped when a fresh Watch state exists | 250 ms against the mock; unknown against a sandbox; ≈ 0 ms on the stored-state path |
| policy + phrasing | < 1 ms |
| audit write (async after response is built, awaited before return) | 15 ms |
| **total** | **≈ 300 ms** against the mock |

Measured, not assumed — see `testing-and-showcase.md` §2.2; the CI gate is p95 < 400 ms. That the stored-state path skips the carrier is asserted by counting carrier requests in the same run, not by comparing timings (a p50 comparison flaked under load; decided 2026-10-11): a live answer fetches its fact set once — `line_is_ok` makes one SIM Swap `check` and one Call Forwarding Signal call (plus `retrieve-date` only when the check says swapped, D17), `is_reachable` one Device Reachability Status `retrieve` — and the stored-state path and `watch_line` make none (`services/tower-mcp/tests/test_latency.py`, `scripts/latency.py` `CountingTransport`). p50/p95 are written to `artifacts/latency.md` as a report.

**Stored-state first.** A watched line is already being observed by Alerts (06) through subscriptions and polls, so `Watches.last_state` is at most minutes old. When it is within `FRESH` (10 min, the same value as `STALE`), the tool answers from it and `checked_at` is `last_state.at`; the carrier is not called on the hot path. An unwatched line (the common case for `self` on first use) is checked live. This keeps the policy identical — `evaluate_line` sees the same `Facts` either way — and makes the voice answer independent of carrier latency wherever a Watch exists. The demo seeds a `self` Watch for the presenter's line so moment 1 shows the stored path and the audit row says `source: watch`. If a sandbox is slow, the carrier call gets a hard 300 ms timeout and the result is `STALE_DATA` with the last known state from the Watch record, clearly marked.

## 5. Auth and identity

- Inbound: the transport-level auth Alexa+ provides (bearer); middleware extracts `user_id`.
- Outbound: nothing — carrier credentials live in AgentCore Identity (component 05); Tower never sees them.
- Local dev: a static bearer and a `X-Tower-User` header, enabled only when `TOWER_ENV=local`.

## 6. Failure behaviour

| Failure | Result |
|---|---|
| Consent store unavailable | refuse with `SERVICE_UNAVAILABLE`; never guess consent |
| Carrier timeout | `STALE_DATA` with last-known facts if a Watch exists, else `CARRIER_ERROR` — for `line_is_ok`, *either* of its two calls failing counts (never `OK` on a half answer); a known alarm from the call that did answer is still reported as `changed` and is never replaced by older stored facts (03 §3) |
| Policy raises | 500, logged; never a partial result |
| Audit write fails | **refuse the response** — no audit, no answer |

The last row is the same rule as every other part of the system: degrade toward silence, never toward an unaudited answer.

## 7. Showcase on its own

`make showcase-tower` runs the server against the mock with the local bearer. The reference client (09) or the MCP Inspector calls each tool on the seeded `demo.yaml` state, then after `POST /_admin/lines/<number>/events {sim_swap}`, then after `{cf_set}`, and the structured results change accordingly. What it proves: the three tools, the envelope, the refusal paths, and the latency number. See `testing-and-showcase.md` §2.2.
