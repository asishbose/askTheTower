# tower-audit

Hash-chained audit log: append-before-release, verify, trim, `recent_checks`. Design:
[`docs/architecture/components/07-audit-log.md`](../../docs/architecture/components/07-audit-log.md).

Every check — allow or refuse, voice or alert — writes one row to the `Audit` table (defined in
`tower_consent.tables.AUDIT`, not here) **before** the answer or alert is released. Rows hold who asked, which
line (the HMAC `line_id`), which tool, the outcome and reason codes, the template id of any alert, the policy
version, and the previous row's hash. They never hold a phone number, a fact about the line beyond the reason
codes, or a message body.

## Use

```python
from tower_audit import AuditRecord, AuditWriteFailed, append, audit_outcome, release

record = AuditRecord(
    line_id=consent.line_id,
    ts=now,
    actor_user_id=user_id,
    tool="line_is_ok",
    trigger="voice",
    source="carrier",
    outcome=audit_outcome(outcome.kind),
    reason_codes=outcome.reason_codes,
    policy_version=policy_version(),
)
try:
    return release(store, record, lambda row: build_result(...))  # append, then respond
except AuditWriteFailed:
    return refusal(ReasonCode.SERVICE_UNAVAILABLE)  # no audit, no answer
```

| Function | What it does |
|---|---|
| `append(store, record, *, after_append=None) -> AuditRecord` | next row of the line's chain; one GetItem + one TransactWriteItems; retries a concurrent-writer collision once; raises `AuditWriteFailed` (never swallowed) |
| `release(store, record, respond)` | `append`, then `respond(row)`; if the append fails `respond` never runs |
| `verify(store, line_id, signer) -> VerifyResult{ok, rows, trimmed, first_bad_ts}` | walks one line's chain; names the altered row |
| `trim(store, line_id, before, *, signer, now)` | re-anchors the chain with a signed `trimmed|...` marker; TTL (90 days) deletes; run nightly with `before=trim_horizon(now)` |
| `list_for_line(store, line_id, viewer_user_id)` | every row, newest first — owner only, else `AuditAccessDenied` |
| `recent_checks(store, line_id, since, *, viewer_user_id)` | `{by_actor, outcomes{ok,changed,refused,suppressed}, last_at}` (minute precision) — owner only |
| `reconcile(store, source, *, now)` | nightly: every traced tool call in the last 24 h has a row, else emit `AuditReconcileMisses` and raise `ReconciliationFailed` |

`message_ref` must be one of `TEMPLATE_IDS` (`"<REASON_CODE>.sms"`, from `tower_policy.TEMPLATES`).
Alerts rows use `actor_user_id=SYSTEM_ALERTS` (`"system:alerts"`) with `trigger` `event` or `poll`.

### Chain

- `prev_hash` = SHA-256 of the previous row's canonical JSON (sorted keys, no whitespace, RFC 3339 Z with
  microseconds, integers only), written with the letters `a`-`p` instead of hex digits so no hash can look
  like a phone number. A line's first row has `"genesis"`. `policy_version` is re-lettered the same way.
- A head item per line (SK `~head`) holds the newest row's `ts#seq` and hash; `append` moves it in the same
  transaction as the row put, so concurrent writers (Tower and Alerts on the same line) cannot fork the chain,
  and `verify` catches an altered or deleted newest row.
- The trim marker is signed with HMAC-SHA256 (local: `TOWER_LINE_ID_KEY`, domain-separated; AWS: KMS
  `GenerateMac` on `TOWER_KMS_HMAC_KEY_ID`) — `signer_from_env()`.

### Trace sources (reconcile)

`TraceSource.tool_calls(since, until) -> Iterable[TracedToolCall(trace_id, ts, tool, line_id | None)]`.
`JsonlTraceSource(path)` for local (one JSON object per line). `ObservabilityTraceSource(log_group="aws/spans")`
on AWS: one CloudWatch Logs Insights query over AgentCore Observability's span records. It keeps Tower's tool
spans (`gen_ai.tool.name` from FastMCP; `tower.line_id`, which Tower sets once consent is resolved) and drops
Tower's own outbound Gateway client spans.

### Nightly job on AWS (prompt 13)

`tower_audit.aws.reconcile_handler(event, context)` is the reconcile Lambda (alerts image,
`python -m awslambdaric`). It trims every audited line at `trim_horizon(now)` with the KMS signer, then reconciles
the last 24 h. `AuditReconcileMisses` is written as EMF (`EmfMetricSink`, dimension `component=audit` only), and
a miss fails the invocation. `now` comes from the event, never a clock: Scheduler sends
`{"scheduled_time": "<aws.scheduler.scheduled-time>"}`, and a manual run sends `{"now": "<RFC 3339>"}`.

## Config

| Variable | Used by |
|---|---|
| `TOWER_DYNAMODB_ENDPOINT`, `AWS_REGION`, `TOWER_TABLE_PREFIX` | `tower_consent.Store.from_env()` |
| `TOWER_ENV` (`local` default), `TOWER_LINE_ID_KEY` (base64) | `signer_from_env()` locally |
| `TOWER_KMS_HMAC_KEY_ID` | `signer_from_env()` on AWS |

## Tests

```
uv run pytest packages/tower-audit -q            # moto and DynamoDB Local (testcontainers) when Docker runs
TOWER_DDB_BACKENDS=moto uv run pytest packages/tower-audit -q
```

## Showcase

`make showcase-audit` (or `uv run python packages/tower-audit/scripts/showcase.py --env local`): seeds a week on
Mom's line, prints her log, `recent_checks`, a canonical row and `verify`, then tampers one row and shows
`verify` naming it. Uses DynamoDB Local if `TOWER_DYNAMODB_ENDPOINT` is set, otherwise in-process moto.

## What it doesn't do

No deletion (TTL does that). No reading by anyone but the line's owner. No phrasing: `recent_checks` returns
counts; Tower's templates say them.
