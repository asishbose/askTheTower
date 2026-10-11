"""Reconciliation (07 §1, §6): delete a row behind a trace → the nightly job raises and emits the metric."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from tower_audit import (
    JsonlTraceSource,
    ObservabilityTraceSource,
    ReconciliationFailed,
    TraceSource,
    append,
    reconcile,
)
from tower_consent import Store
from tower_consent import tables as T

from .conftest import NOW, RecordFactory

pytestmark = pytest.mark.integration

RUN_AT = NOW + timedelta(hours=12)


class Metrics:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, int, dict[str, str]]] = []

    def emit(self, name: str, value: int, dimensions: dict[str, str]) -> None:
        self.emitted.append((name, value, dimensions))


def write_traces(path: Path, calls: list[dict[str, Any]]) -> JsonlTraceSource:
    path.write_text("".join(json.dumps(c) + "\n" for c in calls), encoding="utf-8")
    return JsonlTraceSource(path)


def seed(store: Store, make_record: RecordFactory, tmp_path: Path) -> tuple[list[Any], JsonlTraceSource]:
    rows = [
        append(store, make_record(i * 30, tool="line_is_ok" if i % 2 else "is_reachable")) for i in range(4)
    ]
    calls = [
        {
            "trace_id": f"tr-{i}",
            "ts": (r.ts - timedelta(milliseconds=120)).isoformat().replace("+00:00", "Z"),
            "tool": r.tool,
            "line_id": r.line_id,
        }
        for i, r in enumerate(rows)
    ]

    # Relative to NOW (wall-clock, for DynamoDB Local's TTL sweeper): a hard-coded date drifted out of the window.
    def at(delta: timedelta) -> str:
        return (NOW + delta).isoformat().replace("+00:00", "Z")

    calls.append({"trace_id": "tr-nb", "ts": at(timedelta(hours=1)), "tool": "line_is_ok", "line_id": None})
    calls.append(
        {"trace_id": "tr-old", "ts": at(timedelta(days=-5)), "tool": "line_is_ok", "line_id": rows[0].line_id}
    )  # outside the 24 h window
    return rows, write_traces(tmp_path / "traces.jsonl", calls)


def test_all_traces_have_rows(store: Store, make_record: RecordFactory, tmp_path: Path) -> None:
    _, source = seed(store, make_record, tmp_path)
    m = Metrics()
    report = reconcile(store, source, now=RUN_AT, metrics=m)
    assert (report.checked, report.matched, report.unattributable, report.misses) == (5, 4, 1, [])
    assert m.emitted == [("AuditReconcileMisses", 0, {"component": "audit"})]


def test_deleted_row_behind_a_trace_raises(store: Store, make_record: RecordFactory, tmp_path: Path) -> None:
    rows, source = seed(store, make_record, tmp_path)
    store.delete(T.AUDIT, {"line_id": rows[2].line_id, "ts_seq": rows[2].ts_seq})
    m = Metrics()
    with pytest.raises(ReconciliationFailed) as exc:
        reconcile(store, source, now=RUN_AT, metrics=m)
    assert exc.value.report.misses == ["tr-2"]
    assert m.emitted == [("AuditReconcileMisses", 1, {"component": "audit"})]
    assert all("ln_" not in v for _, _, d in m.emitted for v in d.values())  # no per-line labels


def test_wrong_tool_row_does_not_count(store: Store, make_record: RecordFactory, tmp_path: Path) -> None:
    row = append(store, make_record(0, tool="watch_line", source=None))
    source = write_traces(
        tmp_path / "t.jsonl",
        [{"trace_id": "tr-x", "ts": row.ts.isoformat(), "tool": "line_is_ok", "line_id": row.line_id}],
    )
    with pytest.raises(ReconciliationFailed):
        reconcile(store, source, now=RUN_AT, metrics=Metrics())


def test_one_row_matches_one_call(store: Store, make_record: RecordFactory, tmp_path: Path) -> None:
    row = append(store, make_record(0))
    call = {"ts": row.ts.isoformat(), "tool": "line_is_ok", "line_id": row.line_id}
    source = write_traces(tmp_path / "t.jsonl", [{**call, "trace_id": "a"}, {**call, "trace_id": "b"}])
    with pytest.raises(ReconciliationFailed) as exc:
        reconcile(store, source, now=RUN_AT, metrics=Metrics())
    assert len(exc.value.report.misses) == 1


def test_missing_trace_file_is_empty(store: Store, tmp_path: Path) -> None:
    report = reconcile(store, JsonlTraceSource(tmp_path / "none.jsonl"), now=RUN_AT, metrics=Metrics())
    assert report.checked == 0


class FakeLogs:
    """CloudWatch Logs Insights stand-in: one query, `Running` once, then the rows."""

    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.rows = rows
        self.started: list[dict[str, Any]] = []
        self.polls = 0

    def start_query(self, **kwargs: Any) -> dict[str, str]:
        self.started.append(kwargs)
        return {"queryId": "q-1"}

    def get_query_results(self, queryId: str) -> dict[str, Any]:  # noqa: N803 - boto3 casing
        self.polls += 1
        if self.polls == 1:
            return {"status": "Running", "results": []}
        return {
            "status": "Complete",
            "results": [[{"field": k, "value": v} for k, v in r.items()] for r in self.rows],
        }


def _span(trace_id: str, ts: Any, tool: str, line_id: str | None) -> dict[str, str]:
    row = {"traceId": trace_id, "startTimeUnixNano": str(int(ts.timestamp() * 1e9)), "tool": tool}
    if line_id:
        row["line_id"] = line_id
    return row


def test_observability_source_reads_tower_tool_spans() -> None:
    """Prompt 13 replaced the stub: one Logs Insights query over the span log group."""
    line = "ln_" + "a" * 64
    logs = FakeLogs(
        [
            _span("t1", NOW, "line_is_ok", line),
            _span("t2", NOW + timedelta(seconds=5), "is_reachable", None),  # refused before resolve
            _span("t3", NOW, "sim-swap___checkSimSwap", None),  # Tower's own outbound Gateway client span
            _span("t4", RUN_AT + timedelta(minutes=1), "line_is_ok", line),  # outside [since, until)
        ]
    )
    slept: list[float] = []
    src = ObservabilityTraceSource("aws/spans", logs_client=logs, sleep=slept.append)
    assert isinstance(src, TraceSource)
    calls = list(src.tool_calls(NOW - timedelta(hours=1), RUN_AT))
    assert [(c.trace_id, c.tool, c.line_id) for c in calls] == [
        ("t1", "line_is_ok", line),
        ("t2", "is_reachable", None),
    ]
    assert calls[0].ts == NOW
    q = logs.started[0]
    assert q["logGroupNames"] == ["aws/spans"] and "gen_ai.tool.name" in q["queryString"]
    assert "tower.line_id" in q["queryString"] and slept == [1.0]


def test_observability_source_fails_loudly() -> None:
    class Failing(FakeLogs):
        def get_query_results(self, queryId: str) -> dict[str, Any]:  # noqa: N803
            return {"status": "Failed"}

    with pytest.raises(RuntimeError):
        list(ObservabilityTraceSource(logs_client=Failing([]), sleep=lambda _s: None).tool_calls(NOW, RUN_AT))

    class Slow(FakeLogs):
        def get_query_results(self, queryId: str) -> dict[str, Any]:  # noqa: N803
            return {"status": "Running"}

    src = ObservabilityTraceSource(logs_client=Slow([]), sleep=lambda _s: None, timeout_s=3)
    with pytest.raises(TimeoutError):
        list(src.tool_calls(NOW, RUN_AT))


def test_reconcile_over_observability_spans(store: Store, make_record: RecordFactory) -> None:
    rows = [append(store, make_record(i * 30)) for i in range(2)]
    logs = FakeLogs(
        [_span(f"s{i}", r.ts - timedelta(milliseconds=80), r.tool, r.line_id) for i, r in enumerate(rows)]
    )
    src = ObservabilityTraceSource(logs_client=logs, sleep=lambda _s: None)
    report = reconcile(store, src, now=RUN_AT, metrics=Metrics())
    assert (report.checked, report.matched, report.misses) == (2, 2, [])
