# 07 — Audit Log

**Role:** the record that makes consent real after the fact. Every check, allow or refuse, from any trigger, with who asked and why — readable by the line-holder.
**Runs on:** DynamoDB `Audit` table, written explicitly by Tower (02) and Alerts (06); enriched from AgentCore Observability traces.
**Never contains:** what was observed about the line beyond the outcome and reason codes.

---

## 1. Two sources, one table

| Source | Writes | Why both |
|---|---|---|
| **Explicit append** in Tower and Alerts, before the response/alert is released | the business record: who, which line, which tool, outcome, reason codes | the write that blocks the answer — no audit, no answer |
| **Observability traces** (AgentCore) → nightly reconciliation | latency, carrier call made or not, errors | proves the explicit record wasn't skipped; produces the latency numbers for the README |

The explicit append is authoritative; the trace is the check on it. A trace with no matching audit row is a bug and an alarm.

## 2. Record

```
PK  line_id
SK  ts#seq
    actor_user_id          who asked (or "system:alerts" with the trigger kind)
    tool                   line_is_ok | is_reachable | watch_line | alert
    trigger                voice | poll | event | binding
    source                 carrier | watch   (where the facts came from on a voice check; 02 §4)
    outcome                ok | changed | refused | suppressed
    reason_codes           [...]
    message_ref            template id of any alert sent (never the body)
    policy_version         thresholds file hash
    prev_hash              SHA-256 of the previous row's canonical JSON (per line)
    ttl                    90 days
```

Hash-chained per line: a resident's `verify` walks their own chain. Chains are per line, not global, so one resident's log can be verified without reading anyone else's.

Implementation notes (`packages/tower-audit`): `ts#seq` is fixed-width RFC 3339 UTC with microseconds plus a 4-digit seq; hashes and `policy_version` are hex re-lettered `a`–`p` so no stored value can match the phone-number regex; a first row's `prev_hash` is `genesis`. A per-line **head item** (SK `~head`: newest `ts#seq` and hash) is moved in the same transaction as each row put, so concurrent writers cannot fork a chain and `verify` catches an altered or deleted newest row. `recent_checks` counts `line_is_ok`, `is_reachable` and `alert` rows, not `watch_line` (asking "who checked?" is not a check).

## 3. What is deliberately absent

- The facts themselves (`latest_sim_change`, `reachable`) beyond what the reason codes imply. The audit says "a check happened and the line was reported changed"; it does not become a second store of the line's history.
- Phone numbers. `line_id` is the HMAC.
- Alert message bodies. The template id is enough.

A stolen audit table reveals who asked about whom and when — a real but bounded leak — and nothing about any line's state history.

## 4. Reading it

- **By voice:** "Alexa, who checked my line this week?" → `watch_line(line="self", enable=null)` (02 §2) returns `recent_checks` in its facts, and a summary: counts by actor and outcome, phrased from a template ("Asish checked twice; both times the line was fine. The daily check ran seven times."). No timestamps spoken at second precision.
- **On the binding page:** the full list for lines the viewer owns, with the chain verification status.
- **Never:** a watcher reading the audit of the line they watch. They see their own actions in their own log; they do not see Mom's view of who else checked.

## 5. Retention and trimming

90 days by TTL. Trimming re-anchors each line's chain: the new oldest row's `prev_hash` is replaced by a signed `trimmed_at` marker, so `verify` still passes and still reports that rows were trimmed. The marker (`trimmed|<trimmed_at>|<original prev_hash>|<HMAC>`) keeps the original `prev_hash`, so the anchor row's own hash — and the next row's link — is unchanged; trim runs a day ahead of TTL (`before = now − 89 days`).

## 6. Tests

- Append-before-release: kill the process between decision and response; on restart the audit row exists and no response was sent.
- Chain verify: tamper one row → verification names it.
- Reconciliation: delete an audit row behind a trace → nightly job raises.
- Content: regex over every stored field for anything digit-shaped like a number; template ids only in `message_ref`.

## 7. Showcase on its own

`make showcase-audit`: after the alerts demo, open the binding page as Mom: the log shows the daily check, Asish's voice check, the alert, and the `SUPPRESSED_REVOKED` row after revocation, with a green "chain verified". Then by voice: "who checked my line this week?" See `testing-and-showcase.md` §2.7.
