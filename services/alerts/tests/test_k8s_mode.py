"""ALERTS_MODE=k8s (prompt 14): the Lambda's sender defaults, no in-process scheduler, CronJob entry point."""

from __future__ import annotations

import json

import pytest
from alerts import job
from alerts.config import Settings

pytestmark = pytest.mark.unit


def test_k8s_mode_defaults_to_sns_like_lambda() -> None:
    s = Settings.from_env({"ALERTS_MODE": "k8s"})
    assert s.mode == "k8s" and s.sender == "sns"
    assert Settings.from_env({"ALERTS_MODE": "k8s", "ALERTS_SENDER": "log"}).sender == "log"  # kind
    assert Settings.from_env({"ALERTS_MODE": "lambda"}).mode == "lambda"
    assert Settings.from_env({"ALERTS_MODE": "other"}).mode == "local"
    assert Settings.from_env({}).sender == "log"


@pytest.mark.parametrize(
    ("argv", "target", "event"),
    [
        (["poll", "transplant"], "alerts", {"profile": "transplant"}),
        (["poll", "care"], "alerts", {"profile": "care"}),
        (["poll", "self"], "alerts", {"profile": "self"}),
        (["tick"], "alerts", {"action": "tick"}),
    ],
)
def test_job_builds_the_scheduler_payloads(argv: list[str], target: str, event: dict[str, str]) -> None:
    assert job.event_for(argv) == (target, event)


def test_job_reconcile_carries_an_aware_now() -> None:
    from tower_audit.aws import event_time

    target, event = job.event_for(["reconcile"])
    assert target == "reconcile" and event_time(event).tzinfo is not None


def test_job_rejects_unknown_profile() -> None:
    with pytest.raises(SystemExit):
        job.event_for(["poll", "everyone"])


def test_job_dispatches_to_the_lambda_handler(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    seen = []
    monkeypatch.setattr("alerts.handler.lambda_handler", lambda event: seen.append(event) or {"polled": 0})
    assert job.main(["poll", "care"]) == 0
    assert seen == [{"profile": "care"}]
    assert json.loads(capsys.readouterr().out) == {"polled": 0}
