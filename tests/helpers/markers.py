"""The four layer markers (testing-and-showcase §3). Every collected test carries exactly one; the root
conftest turns a violation into a collection error, so an unmarked test cannot slip into no layer."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

LAYERS: tuple[str, ...] = ("unit", "integration", "e2e", "nightly")


class _Item(Protocol):
    nodeid: str

    def iter_markers(self) -> Iterable[object]: ...


def layers_of(item: _Item) -> list[str]:
    return sorted({m.name for m in item.iter_markers() if getattr(m, "name", None) in LAYERS})  # type: ignore[attr-defined]


def layer_violations(items: Iterable[_Item]) -> list[str]:
    """`nodeid: <problem>` for every test with zero or several layer markers."""
    bad = []
    for item in items:
        found = layers_of(item)
        if len(found) != 1:
            what = "no layer marker" if not found else f"several layer markers {found}"
            bad.append(f"{item.nodeid}: {what} (needs exactly one of {', '.join(LAYERS)})")
    return bad
