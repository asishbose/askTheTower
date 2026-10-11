"""Nightly, live, opt-in: the backend swap by changing the Gateway target variable (05 §4, §7;
testing-and-showcase §2.5). Applies Terraform twice (sandbox, then back to mock), so it runs only with
ATT_ALLOW_APPLY=1, AWS credentials, a deployment, and sandbox settings in envs/aws.tfvars."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any

import pytest
from camara_client import CarrierError, LineRef

from tests.aws.conftest import ROOT, TF
from tests.aws.test_gateway_conformance import NUMBERS, gateway_client

pytestmark = pytest.mark.nightly

VARS = TF / "envs" / "aws.tfvars"


def apply(backend: str) -> None:
    tf = shutil.which("terraform")
    assert tf, "terraform not on PATH"
    r = subprocess.run(  # noqa: S603
        [
            tf,
            "apply",
            "-input=false",
            "-auto-approve",
            f"-var-file={VARS}",
            f"-var=carrier_backend={backend}",
        ],
        cwd=TF,
        capture_output=True,
        text=True,
        timeout=3600,
    )
    assert r.returncode == 0, r.stderr[-2000:]


async def test_swap_gateway_target_and_back(aws_outputs: dict[str, Any]) -> None:
    if os.environ.get("ATT_ALLOW_APPLY") != "1":
        pytest.skip("changes AWS resources: set ATT_ALLOW_APPLY=1 to run the swap")
    if not VARS.exists() or "sandbox_base_url" not in VARS.read_text():
        pytest.skip("no sandbox configured in deploy/terraform/envs/aws.tfvars")
    line = LineRef("ln_swap", NUMBERS[0])
    try:
        apply("sandbox")
        client = gateway_client(aws_outputs)
        try:
            await client.reachability(
                line
            )  # the sandbox may not know a 555 number: any mapped answer will do
        except CarrierError as e:
            assert e.reason_code is not None
        await client.aclose()
    finally:
        apply("mock")
    client = gateway_client(aws_outputs)
    assert (await client.reachability(line)).reachable is not None
    await client.aclose()
    assert (ROOT / "artifacts").exists()
