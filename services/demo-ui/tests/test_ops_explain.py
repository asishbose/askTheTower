"""Every Ops target has its hover card (demo_ui.ops.explain): plain text, no secret, no number."""

from __future__ import annotations

import pytest
from demo_ui.ops.allowlist import TARGETS
from demo_ui.ops.explain import EXPLAIN, explain

pytestmark = pytest.mark.unit


def test_every_target_says_what_it_does() -> None:
    names = {t.name for t in TARGETS}
    assert names <= set(EXPLAIN), sorted(names - set(EXPLAIN))
    assert set(EXPLAIN) <= names, sorted(set(EXPLAIN) - names)  # no card for a target that no longer exists
    for name in names:
        lines = explain(name)
        assert lines and all(isinstance(x, str) and x.strip() for x in lines), name


def test_cards_carry_no_secret_or_number() -> None:
    import re

    text = " ".join(x for lines in EXPLAIN.values() for x in lines)
    assert not re.search(r"\d{7,}", text)  # no account id, no phone number
    assert "Bearer " not in text and "PASSWORD=" not in text
