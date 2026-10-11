import pytest

pytestmark = pytest.mark.unit


def test_imports() -> None:
    import ref_client  # noqa: F401
