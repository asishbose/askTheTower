"""Doc 11 §2 "The quiet probes", "Next step", "Cost banner", "Teardown", §11 (extends `test_ops_aws_status.py`):
the stepper with probe answers injected (up / down / timeout / not found); the `tf` and `ecr` filters; the header's
account; the next-step rule; the banner; the Run block reasons; the quiet-probe cache (60 s, run only when asked)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from demo_ui.ops import plan
from demo_ui.ops.allowlist import by_name
from demo_ui.ops.aws_status import COGNITO_NEEDED, NOT_DEPLOYED, account, next_step, run_block
from demo_ui.ops.envload import Facts
from demo_ui.ops.probes import (
    ProbeResult,
    ProbeSnapshot,
    QuietProbes,
    TfState,
    dashed,
    filter_ecr,
    filter_tf,
    public_slash32,
)

from tests.privacy.patterns import phone_hits

from .opsfix import BINDING, SERVICES, WEB_CHAT, Wire, everything, pipeline_for, state, write

pytestmark = pytest.mark.unit

NAMES = tuple(f"att-dev/{s}" for s in SERVICES)
STACK = TfState(True, {"binding_url": BINDING, "web_chat_url": WEB_CHAT})


def snap(**kw: object) -> ProbeSnapshot:
    return ProbeSnapshot(**kw)  # type: ignore[arg-type]


def test_ecr_probe_lists_none_with_a_file_is_stale(tmp_path: Path) -> None:
    everything(tmp_path)
    p = pipeline_for(tmp_path, Wire(), snap(ecr=ProbeResult(True, ())))
    assert state(p, 1) == ("todo", "file is stale: ECR lists none of the five")
    p = pipeline_for(tmp_path, Wire(), snap(ecr=ProbeResult(True, NAMES[:3])))
    assert state(p, 1)[0] == "fail" and "missing in ECR" in state(p, 1)[1]
    p = pipeline_for(tmp_path, Wire(), snap(ecr=ProbeResult(True, (*NAMES, "other/thing"))))
    assert state(p, 1)[0] == "done" and p.steps[0].evidence == ", ".join(sorted(NAMES))
    p = pipeline_for(tmp_path, Wire(), snap(ecr=ProbeResult(False, reason="timeout (5 s)")))
    assert state(p, 1)[0] == "done"  # a failed probe leaves the step on its file


def test_tf_no_stack_in_state_ignores_old_evidence(tmp_path: Path) -> None:
    everything(tmp_path)
    p = pipeline_for(tmp_path, Wire(), snap(tf=ProbeResult(True, TfState(False, {}))))
    assert [s.state for s in p.steps[:3]] == ["done"] * 3
    assert all(s.state == "todo" and s.detail == NOT_DEPLOYED for s in p.steps[3:])
    assert p.next_id == 4 and p.teardown.stack == "not in state"
    assert p.banner is None  # ECR alone: no banner


def test_tf_in_state_without_the_file(tmp_path: Path) -> None:
    write(tmp_path, "deploy/.env.aws", f"TOWER_URL=x\nBINDING_URL={BINDING}\n")
    p = pipeline_for(tmp_path, Wire(), snap(tf=ProbeResult(True, STACK)))
    assert state(p, 4)[0] == "done" and p.steps[3].evidence == f"{BINDING} · {WEB_CHAT}"
    assert p.teardown.stack == "in state" and p.banner is not None
    assert "an unknown time" in p.banner.text and "Down keeps ECR" in p.banner.text
    p = pipeline_for(tmp_path, Wire(), snap(tf=ProbeResult(False, reason="terraform not found")))
    assert state(p, 4)[0] == "todo" and p.teardown.stack == "unknown"  # no tf, no file: not deployed


def test_filters_keep_only_what_the_doc_allows() -> None:
    raw = json.dumps({
        "region": {"value": "us-east-1", "sensitive": False},
        "binding_url": {"value": BINDING, "sensitive": False},
        "sms_sandbox_commands": {"value": "aws sns create-sms-sandbox-phone-number +16135550101", "sensitive": True},
        "web_chat_url": {"value": WEB_CHAT, "sensitive": True},
        "mock_admin_url": {"value": "https://abc.execute-api.x/carrier-admin", "sensitive": False},
        "name": {"value": ["not", "a", "string"], "sensitive": False},
    })  # fmt: skip
    kept = filter_tf(raw)
    assert (
        kept is not None
        and kept.in_state
        and dict(kept.outputs) == {"region": "us-east-1", "binding_url": BINDING}
    )
    assert phone_hits(repr(kept)) == []
    assert filter_tf("{}") == TfState(False, {}) and filter_tf("[]") is None and filter_tf("nope") is None
    assert filter_ecr(json.dumps(list(NAMES))) == NAMES
    assert filter_ecr(json.dumps(["123456789012.dkr.ecr.us-east-1.amazonaws.com/att-dev/alerts"])) is None
    assert filter_ecr('{"a": 1}') is None and filter_ecr("[1]") is None


def test_account_is_dashed_or_unknown() -> None:
    assert dashed("123456789012") == "1234-5678-9012"
    assert account(ProbeResult(True, "123456789012")) == "account 1234-5678-9012"
    assert phone_hits(account(ProbeResult(True, "123456789012"))) == []
    assert account(ProbeResult(True, "12345")) == "account unknown: unexpected answer"
    assert account(ProbeResult(False, reason="aws CLI not found (runbook 0.8)")) == (
        "account unknown: aws CLI not found (runbook 0.8)"
    )
    assert account(None) == "account unknown: not checked"


def test_next_step_rule(tmp_path: Path) -> None:
    p = pipeline_for(tmp_path, Wire())
    assert p.next_id == 1
    everything(tmp_path)
    full = pipeline_for(tmp_path, Wire())
    assert full.next_id is None
    first_three = tuple(s if s.n <= 3 else s.__class__(s.n, s.target, s.title, "todo") for s in full.steps)
    assert next_step(first_three) == 4
    gap = tuple(s if s.n != 6 else s.__class__(s.n, s.target, s.title, "fail") for s in full.steps)
    assert next_step(gap) == 6  # 7 is after 6, so 6 is next
    only_8 = tuple(s if s.n != 8 else s.__class__(s.n, s.target, s.title, "todo") for s in full.steps)
    assert next_step(only_8) == 8


def test_run_block_reasons(tmp_path: Path) -> None:
    targets = by_name()
    p = pipeline_for(tmp_path, Wire())
    assert run_block(targets["aws-ecr-up"], p, gate=None, busy=None, facts_cognito=2) == ""
    assert run_block(targets["aws-push"], p, gate=None, busy=None, facts_cognito=2) == (
        "after 1 · ECR repositories (idempotent)"
    )
    assert run_block(targets["aws-admin-close"], p, gate=None, busy=None, facts_cognito=2) == ""
    assert (
        run_block(targets["aws-down"], p, gate="AWS runs are off", busy=None, facts_cognito=2)
        == "AWS runs are off"
    )
    assert run_block(targets["aws-ecr-up"], p, gate=None, busy="a job is running", facts_cognito=2) == (
        "a job is running"
    )
    everything(tmp_path)
    p = pipeline_for(tmp_path, Wire())
    assert run_block(targets["aws-cognito-users"], p, gate=None, busy=None, facts_cognito=1) == COGNITO_NEEDED
    assert run_block(targets["aws-cognito-users"], p, gate=None, busy=None, facts_cognito=2) == ""


def test_banner_variants(tmp_path: Path) -> None:
    assert pipeline_for(tmp_path, Wire()).banner is None
    everything(tmp_path)
    p = pipeline_for(tmp_path, Wire())
    assert p.banner is not None and p.banner.text.startswith("Deployed since 1970-01-01 01:06 UTC")
    assert "$0.06/h" in p.banner.text and "Admin path open: close it after the demo." in p.banner.text
    assert "make outputs" in p.banner.title
    p = pipeline_for(tmp_path, Wire({"/_admin/clock": 404}))
    assert p.banner is not None and "Admin path open" not in p.banner.text


def test_deploy_needs_its_word_when_the_plan_destroys(tmp_path: Path) -> None:
    f = tmp_path / "plan.txt"
    assert plan.deploy_needs_word(f)  # no plan: unreadable counts as destroying
    f.write_text("Plan: 2 to add, 1 to change, 3 to destroy.\n")
    assert plan.deploy_needs_word(f) and plan.read(f) == plan.PlanSummary(2, 1, 3)
    f.write_text("Plan: 1 to add, 0 to change, 1 to destroy.\nlater\nNo changes.\n")
    assert not plan.deploy_needs_word(f)  # the last match counts
    f.write_text("Plan: 1 to import, 4 to add, 0 to change, 0 to destroy.\n")
    assert plan.read(f) == plan.PlanSummary(4, 0, 0) and plan.read(f).line == "== plan +4 ~0 -0"  # type: ignore[union-attr]
    f.write_text("NOT RUN\n")
    assert plan.deploy_needs_word(f)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("203.0.113.7", None),  # TEST-NET-3 is documentation space, not global
        ("8.8.8.8", "8.8.8.8/32"),
        ("8.8.8.8/32", "8.8.8.8/32"),
        (" 1.1.1.1 ", "1.1.1.1/32"),
        ("10.0.0.1/32", None),
        ("127.0.0.1", None),
        ("8.8.8.0/24", None),
        ("::1", None),
        ("2001:4860:4860::8888", None),
        ("1.2.3.4; rm -rf /", None),
        ("", None),
    ],
)
def test_public_slash32(value: str, expected: str | None) -> None:
    assert public_slash32(value) == expected


class FakeProbes:
    def __init__(self) -> None:
        self.calls = 0
        self.checkips = 0

    async def facts(self) -> ProbeResult[Facts]:
        self.calls += 1
        return ProbeResult(True, Facts("att", "us-east-1", False, 2))

    async def sts(self) -> ProbeResult[str]:
        return ProbeResult(False, reason="aws CLI not found (runbook 0.8)")

    async def ecr(self) -> ProbeResult[tuple[str, ...]]:
        return ProbeResult(False, reason="aws CLI not found (runbook 0.8)")

    async def tf(self) -> ProbeResult[TfState]:
        return ProbeResult(False, reason="terraform not found")

    async def checkip(self) -> ProbeResult[str]:
        self.checkips += 1
        return ProbeResult(True, "8.8.8.8/32")


def test_quiet_probes_run_only_when_asked_and_cache() -> None:
    fake = FakeProbes()
    now = [0.0]
    quiet = QuietProbes(fake, monotonic=lambda: now[0])  # type: ignore[arg-type]

    async def go() -> None:
        assert await quiet.snapshot(gate_on=False) == ProbeSnapshot() and fake.calls == 0
        assert await quiet.snapshot(gate_on=True, run=False) == ProbeSnapshot() and fake.calls == 0  # a tick
        first = await quiet.snapshot(gate_on=True)
        assert fake.calls == 1 and first.facts is not None and first.facts.value is not None
        now[0] = 30.0
        assert await quiet.snapshot(gate_on=True) is first and fake.calls == 1  # 60 s cache
        assert await quiet.snapshot(gate_on=True, run=False) is first  # a tick reads the last snapshot
        now[0] = 61.0
        await quiet.snapshot(gate_on=True)
        assert fake.calls == 2 and fake.checkips == 1  # checkip: 10 min
        await quiet.snapshot(gate_on=True, fresh=True)
        assert fake.calls == 3 and fake.checkips == 2  # re-check skips every cache

    asyncio.run(go())
