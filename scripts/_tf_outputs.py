"""Shared by the AWS scripts: read `terraform output -json` (artifacts/tf-outputs.json, written by `make outputs`)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = {"aws": ROOT / "artifacts" / "tf-outputs.json", "eks": ROOT / "artifacts" / "tf-outputs-eks.json"}


def load_outputs(path: Path | None = None, *, env: str = "aws") -> dict[str, Any]:
    """{name: value} from a `terraform output -json` file. Raises SystemExit with the command to run if absent."""
    p = path or OUTPUTS[env]
    if not p.exists():
        raise SystemExit(f"{p} not found: run `make deploy ENV={env}` (or `make outputs ENV={env}`)")
    raw = json.loads(p.read_text("utf-8"))
    return {k: (v["value"] if isinstance(v, dict) and "value" in v else v) for k, v in raw.items()}
