"""The nightly audit Lambda (`tower_audit.aws`, prompt 13): `now` from the event, trim every audited line, then
reconcile against the traces; the metric is EMF with no per-line dimension; a miss fails the invocation."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from tower_audit import EmfMetricSink, HmacMarkerSigner, JsonlTraceSource, ReconciliationFailed, append
from tower_audit.aws import audited_lines, event_time, run
from tower_consent import Store
from tower_consent import tables as T

from .conftest import NOW, RecordFactory


@pytest.mark.unit
def test_event_time_comes_from_the_scheduler() -> None:
    assert event_time({"scheduled_time": "2026-10-07T03:00:00Z"}) == datetime(2026, 10, 7, 3, tzinfo=UTC)
    assert event_time({"now": "2026-10-07T03:00:00+00:00"}).tzinfo is not None
    for bad in ({}, {"scheduled_time": "<aws.scheduler.scheduled-time>"}, {"now": "2026-10-07T03:00:00"}):
        with pytest.raises(ValueError):
            event_time(bad)


@pytest.mark.unit
def test_emf_metric_has_no_line_dimension() -> None:
    out: list[str] = []
    at = datetime(2026, 10, 7, 3, tzinfo=UTC)
    EmfMetricSink(at, write=out.append).emit("AuditReconcileMisses", 2, {"component": "audit"})
    doc = json.loads(out[0])
    meta = doc["_aws"]["CloudWatchMetrics"][0]
    assert meta["Namespace"] == "AskTheTower" and meta["Dimensions"] == [["component"]]
    assert doc["AuditReconcileMisses"] == 2 and doc["_aws"]["Timestamp"] == int(at.timestamp() * 1000)
    assert "line_id" not in out[0]
    with pytest.raises(ValueError):
        EmfMetricSink(at, write=out.append).emit("X", 1, {"line_id": "ln_x"})


@pytest.mark.integration
def test_audited_lines_lists_each_chain_once(store: Store, make_record: RecordFactory) -> None:
    for i in range(3):
        append(store, make_record(i * 10))
    assert list(audited_lines(store)) == [make_record(0).line_id]


@pytest.mark.integration
def test_nightly_run_trims_and_reconciles(
    store: Store, make_record: RecordFactory, signer: HmacMarkerSigner, tmp_path: Any
) -> None:
    # Older than the trim horizon (now - 89 d) but not yet past its 90-day TTL (DynamoDB Local sweeps TTL).
    old = append(store, make_record(-int(89.5 * 24 * 60)))
    fresh = append(store, make_record(0))
    traces = tmp_path / "t.jsonl"
    traces.write_text(
        json.dumps(
            {"trace_id": "a", "ts": fresh.ts.isoformat(), "tool": fresh.tool, "line_id": fresh.line_id}
        )
        + "\n"
    )
    out: list[str] = []
    now = NOW + timedelta(hours=1)
    summary = run(
        store, JsonlTraceSource(traces), signer, now=now, metrics=EmfMetricSink(now, write=out.append)
    )
    assert summary["misses"] == 0 and summary["matched"] == 1 and summary["trimmed_lines"] == 1
    assert json.loads(out[0])["AuditReconcileMisses"] == 0
    assert (
        store.get(T.AUDIT, {"line_id": old.line_id, "ts_seq": old.ts_seq}) is not None
    )  # TTL deletes, not trim
    anchor = store.get(T.AUDIT, {"line_id": fresh.line_id, "ts_seq": fresh.ts_seq})
    assert anchor is not None and str(anchor["prev_hash"]).startswith("trimmed|")

    store.delete(T.AUDIT, {"line_id": fresh.line_id, "ts_seq": fresh.ts_seq})
    with pytest.raises(ReconciliationFailed):
        run(store, JsonlTraceSource(traces), signer, now=now, metrics=EmfMetricSink(now, write=out.append))
