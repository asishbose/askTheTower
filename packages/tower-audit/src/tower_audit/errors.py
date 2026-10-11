"""Errors raised by tower-audit. Messages never carry a phone number, a token, a key or a row's content."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tower_audit.reconcile import ReconcileReport


class AuditError(Exception):
    """Base class for every tower-audit error."""


class AuditWriteFailed(AuditError):
    """The audit row was not written. The caller must **not** release the response or alert (07 §1, 02 §6).

    `append` never swallows this; callers decide, and the rule is: refuse (SERVICE_UNAVAILABLE).
    """


class AuditAccessDenied(AuditError):
    """The viewer does not own the line (or the line does not exist). One error for both on purpose (07 §4)."""


class ReconciliationFailed(AuditError):
    """At least one traced tool call in the window has no audit row (07 §1: "a bug and an alarm")."""

    def __init__(self, report: ReconcileReport) -> None:
        super().__init__(f"{len(report.misses)} traced tool call(s) without an audit row")
        self.report = report
