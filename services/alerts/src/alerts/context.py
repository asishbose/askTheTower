"""The service's dependencies in one object, plus the audit and log helpers every module shares.

Nothing here decides anything. `AlertsService` is built once per process (`build_service` in `runner.py`)
or by a test with fakes.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from camara_client import CarrierClient
from tower_audit import SYSTEM_ALERTS, AuditOutcome, AuditRecord, Trigger, append
from tower_consent import LineIdHasher, MsisdnCipher, Store
from tower_policy import ReasonCode, Thresholds, default_thresholds, policy_version

from alerts.clock import Clock
from alerts.config import Settings
from alerts.send_backends import SmsSender
from alerts.sent_log import SentLog

log = logging.getLogger("alerts")


@dataclass
class AlertsService:
    store: Store
    hasher: LineIdHasher
    cipher: MsisdnCipher
    carrier: CarrierClient
    sender: SmsSender
    clock: Clock
    settings: Settings = field(default_factory=Settings)
    thresholds: Thresholds = field(default_factory=default_thresholds)
    metrics: Counter[str] = field(default_factory=Counter)
    sent_log: SentLog | None = None  # ALERTS_MODE=local only: GET /internal/sent (06 §3.1)

    def count(self, name: str, n: int = 1) -> None:
        """Process-local counters (no per-line labels, 10 §2)."""
        self.metrics[name] += n


def audit(
    svc: AlertsService,
    line_id: str,
    now: datetime,
    *,
    trigger: Trigger,
    outcome: AuditOutcome,
    codes: Sequence[ReasonCode],
    message_ref: str | None = None,
) -> AuditRecord:
    """Append one `tool="alert"` row as `system:alerts` — before anything is released (e2e §8.5)."""
    row = append(
        svc.store,
        AuditRecord(
            line_id=line_id,
            ts=now,
            actor_user_id=SYSTEM_ALERTS,
            tool="alert",
            trigger=trigger,
            outcome=outcome,
            reason_codes=tuple(codes),
            message_ref=message_ref,
            policy_version=policy_version(),
        ),
    )
    svc.count(f"audit_{outcome}")
    log.info(
        "audit line=%s trigger=%s outcome=%s codes=%s ref=%s",
        line_id[:12],
        trigger,
        outcome,
        ",".join(c.value for c in codes),
        message_ref or "-",
    )
    return row
