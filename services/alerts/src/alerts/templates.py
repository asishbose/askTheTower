"""SMS bodies. Every alert body is `tower_policy.phrase(form="sms")` — the same templates Tower speaks (03 §4).

The one addition is `ACK_IGNORED_NOTE`, appended to the watcher's next message after a reply from a
swapped line was ignored (06 §3). `ACK_IGNORED_SWAPPED_LINE` is an audit-only code with no template in
tower_policy, and 06 §3 requires the watcher to be told; the sentence lives here (docs: 06 §3).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from tower_policy import Facts, Outcome, ReasonCode, phrase

ACK_IGNORED_NOTE = "A reply from the affected line was ignored."


def render(codes: Sequence[ReasonCode], facts: Facts, *, alias: str | None, tz: str, now: datetime) -> str:
    body: str = phrase(
        Outcome(kind="changed", reason_codes=list(codes)), facts, alias=alias, tz=tz, form="sms", now=now
    )
    return body


def message_ref(codes: Sequence[ReasonCode]) -> str:
    """The audit `message_ref` — a tower_audit TEMPLATE_IDS value, never a body."""
    return f"{codes[0].value}.sms"
