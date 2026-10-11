"""Hash-chained audit log: append-before-release, verify, trim, recent_checks (docs/architecture/components/07).

Write path (Tower 08, Alerts 10): build an `AuditRecord`, then `append(store, record)` **before** releasing the
answer or alert — or `release(store, record, respond)`. `AuditWriteFailed` means: refuse.
"""

from tower_audit.chain import (
    HmacMarkerSigner,
    KmsMarkerSigner,
    MarkerSigner,
    row_hash,
    signer_from_env,
)
from tower_audit.errors import AuditAccessDenied, AuditError, AuditWriteFailed, ReconciliationFailed
from tower_audit.reader import RecentChecks, list_for_line, recent_checks
from tower_audit.reconcile import (
    EmfMetricSink,
    JsonlTraceSource,
    ObservabilityTraceSource,
    ReconcileReport,
    TracedToolCall,
    TraceSource,
    reconcile,
)
from tower_audit.record import (
    GENESIS,
    RETENTION,
    SYSTEM_ALERTS,
    TEMPLATE_IDS,
    AuditOutcome,
    AuditRecord,
    Source,
    Tool,
    Trigger,
    audit_outcome,
)
from tower_audit.trim import TrimResult, trim, trim_horizon
from tower_audit.verify import VerifyResult, verify
from tower_audit.writer import append, release

__all__ = [
    "GENESIS",
    "RETENTION",
    "SYSTEM_ALERTS",
    "TEMPLATE_IDS",
    "AuditAccessDenied",
    "AuditError",
    "AuditOutcome",
    "AuditRecord",
    "AuditWriteFailed",
    "EmfMetricSink",
    "HmacMarkerSigner",
    "JsonlTraceSource",
    "KmsMarkerSigner",
    "MarkerSigner",
    "ObservabilityTraceSource",
    "RecentChecks",
    "ReconcileReport",
    "ReconciliationFailed",
    "Source",
    "Tool",
    "TraceSource",
    "TracedToolCall",
    "Trigger",
    "TrimResult",
    "VerifyResult",
    "append",
    "audit_outcome",
    "list_for_line",
    "recent_checks",
    "reconcile",
    "release",
    "row_hash",
    "signer_from_env",
    "trim",
    "trim_horizon",
    "verify",
]
