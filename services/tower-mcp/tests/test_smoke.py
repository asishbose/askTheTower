import pytest

pytestmark = pytest.mark.unit


def test_imports() -> None:
    import tower_mcp  # noqa: F401
