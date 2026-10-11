"""Nightly chaos on AWS (testing-and-showcase §3, Chaos row: "Lambda cold start mid-window"): force a cold start
of every Alerts Lambda (a config change makes Lambda drop warm sandboxes), then, while they come back:

- Tower still answers the seeded line holder with the seeded outcome or a refusal (STALE_DATA / CARRIER_ERROR) —
  never a different outcome about the line;
- each Alerts poller invoked cold completes without a function error.

Live and opt-in: ENV=aws, AWS credentials, a deployment (`make deploy` → artifacts/tf-outputs.json),
TOWER_BEARER (a JWT Tower accepts for the seeded user) and ATT_ALLOW_APPLY=1 (it changes Lambda configuration).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import pytest

from tests.aws.conftest import OUTPUTS, aws_credentials_reason, deployment_reason
from tests.e2e.tower import call
from tests.helpers.env import Targets

pytestmark = pytest.mark.nightly

ACCEPTABLE = {"OK", "STALE_DATA", "CARRIER_ERROR"}  # the demo seed's line is fine; a refusal is allowed


@pytest.fixture(scope="module")
def aws(env: Targets) -> dict[str, Any]:
    reason = (
        (None if env.env == "aws" else f"ENV={env.env}: cold-start chaos runs only with ENV=aws")
        or aws_credentials_reason()
        or deployment_reason()
        or (None if env.bearer else "TOWER_BEARER (a JWT for the seeded user) is not set")
        or (
            None
            if os.environ.get("ATT_ALLOW_APPLY") == "1"
            else "changes Lambda configuration: set ATT_ALLOW_APPLY=1"
        )
    )
    if reason:
        pytest.skip(reason)
    raw = json.loads(OUTPUTS.read_text("utf-8"))
    return {k: (v.get("value") if isinstance(v, dict) and "value" in v else v) for k, v in raw.items()}


def _cold_start(lam: Any, name: str) -> None:
    cfg = lam.get_function_configuration(FunctionName=name)
    variables = dict((cfg.get("Environment") or {}).get("Variables") or {})
    variables["ATT_COLD_START"] = str(int(time.time()))
    lam.update_function_configuration(FunctionName=name, Environment={"Variables": variables})
    lam.get_waiter("function_updated_v2").wait(FunctionName=name)


def test_cold_alerts_never_change_an_answer(aws: dict[str, Any], env: Targets) -> None:
    import boto3

    lam = boto3.client("lambda", region_name=aws.get("region"))
    names = [n for n in dict(aws["lambda_function_names"]).values() if "alerts" in str(n)]
    assert names, "no Alerts Lambda in lambda_function_names"
    for name in names:
        _cold_start(lam, name)
    for tool in ("line_is_ok", "is_reachable"):
        r = call(env, "user-asish", tool, {"line": "self"})
        assert set(r["reason_codes"]) <= ACCEPTABLE, (tool, r["reason_codes"])
    for name in names:
        if "poll" not in name:
            continue
        out = lam.invoke(FunctionName=name, InvocationType="RequestResponse", Payload=b"{}")
        assert "FunctionError" not in out, f"{name} failed cold: {out['Payload'].read()[:500]!r}"
