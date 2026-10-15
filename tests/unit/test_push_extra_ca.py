"""`make push` with EXTRA_CA_FILE: it becomes a build secret, never a build arg or a layer."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def _push():  # type: ignore[no-untyped-def]
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("push_images", ROOT / "scripts" / "push_images.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_unset_adds_nothing_and_a_file_becomes_a_secret(tmp_path: Path) -> None:
    push = _push()
    assert push.extra_ca_secret({}) == []
    ca = tmp_path / "ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\n")
    assert push.extra_ca_secret({"EXTRA_CA_FILE": str(ca)}) == ["--secret", f"id=extra_ca,src={ca}"]
    with pytest.raises(SystemExit, match="not a file"):
        push.extra_ca_secret({"EXTRA_CA_FILE": str(tmp_path / "missing.pem")})


def test_every_dockerfile_mounts_the_secret_for_its_install_only() -> None:
    for f in sorted((ROOT / "services").glob("*/Dockerfile")):
        text = f.read_text()
        assert "--mount=type=secret,id=extra_ca,required=false" in text, f
        assert "extra_ca" not in "".join(ln for ln in text.splitlines() if ln.startswith(("COPY", "ENV", "ARG"))), f
