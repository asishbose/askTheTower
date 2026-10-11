"""The layer rule the root conftest enforces at collection: exactly one of unit / integration / e2e / nightly."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from tests.helpers.markers import LAYERS, layer_violations, layers_of

pytestmark = pytest.mark.unit


@dataclass
class _Mark:
    name: str


@dataclass
class _Item:
    nodeid: str
    marks: list[str] = field(default_factory=list)

    def iter_markers(self) -> list[_Mark]:
        return [_Mark(m) for m in self.marks]


def test_layers_are_the_four_words_the_readme_uses() -> None:
    assert LAYERS == ("unit", "integration", "e2e", "nightly")


def test_exactly_one_layer_passes() -> None:
    for layer in LAYERS:
        assert layer_violations([_Item(f"t::{layer}", [layer, "slow", "parametrize"])]) == []


def test_unmarked_and_double_marked_are_reported() -> None:
    bad = layer_violations(
        [_Item("a::none", ["slow"]), _Item("b::two", ["unit", "integration"]), _Item("c::ok", ["e2e"])]
    )
    assert len(bad) == 2
    assert bad[0].startswith("a::none: no layer marker")
    assert bad[1].startswith("b::two: several layer markers ['integration', 'unit']")


def test_module_and_function_marks_collapse() -> None:
    # pytestmark at module level + the same mark on the function is still one layer
    assert layers_of(_Item("x", ["unit", "unit"])) == ["unit"]


def test_the_ini_declares_every_layer(pytestconfig: pytest.Config) -> None:
    declared = {line.split(":", 1)[0].strip() for line in pytestconfig.getini("markers")}
    assert set(LAYERS) | {"slow"} <= declared
    assert "--strict-markers" in pytestconfig.getini("addopts")
