"""D8/D9 end to end (06 §11.4–11.5, criteria 23–24; e2e-wiring §5 step 6b): the line-holder's transplant settings,
saved on the binding page, drive the 20-minute text and the 15-minute escalation on the running stack.

ENV=local only for criterion 24 (it needs the mobile-data simulation, the binding page's `/_admin` and the mock's
`/_admin`); criterion 23's golden comparison runs on any ENV with `make demo`, its SMS-log check on local/eks.

Seed additions this relies on (06 §11.4): users `user-partner` / `user-neighbour`, simulated client ids
`phone-partner` / `phone-neighbour` on two new mock lines in `scenarios/demo.yaml`, and
`POST /_admin/watch-settings` on the binding page. Until they exist these tests fail at that step.

Timing: "within one real transplant-poll interval" is `ALERTS_TRANSPLANT_POLL_S / ALERTS_CLOCK_SCALE` (compose
defaults 300 / 60 = 5 s) and "one tick interval" is `ALERTS_TICK_S / ALERTS_CLOCK_SCALE` (1 s); each wait adds
`LOG_SLACK_S` for `docker compose logs` to show the line. Scripted demo.yaml events (+12 sim_swap, +20 cf_set) are
consumed before any Watch on Asish's line exists, so they cannot interleave texts or audit rows with the story.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from ref_client.transcript import compare

from tests.e2e.helpers import GOLDEN, ROOT, TRANSCRIPTS, make
from tests.e2e.tower import call
from tests.helpers.env import MockAdmin, Stack, Targets
from tests.helpers.patterns import HEALTH_WORDS, phone_hits

pytestmark = pytest.mark.e2e

COMPOSE = ["docker", "compose", "-f", str(ROOT / "deploy" / "compose" / "docker-compose.yml")]
NS = os.environ.get("K8S_NAMESPACE", "ask-the-tower")
OWNER, PARTNER, NEIGHBOUR = "user-asish", "user-partner", "user-neighbour"
DEVICES = {OWNER: "phone-asish", PARTNER: "phone-partner", NEIGHBOUR: "phone-neighbour"}
ALIAS = "asish"

_SCALE = float(os.environ.get("ALERTS_CLOCK_SCALE", "60") or 60)
POLL_S = float(os.environ.get("ALERTS_TRANSPLANT_POLL_S", "300")) / _SCALE
TICK_S = float(os.environ.get("ALERTS_TICK_S", "60")) / _SCALE
LOG_SLACK_S = 2.0

CSRF_RE = re.compile(r'name="csrf" value="([a-p]+)"')
INVITE_RE = re.compile(r'<p class="big">([A-Z]{4}-[A-Z]{4})</p>')


# --- logs (as test_alerts.py) ------------------------------------------------------------------------------


def _logs_since(env: Targets, since: datetime) -> str:
    stamp = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    if env.env == "local":
        cmd = [*COMPOSE, "logs", "--no-color", "--since", stamp, "alerts"]
    else:
        cmd = ["kubectl", "-n", NS, "logs", "deploy/alerts", "--all-containers", f"--since-time={stamp}"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)  # noqa: S603 - fixed argv
    assert r.returncode == 0, r.stderr[-1000:]
    return r.stdout


def _sms(text: str, to: str) -> list[str]:
    return [ln for ln in text.splitlines() if "SMS to=" in ln and f"to={to}" in ln]


def _wait_for(env: Targets, since: datetime, done: Callable[[str], bool], what: str, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    while True:
        text = _logs_since(env, since)
        if done(text):
            return text
        if time.monotonic() > deadline:
            pytest.fail(f"Alerts did not {what} within {timeout:.1f} s (alerts log tail):\n{text[-2000:]}")
        time.sleep(0.5)


# --- criterion 23 ------------------------------------------------------------------------------------------


def _load(path: Any) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8")))


def test_c23_make_demo_matches_golden_and_transplant_ends_ok(stack: Stack, env: Targets) -> None:
    started = datetime.now(UTC)
    t0 = time.time()
    r = make("demo", timeout=900)
    out = r.stdout + r.stderr
    assert r.returncode == 0, f"make demo exited {r.returncode}:\n{out[-4000:]}"
    assert "demo ok" in out, out[-4000:]

    golden = _load(GOLDEN / "transplant.json")
    transcript_path = TRANSCRIPTS / "transplant.json"
    assert transcript_path.stat().st_mtime >= t0 - 2, "make demo did not rewrite the transplant transcript"
    transcript = _load(transcript_path)
    assert not compare(golden, transcript), compare(golden, transcript)

    # 06 §11.4: one control is added for the Settings step, before the first utterance.
    first_utt = next(i for i, s in enumerate(transcript["steps"]) if "utterance" in s)
    early = [s["control"] for s in transcript["steps"][:first_utt] if "control" in s]
    assert any("setting" in c.lower() for c in early), (
        f"no Settings control before the first utterance: {early}"
    )
    g_controls = [s for s in golden["steps"] if "control" in s]
    assert len(g_controls) == 3, "golden transplant.json: one control added for the settings step (06 §11.4)"

    if env.env == "aws":
        return  # SMS goes through SNS to a real phone; a human watches it
    text = _logs_since(env, started)
    assert _sms(text, f"chain:{PARTNER}"), "the transplant story texted no chain:user-partner"
    assert _sms(text, f"chain:{NEIGHBOUR}"), "the transplant story texted no chain:user-neighbour"


# --- criterion 24 ------------------------------------------------------------------------------------------


def _csrf(c: httpx.Client) -> str:
    r = c.get("/me")
    assert r.status_code == 200, r.text[:300]
    m = CSRF_RE.search(r.text)
    assert m, "no CSRF token on /me"
    return m.group(1)


def _bind(env: Targets, user: str) -> httpx.Client:
    """The one-tap flow with the mobile-data simulation (`?as=<client id>`), in a fresh cookie jar."""
    c = httpx.Client(base_url=env.binding_url, follow_redirects=True, timeout=15.0)
    r = c.post("/_admin/bind-tokens", json={"user_id": user})
    assert r.status_code == 200, f"binding admin bind-tokens → {r.status_code}"
    path = httpx.URL(r.json()["url"]).path
    device = DEVICES[user]
    assert c.get(f"{path}?as={device}").status_code == 200
    r = c.post(f"{path}/verify", data={"as": device})
    assert r.status_code == 200 and "Line connected" in r.text, f"{user} could not bind with {device}"
    return c


def _invite(c: httpx.Client) -> str:
    r = c.post("/me/invite", data={"csrf": _csrf(c)})
    assert r.status_code == 200
    m = INVITE_RE.search(r.text)
    assert m, "no invite code shown"
    return m.group(1)


def _tables(env: Targets) -> dict[str, list[dict[str, Any]]]:
    r = httpx.get(f"{env.binding_url}/_admin/tables", params={"format": "json"}, timeout=15.0)
    assert r.status_code == 200
    return dict(r.json())


def _asish_line(env: Targets) -> str:
    (line,) = [ln["line_id"] for ln in _tables(env)["Lines"] if ln["owner_user_id"] == OWNER]
    return str(line)


def _audit(env: Targets, line: str) -> dict[str, dict[str, Any]]:
    return {
        str(a["ts_seq"]): a
        for a in _tables(env)["Audit"]
        if a.get("line_id") == line and not str(a.get("ts_seq", "")).startswith("~")
    }


@pytest.fixture(scope="module")
def local_stack(stack: Stack, env: Targets, mock_admin: MockAdmin) -> Iterator[tuple[Targets, MockAdmin]]:
    if env.env != "local":
        pytest.skip(f"ENV={env.env}: the one-tap simulation and both /_admin pages exist only with ENV=local")
    r = make("seed", timeout=300)
    assert r.returncode == 0, (r.stdout + r.stderr)[-2000:]
    yield env, mock_admin
    make("seed", timeout=300)  # back to the demo's starting state


def test_c24_full_path_settings_on_page_to_escalation(local_stack: tuple[Targets, MockAdmin]) -> None:
    env, admin = local_stack
    page = httpx.Client(base_url=env.binding_url, timeout=15.0)

    # A clean start: no grants to the contacts, no saved settings, demo.yaml's scripted events consumed.
    for grantee in (PARTNER, NEIGHBOUR):
        body = {"owner_user_id": OWNER, "grantee_user_id": grantee, "grant": "watch", "alias": ALIAS}
        r = page.post("/_admin/grants", json=body | {"action": "revoke"})
        assert r.status_code == 200, r.text[:300]
    r = page.post(
        "/_admin/watch-settings",
        json={"owner_user_id": OWNER, "profile": "self", "contacts": [], "reset": True},
    )
    assert r.status_code == 200, f"/_admin/watch-settings → {r.status_code}"
    admin.load_scenario("demo")
    admin.advance_clock(minutes=21)

    lines = admin.state()["lines"]
    (asish_msisdn,) = [m for m, ln in lines.items() if "phone-asish" in ln.get("mobile_data_client_ids", [])]

    # 1. each binds with the one-tap flow
    asish, partner, neighbour = (_bind(env, u) for u in (OWNER, PARTNER, NEIGHBOUR))
    line = _asish_line(env)

    # 2–3. the contacts create invite codes; Asish grants both `watch` on /me
    for contact in (partner, neighbour):
        code = _invite(contact)
        r = asish.post(
            "/grants", data={"csrf": _csrf(asish), "invite_code": code, "kind": "watch", "alias": ALIAS}
        )
        assert r.status_code == 200, r.text[:300]

    # 4. Asish saves transplant + [partner, neighbour] with the real form
    before = set(_audit(env, line))
    r = asish.post(
        f"/me/lines/{line}/watch-settings",
        data={
            "profile": "transplant",
            "contact_1": PARTNER,
            "contact_2": NEIGHBOUR,
            "contact_3": "",
            "csrf": _csrf(asish),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, f"watch-settings → {r.status_code}"
    me = asish.get("/me").text
    assert "transplant" not in me.lower() and not phone_hits(me)

    # 5. "Alexa, watch my line"
    res = call(env, OWNER, "watch_line", {"line": "self", "enable": True})
    assert res["reason_codes"] == ["OK"]
    assert res["facts"].get("profile") == "transplant"
    wire = json.dumps(res)
    assert PARTNER not in wire and NEIGHBOUR not in wire and not phone_hits(wire)
    time.sleep(POLL_S + LOG_SLACK_S)  # one poll: the baseline observation of the enabled Watch

    # 6. dark 10 min, one reachable, dark again
    t0 = datetime.now(UTC)
    admin.fire_event(asish_msisdn, "unreachable")
    admin.advance_clock(minutes=10)
    admin.fire_event(asish_msisdn, "reachable")
    admin.fire_event(asish_msisdn, "unreachable")

    # 7. +19 min: no chain text within one poll interval
    admin.advance_clock(minutes=19)
    time.sleep(POLL_S + LOG_SLACK_S)
    early = _logs_since(env, t0)
    assert not [ln for ln in early.splitlines() if "SMS to=chain:" in ln], "a chain text before 20 min dark"

    # 8. +1 min: exactly one text to the partner, none to the neighbour
    admin.advance_clock(minutes=1)
    text = _wait_for(
        env, t0, lambda t: len(_sms(t, f"chain:{PARTNER}")) >= 1, "text the partner", POLL_S + LOG_SLACK_S
    )
    assert len(_sms(text, f"chain:{PARTNER}")) == 1
    assert _sms(text, f"chain:{NEIGHBOUR}") == []
    body = _sms(text, f"chain:{PARTNER}")[0].split("body=", 1)[-1]
    assert body.strip("'\"").startswith(f"{ALIAS}'s phone"), (
        "the partner's text names the line by their alias"
    )
    assert not phone_hits(_sms(text, f"chain:{PARTNER}")[0]) and not HEALTH_WORDS.search(body)

    # 9. +15 min, no reply: one text to the neighbour within one tick
    admin.advance_clock(minutes=15)
    text = _wait_for(
        env,
        t0,
        lambda t: len(_sms(t, f"chain:{NEIGHBOUR}")) >= 1,
        "escalate to the neighbour",
        TICK_S + LOG_SLACK_S,
    )
    assert len(_sms(text, f"chain:{NEIGHBOUR}")) == 1
    assert len(_sms(text, f"chain:{PARTNER}")) == 1

    # 10. Asish's audit rows from step 4 on, in chain order
    after = _audit(env, line)
    new = [after[k] for k in sorted(set(after) - before)]
    got = [(a["tool"], a["trigger"], a["outcome"], tuple(a["reason_codes"])) for a in new]
    assert [g[:3] for g in got[:2]] == [("watch_line", "binding", "ok"), ("watch_line", "voice", "ok")], got
    assert [(g[0], g[2], g[3]) for g in got[2:]] == [
        ("alert", "changed", ("UNREACHABLE",)),
        ("alert", "changed", ("UNREACHABLE",)),
    ], got

    for c in (asish, partner, neighbour, page):
        c.close()
