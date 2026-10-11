"""tests/aws: the AWS column (prompt 13).

Two kinds of test live here:

- **Offline** (`unit`/`integration`): Terraform shape and guardrails (tables current, no per-line metric labels,
  no inline secrets, `terraform fmt`/`validate`), and the KMS wiring against moto. These run everywhere.
- **Live** (`nightly`): conformance through the deployed Gateway, the latency run, the backend swap. They need AWS
  credentials *and* a deployment (`make deploy` → `artifacts/tf-outputs.json`), and skip with a reason naming
  what is missing. Nothing here creates or changes AWS resources unless `ATT_ALLOW_APPLY=1` is set (swap test).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
TF = ROOT / "deploy" / "terraform"
OUTPUTS = ROOT / "artifacts" / "tf-outputs.json"
FAKE_KEYS = {"testing", "test", "foobar_key", "AKIAIOSFODNN7EXAMPLE"}


def aws_credentials_reason() -> str | None:
    """None when real-looking AWS credentials resolve; else why not (moto's fake keys don't count)."""
    try:
        import botocore.session

        creds = botocore.session.get_session().get_credentials()
    except Exception as e:  # noqa: BLE001
        return f"no AWS credentials ({type(e).__name__})"
    if creds is None:
        return "no AWS credentials (botocore found none)"
    if creds.access_key in FAKE_KEYS or os.environ.get("AWS_ACCESS_KEY_ID") in FAKE_KEYS:
        return "no AWS credentials (only moto's fake test keys are set)"
    return None


def deployment_reason() -> str | None:
    if not OUTPUTS.exists():
        return "no AWS deployment: artifacts/tf-outputs.json missing (run `make deploy ENV=aws`, then `make outputs`)"
    return None


@pytest.fixture(scope="session")
def aws_outputs() -> dict[str, Any]:
    """Terraform outputs of a live deployment, flattened to {name: value}; skips when there is none."""
    reason = aws_credentials_reason() or deployment_reason()
    if reason:
        pytest.skip(reason)
    raw = json.loads(OUTPUTS.read_text("utf-8"))
    return {k: (v.get("value") if isinstance(v, dict) and "value" in v else v) for k, v in raw.items()}
