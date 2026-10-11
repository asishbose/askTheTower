"""AWS entry point for the nightly audit job (10 §2-3: `cron(0 3 * * ? *)` -> reconcile Lambda).

`reconcile_handler(event, context)` runs, in order:

1. **trim** every audited line at `trim_horizon(now)` (07 §5: re-anchor a day ahead of the 90-day TTL), signing
   the marker with KMS (`signer_from_env`, `TOWER_KMS_HMAC_KEY_ID`);
2. **reconcile** the last `TOWER_RECONCILE_WINDOW_H` hours (default 24) of Tower tool spans from AgentCore
   Observability (`ObservabilityTraceSource(TOWER_TRACE_LOG_GROUP)`, default `aws/spans`) against the audit rows.
   The `AuditReconcileMisses` metric (dimension `component=audit` only) is written as EMF; a miss raises
   `ReconciliationFailed`, so the invocation fails and the Lambda error alarm fires.

`now` comes from the event, never from a clock (packages read no clock): EventBridge Scheduler passes
`{"scheduled_time": "<aws.scheduler.scheduled-time>"}`; a manual run passes `{"now": "<RFC 3339>"}`.
The image is the alerts image (tower_audit ships in it); Lambda enters through `python -m awslambdaric`.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator, Mapping
from datetime import datetime, timedelta
from typing import Any

from tower_consent import Store
from tower_consent import tables as T

from tower_audit.chain import HEAD_SK, MarkerSigner, signer_from_env
from tower_audit.reconcile import EmfMetricSink, MetricSink, ObservabilityTraceSource, TraceSource, reconcile
from tower_audit.trim import trim, trim_horizon

log = logging.getLogger("tower_audit.aws")


def event_time(event: Mapping[str, Any]) -> datetime:
    """The run's `now`: `scheduled_time` (Scheduler) or `now` (manual), timezone-aware."""
    raw = event.get("scheduled_time") or event.get("now") or event.get("time")
    if not raw or not isinstance(raw, str) or raw.startswith("<"):
        raise ValueError("event needs scheduled_time or now (RFC 3339)")
    ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        raise ValueError("event time must carry a timezone")
    return ts


def audited_lines(store: Store) -> Iterator[str]:
    """Every line with an audit chain: one item per line, the `~head` item (07 build note). A Scan with a
    filter — nightly, never on a request path."""
    kwargs: dict[str, Any] = {
        "TableName": store.name(T.AUDIT),
        "FilterExpression": "ts_seq = :h",
        "ExpressionAttributeValues": {":h": {"S": HEAD_SK}},
        "ProjectionExpression": "line_id",
    }
    while True:
        resp = store.client.scan(**kwargs)
        for item in resp.get("Items", []):
            yield item["line_id"]["S"]
        if "LastEvaluatedKey" not in resp:
            return
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def run(
    store: Store,
    source: TraceSource,
    signer: MarkerSigner,
    *,
    now: datetime,
    window: timedelta = timedelta(hours=24),
    metrics: MetricSink | None = None,
) -> dict[str, Any]:
    before = trim_horizon(now)
    trimmed = sum(
        1 for line_id in audited_lines(store) if trim(store, line_id, before, signer=signer, now=now)
    )
    report = reconcile(store, source, now=now, window=window, metrics=metrics)
    summary = {
        "since": report.since.isoformat(),
        "until": report.until.isoformat(),
        "checked": report.checked,
        "matched": report.matched,
        "unattributable": report.unattributable,
        "misses": len(report.misses),
        "trimmed_lines": trimmed,
    }
    log.info("audit nightly: %s", summary)
    return summary


def reconcile_handler(event: Mapping[str, Any], context: Any = None) -> dict[str, Any]:
    """Lambda handler (`tower_audit.aws.reconcile_handler`)."""
    env = os.environ
    now = event_time(event)
    return run(
        Store.from_env(env),
        ObservabilityTraceSource(env.get("TOWER_TRACE_LOG_GROUP", "aws/spans")),
        signer_from_env(env),
        now=now,
        window=timedelta(hours=float(env.get("TOWER_RECONCILE_WINDOW_H", "24"))),
        metrics=EmfMetricSink(now, env.get("TOWER_METRICS_NAMESPACE", "AskTheTower")),
    )
