"""Nightly reconciliation (07 §1, 10 §3 `cron(0 3 * * ? *)`): every traced tool call has an audit row.

For each tool call in the window (default: the 24 h before `now`) the trace source reports, find an audit row
on the same `line_id` with the same `tool` and a ts within `tolerance` of the call; each row matches at most
one call. Any unmatched call is a miss: the `AuditReconcileMisses` metric is emitted (always, 0 included) and
`ReconciliationFailed` is raised.

Calls without a `line_id` (refused before a line was resolved — `NOT_BOUND`) have no line to audit on; they are
counted as `unattributable`, not as misses.

Trace sources are pluggable:

- `JsonlTraceSource(path)` — local: one JSON object per line,
  `{"trace_id": str, "ts": RFC 3339, "tool": str, "line_id": str | null}`.
- `ObservabilityTraceSource` — AgentCore Observability: a CloudWatch Logs Insights query over the Runtime's span
  records (`aws/spans`), wired by prompt 13 (`tower_audit.aws.reconcile_handler`).

Metrics carry no per-line dimension (10 §2: per-`line_id` labels are forbidden on metrics).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from tower_consent import Store

from tower_audit.errors import ReconciliationFailed
from tower_audit.reader import query_rows

log = logging.getLogger("tower_audit.reconcile")

WINDOW = timedelta(hours=24)
TOLERANCE = timedelta(seconds=30)
METRIC = "AuditReconcileMisses"


@dataclass(frozen=True)
class TracedToolCall:
    trace_id: str
    ts: datetime
    tool: str
    line_id: str | None


@runtime_checkable
class TraceSource(Protocol):
    def tool_calls(self, since: datetime, until: datetime) -> Iterable[TracedToolCall]:
        """Every tool call that started in [since, until)."""
        ...


@runtime_checkable
class MetricSink(Protocol):
    def emit(self, name: str, value: int, dimensions: dict[str, str]) -> None: ...


class LogMetricSink:
    """Writes the metric as one structured log line (CloudWatch EMF-shaped JSON under `TOWER_ENV=aws`)."""

    def emit(self, name: str, value: int, dimensions: dict[str, str]) -> None:
        log.info(json.dumps({"metric": name, "value": value, **dimensions}, sort_keys=True))


class JsonlTraceSource:
    """Local trace source: a JSONL file of tool calls (written by Tower in local mode, or by a test)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def tool_calls(self, since: datetime, until: datetime) -> Iterator[TracedToolCall]:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as fh:
            for raw in fh:
                if not raw.strip():
                    continue
                obj: dict[str, Any] = json.loads(raw)
                ts = datetime.fromisoformat(str(obj["ts"]).replace("Z", "+00:00"))
                if since <= ts < until:
                    yield TracedToolCall(
                        trace_id=str(obj["trace_id"]),
                        ts=ts,
                        tool=str(obj["tool"]),
                        line_id=obj.get("line_id") or None,
                    )


TOWER_TOOLS: frozenset[str] = frozenset({"line_is_ok", "is_reachable", "watch_line"})

# CloudWatch Logs Insights over AgentCore Observability's span records (Transaction Search writes one JSON span per
# log event to `aws/spans`). FastMCP's server span for a tool call carries `gen_ai.tool.name`; Tower adds
# `tower.line_id` (the HMAC id, never a number) once consent is resolved. Client spans for Tower's own outbound
# Gateway calls carry Gateway tool names and are dropped by the tool filter below.
SPAN_QUERY = (
    "fields traceId, startTimeUnixNano, @timestamp, "
    "coalesce(`attributes.gen_ai.tool.name`, `attributes.tool.name`) as tool, "
    "`attributes.tower.line_id` as line_id "
    "| filter ispresent(tool) "
    "| sort @timestamp asc "
    "| limit 10000"
)


class ObservabilityTraceSource:
    """AgentCore Observability (CloudWatch Transaction Search over the Tower runtime's spans).

    `tool_calls` runs one Logs Insights query over `log_group` (default `aws/spans`) for [since, until) and
    yields Tower's tool spans: `tool` from `gen_ai.tool.name` (FastMCP) or `tool.name`, `line_id` from
    `tower.line_id`. `logs_client` is a boto3 CloudWatch Logs client (created on first use if omitted);
    `sleep` is injectable so tests do not wait. A query that does not complete within `timeout_s` raises
    `TimeoutError` — the nightly Lambda then fails loudly rather than reporting zero misses.
    """

    def __init__(
        self,
        log_group: str = "aws/spans",
        *,
        logs_client: Any = None,
        tools: frozenset[str] = TOWER_TOOLS,
        poll_s: float = 1.0,
        timeout_s: float = 120.0,
        sleep: Any = None,
    ) -> None:
        self.log_group = log_group
        self.logs_client = logs_client
        self.tools = tools
        self.poll_s = poll_s
        self.timeout_s = timeout_s
        self._sleep = sleep

    def _client(self) -> Any:
        if self.logs_client is None:
            import boto3

            self.logs_client = boto3.client("logs")
        return self.logs_client

    def _rows(self, since: datetime, until: datetime) -> list[dict[str, str]]:
        import time

        sleep = self._sleep or time.sleep
        logs = self._client()
        query_id = logs.start_query(
            logGroupNames=[self.log_group],
            startTime=int(since.timestamp()),
            endTime=int(until.timestamp()) + 1,  # second granularity; the exact bound is applied below
            queryString=SPAN_QUERY,
            limit=10000,
        )["queryId"]
        waited = 0.0
        while True:
            resp = logs.get_query_results(queryId=query_id)
            status = resp.get("status")
            if status == "Complete":
                return [{f["field"]: f.get("value", "") for f in row} for row in resp.get("results", [])]
            if status in ("Failed", "Cancelled", "Timeout", "Unknown"):
                raise RuntimeError(f"trace query {status}")
            if waited >= self.timeout_s:
                raise TimeoutError("trace query did not complete")
            sleep(self.poll_s)
            waited += self.poll_s

    def tool_calls(self, since: datetime, until: datetime) -> Iterator[TracedToolCall]:
        for row in self._rows(since, until):
            tool = row.get("tool") or ""
            if tool not in self.tools:
                continue
            ts = _span_time(row)
            if ts is None or not (since <= ts < until):
                continue
            yield TracedToolCall(
                trace_id=row.get("traceId") or "",
                ts=ts,
                tool=tool,
                line_id=row.get("line_id") or None,
            )


def _span_time(row: dict[str, str]) -> datetime | None:
    nanos = row.get("startTimeUnixNano")
    if nanos and nanos.isdigit():
        return datetime.fromtimestamp(int(nanos) / 1e9, tz=UTC)
    stamp = row.get("@timestamp")
    if stamp:  # "2026-10-06 03:00:00.123" (UTC)
        return datetime.fromisoformat(stamp.replace(" ", "T")).replace(tzinfo=UTC)
    return None


class EmfMetricSink:
    """CloudWatch embedded metric format on stdout (Lambda ships it to CloudWatch as a metric). `at` is the
    metric timestamp (the run's `now`; packages read no clock). No per-line dimension is accepted (10 §2)."""

    def __init__(self, at: datetime, namespace: str = "AskTheTower", *, write: Any = None) -> None:
        self.at = at
        self.namespace = namespace
        self._write = write or print

    def emit(self, name: str, value: int, dimensions: dict[str, str]) -> None:
        if any("line" in k.lower() for k in dimensions):
            raise ValueError("per-line metric dimensions are forbidden (10 §2)")
        doc: dict[str, Any] = {
            "_aws": {
                "Timestamp": int(self.at.timestamp() * 1000),
                "CloudWatchMetrics": [
                    {
                        "Namespace": self.namespace,
                        "Dimensions": [sorted(dimensions)],
                        "Metrics": [{"Name": name, "Unit": "Count"}],
                    }
                ],
            },
            name: value,
            **dimensions,
        }
        self._write(json.dumps(doc, sort_keys=True))


@dataclass
class ReconcileReport:
    since: datetime
    until: datetime
    checked: int = 0
    matched: int = 0
    unattributable: int = 0
    misses: list[str] = field(default_factory=list)  # trace ids


def reconcile(
    store: Store,
    source: TraceSource,
    *,
    now: datetime,
    window: timedelta = WINDOW,
    tolerance: timedelta = TOLERANCE,
    metrics: MetricSink | None = None,
) -> ReconcileReport:
    """Raise `ReconciliationFailed` if any traced tool call in [now - window, now) has no audit row."""
    since, until = now - window, now
    report = ReconcileReport(since=since, until=until)
    by_line: dict[str, list[TracedToolCall]] = {}
    for call in source.tool_calls(since, until):
        report.checked += 1
        if call.line_id is None:
            report.unattributable += 1
            continue
        by_line.setdefault(call.line_id, []).append(call)

    for line_id, calls in by_line.items():
        rows = query_rows(store, line_id, since=since - tolerance, until=until + tolerance)
        used: set[str] = set()
        for call in sorted(calls, key=lambda c: c.ts):
            match = next(
                (
                    r
                    for r in rows
                    if r.ts_seq not in used and r.tool == call.tool and abs(r.ts - call.ts) <= tolerance
                ),
                None,
            )
            if match is None:
                report.misses.append(call.trace_id)
            else:
                used.add(match.ts_seq)
                report.matched += 1

    (metrics or LogMetricSink()).emit(METRIC, len(report.misses), {"component": "audit"})
    if report.misses:
        raise ReconciliationFailed(report)
    return report
