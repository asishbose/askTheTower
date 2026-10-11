"""EKS end to end (prompt 14 acceptance): runs only with ENV=eks against a cluster made by `make deploy-eks`.

- `make demo ENV=eks` produces transcripts that match the golden files (the third environment of one transcript set).
- One Alerts CronJob fires: its schedule is shortened to every minute, the test waits for a successful run, then
  restores the 10 §3 schedule.
- The privacy grep runs over `kubectl logs` of every pod after the demo (nothing phone-number-shaped).

Without ENV=eks, or without kubectl pointing at the cluster (`make kube-context`), every test skips with the reason.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import UTC, datetime

import pytest
from ref_client.transcript import compare

from tests.e2e.helpers import ENV, GOLDEN, TRANSCRIPTS, make

pytestmark = pytest.mark.e2e

NS = os.environ.get("K8S_NAMESPACE", "ask-the-tower")
CRONJOB = "alerts-poll-transplant"
SCHEDULE = "*/5 * * * *"  # 10 §3; restored after the test


def kubectl(*args: str, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["kubectl", "-n", NS, *args],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


@pytest.fixture(scope="module")
def eks() -> str:
    if ENV != "eks":
        pytest.skip(f"ENV={ENV}: the EKS tests run only with ENV=eks (after `make deploy-eks`)")
    if shutil.which("kubectl") is None:
        pytest.skip("kubectl not installed")
    r = kubectl("get", "deploy", "tower-mcp", "-o", "name")
    if r.returncode != 0:
        pytest.skip(
            f"no Ask the Tower release in namespace {NS} (run `make deploy-eks && make kube-context`)"
        )
    return NS


def test_make_demo_eks_matches_golden(eks: str) -> None:
    r = make("demo", "ENV=eks")
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
    diffs = []
    goldens = sorted(GOLDEN.glob("*.json"))
    assert {g.stem for g in goldens} == {"moment-1", "moment-2", "moment-3", "transplant"}
    for g in goldens:
        t = TRANSCRIPTS / g.name
        assert t.exists(), f"{t} not written by make demo ENV=eks"
        diffs += [f"{g.stem}: {d}" for d in compare(json.loads(g.read_text()), json.loads(t.read_text()))]
    assert not diffs, "transcripts differ from golden:\n  " + "\n  ".join(diffs)


def test_alerts_cronjob_fires_once(eks: str) -> None:
    started = datetime.now(UTC)
    patch = json.dumps({"spec": {"schedule": "* * * * *"}})
    assert kubectl("patch", "cronjob", CRONJOB, "-p", patch).returncode == 0
    try:
        deadline = time.monotonic() + 180
        last = ""
        while time.monotonic() < deadline:
            r = kubectl("get", "cronjob", CRONJOB, "-o", "jsonpath={.status.lastSuccessfulTime}")
            last = r.stdout.strip()
            if last and datetime.fromisoformat(last.replace("Z", "+00:00")) >= started:
                break
            time.sleep(10)
        else:
            pytest.fail(f"{CRONJOB} did not complete a run within 180 s (lastSuccessfulTime={last!r})")
    finally:
        kubectl("patch", "cronjob", CRONJOB, "-p", json.dumps({"spec": {"schedule": SCHEDULE}}))


def test_pod_logs_carry_no_phone_numbers(eks: str) -> None:
    from tests.privacy.patterns import phone_hits

    logs = []
    for deploy in ("tower-mcp", "binding-page", "alerts", "mock-carrier"):
        r = kubectl("logs", f"deploy/{deploy}", "--all-containers", "--tail=-1", timeout=120)
        assert r.returncode == 0, r.stderr
        logs.append(r.stdout)
    text = "\n".join(logs)
    assert text.strip(), "no pod logs collected"
    assert phone_hits(text) == []
