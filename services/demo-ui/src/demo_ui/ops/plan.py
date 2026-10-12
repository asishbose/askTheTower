"""Plan summary chips and "is the last plan current?" (doc 11 §2 "Plan summary chips", T5).

The summary is the *last* match of terraform's own line in text with ANSI already stripped: `Plan: [I to import, ]
A to add, C to change, D to destroy.`, or `No changes.` (0/0/0). `make plan` writes `artifacts/terraform-plan.txt`
with `terraform show`; `make deploy` re-plans inside `apply`, so these chips describe the plan the presenter read,
and "current" (newer than the image tag and the tfvars) guards against a change in between.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

SUMMARY = re.compile(
    r"^(?:Plan: (?:(\d+) to import, )?(\d+) to add, (\d+) to change, (\d+) to destroy\.|No changes\.)", re.M
)


@dataclass(frozen=True)
class PlanSummary:
    add: int
    change: int
    destroy: int

    @property
    def line(self) -> str:
        """The transcript's plan line (T7)."""
        return f"== plan +{self.add} ~{self.change} -{self.destroy}"


def parse(text: str) -> PlanSummary | None:
    """The last summary in `text`, or None."""
    matches = list(SUMMARY.finditer(text))
    if not matches:
        return None
    last = matches[-1]
    if last.group(0) == "No changes.":
        return PlanSummary(0, 0, 0)
    return PlanSummary(int(last[2]), int(last[3]), int(last[4]))


def parse_lines(lines: Iterable[str]) -> PlanSummary | None:
    found = None
    for line in lines:
        found = parse(line) or found
    return found


def read(path: Path) -> PlanSummary | None:
    """The summary of a plan file; None when it is absent, unreadable or has none."""
    try:
        return parse(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None


def is_current(plan: Path, *inputs: Path) -> bool:
    """The plan file exists and is newer than every input that exists (`image-tag`, `aws.tfvars`)."""
    try:
        at = plan.stat().st_mtime
    except OSError:
        return False
    for path in inputs:
        try:
            if path.stat().st_mtime >= at:
                return False
        except OSError:
            continue
    return True


def deploy_needs_word(plan: Path) -> bool:
    """T5: `deploy` must be typed when the last plan destroys anything, or its summary cannot be read."""
    summary = read(plan)
    return summary is None or summary.destroy > 0
