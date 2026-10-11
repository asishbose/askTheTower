"""Alerts §2.6 against the running ENV, observed where the "phone" is: Alerts' SMS log lines (local compose:
`docker compose logs alerts`; eks: `kubectl logs deploy/alerts`). On aws the SMS goes to a real phone through
SNS — a human watches it (TODO(human) in the build log), so the test skips there.

watch Mom's line → fire `sim_swap` on it → the watcher is texted once → fire again within 6 h → audited
(`suppressed`), not sent → revoke the grant → fire → `SUPPRESSED_REVOKED`, nothing sent. The transplant windows
(19 min → nothing, 20 → text, +15 → second contact) run in `make showcase-alerts`, which
`test_showcase_order.py` step 7 runs. The stack is re-seeded afterwards, so the demo tests are unaffected.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import httpx
import pytest

from tests.e2e.helpers import ROOT, make
from tests.e2e.tower import call
from tests.helpers.env import MockAdmin, Stack, Targets
from tests.helpers.patterns import HEALTH_WORDS, phone_hits

pytestmark = pytest.mark.e2e

COMPOSE = ["docker", "compose", "-f", str(ROOT / "deploy" / "compose" / "docker-compose.yml")]
NS = os.environ.get("K8S_NAMESPACE", "ask-the-tower")


def _logs_since(env: Targets, since: datetime) -> str:
    stamp = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    if env.env == "local":
        cmd = [*COMPOSE, "logs", "--no-color", "--since", stamp, "alerts"]
    else:
        cmd = ["kubectl", "-n", NS, "logs", "deploy/alerts", "--all-containers", f"--since-time={stamp}"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)  # noqa: S603
    assert r.returncode == 0, r.stderr[-1000:]
    return r.stdout


def _wait_for(
    env: Targets, since: datetime, done: Callable[[str], bool], what: str, timeout: float = 30
) -> str:
    deadline = time.monotonic() + timeout
    while True:
        text = _logs_since(env, since)
        if done(text):
            return text
        if time.monotonic() > deadline:
            pytest.fail(
                f"Alerts did not {what} within {timeout:.0f} s; alerts log since {since:%H:%M:%S}:\n{text[-3000:]}"
            )
        time.sleep(1)


def _sms(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if "SMS to=" in ln]


@pytest.fixture(scope="module")
def alerts_env(stack: Stack, env: Targets, mock_admin: MockAdmin) -> Iterator[tuple[Targets, MockAdmin, str]]:
    if env.env == "aws":
        pytest.skip(
            "ENV=aws: the SMS goes to a real phone through SNS; a human watches it (build log TODO(human))"
        )
    if env.env == "eks" and shutil.which("kubectl") is None:
        pytest.skip("kubectl not installed")
    if env.env == "local":
        r = make("seed", timeout=300)
        assert r.returncode == 0, (r.stdout + r.stderr)[-2000:]
    lines = mock_admin.state()["lines"]
    mom = [m for m, line in lines.items() if "phone-mom" in line.get("mobile_data_client_ids", [])]
    assert len(mom) == 1, "the demo scenario has no single line for phone-mom"
    yield env, mock_admin, mom[0]
    if env.env == "local":
        make("seed", timeout=300)  # back to the demo's starting state for the tests after this one


def _grant(env: Targets, action: str) -> None:
    body = {"owner_user_id": "user-mom", "grantee_user_id": "user-asish", "grant": "watch", "alias": "mom"}
    r = httpx.post(f"{env.binding_url}/_admin/grants", json=body | {"action": action}, timeout=15.0)
    assert r.status_code == 200, f"binding admin grants → {r.status_code}: {r.text[:300]}"


def test_watch_fire_ratelimit_revoke(alerts_env: tuple[Targets, MockAdmin, str]) -> None:
    env, admin, mom = alerts_env
    r = call(env, "user-asish", "watch_line", {"line": "mom", "enable": True})
    assert r["reason_codes"] == ["OK"] and r["facts"]["watching"] is True

    t0 = datetime.now(UTC)
    admin.fire_event(mom, "sim_swap")
    text = _wait_for(env, t0, lambda t: len(_sms(t)) >= 1, "text the watcher after the sim_swap")
    assert "outcome=changed codes=SIM_SWAPPED_RECENT" in text
    first = _sms(text)
    assert len(first) == 1 and "to=chain:user-asish" in first[0], first
    body = first[0].split("body=", 1)[-1]
    assert not phone_hits(first[0]) and not HEALTH_WORDS.search(body)

    # Everything below reads the log since t0 and counts: `--since` has one-second resolution.
    admin.advance_clock(minutes=10)  # a second, later swap (same instant = no change at all)
    admin.fire_event(mom, "sim_swap")  # within 6 h of the first text: audited, not sent
    _wait_for(
        env,
        t0,
        lambda t: "outcome=suppressed codes=SIM_SWAPPED_RECENT" in t,
        "audit the repeat as suppressed",
    )
    time.sleep(2)
    assert _sms(_logs_since(env, t0)) == first, "a second SMS inside the 6 h rate limit"

    if env.env != "local":
        return  # the grant admin is a local-only page (BIND_ADMIN=1); revocation on eks is the demo's moment 3
    _grant(env, "revoke")
    admin.advance_clock(minutes=10)
    admin.fire_event(mom, "sim_swap")
    _wait_for(env, t0, lambda t: "codes=SUPPRESSED_REVOKED" in t, "audit SUPPRESSED_REVOKED after the revoke")
    time.sleep(2)
    assert _sms(_logs_since(env, t0)) == first, "texted after the grant was revoked"
