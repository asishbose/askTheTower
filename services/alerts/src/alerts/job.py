"""One scheduled run, for Kubernetes CronJobs (`ALERTS_MODE=k8s`; 10 §1 EKS column, 10 §3 schedules).

    python -m alerts.job poll transplant|care|self     # the EventBridge Scheduler payload {"profile": ...}
    python -m alerts.job tick                          # {"action": "tick"}: escalation steps
    python -m alerts.job reconcile                     # audit trim + reconciliation (tower_audit.aws)

Each command builds the event the Lambda would receive and hands it to the same dispatcher
(`alerts.handler.lambda_handler`), so a CronJob and an EventBridge schedule run identical code. `reconcile`'s
`now` is read here, at the process edge (packages read no clock), as Scheduler's `scheduled_time` would be.
Prints the handler's JSON result; exits non-zero on an exception so the Job is marked failed.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

PROFILES = ("transplant", "care", "self")


def event_for(argv: list[str]) -> tuple[str, dict[str, Any]]:
    ap = argparse.ArgumentParser(prog="python -m alerts.job", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("poll", help="poll one watch profile")
    p.add_argument("profile", choices=PROFILES)
    sub.add_parser("tick", help="escalation tick")
    sub.add_parser("reconcile", help="audit trim + reconciliation")
    args = ap.parse_args(argv)
    if args.cmd == "poll":
        return "alerts", {"profile": args.profile}
    if args.cmd == "tick":
        return "alerts", {"action": "tick"}
    return "reconcile", {"now": datetime.now(UTC).isoformat()}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    target, event = event_for(sys.argv[1:] if argv is None else argv)
    if target == "reconcile":
        from tower_audit.aws import reconcile_handler

        result: Any = reconcile_handler(event)
    else:
        from alerts.handler import lambda_handler

        result = lambda_handler(event)
    print(json.dumps(result, default=str, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
