#!/usr/bin/env python3
"""`make showcase`: testing-and-showcase.md §4, steps 1–9, in order, against the running local stack.

    uv run python scripts/showcase_order.py [--pause] [--fast] [--only N ...] [--list]

`--pause` waits for enter before each step ("press enter for step 4: moment 1"; steps 4–6 are one reference-client
run that pauses before each moment itself). `--fast` drops the Alerts showcase's on-camera pacing (tests, CI).
Each step is a `make` target, so every step also runs on its own. Exit status: the first failing step's.

Steps 4–6 run through the reference client here; on Alexa+ they are the simulator (prompt 15).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", str(ROOT / "deploy/compose/docker-compose.yml")]


@dataclass(frozen=True)
class Step:
    number: str  # "4–6" for the one reference-client run
    label: str
    says: str
    make: tuple[str, ...]


MOMENTS = "--story moment-1 --story moment-2 --story moment-3"


def steps(*, pause: bool, fast: bool) -> list[Step]:
    return [
        Step(
            "1",
            "mock",
            "this is a carrier, built to the public spec; here's the clock",
            ("showcase-mock", "SHOWCASE_ARGS=--print-only"),
        ),
        Step("2", "policy table", "this is every decision the system can make", ("policy-table",)),
        Step(
            "3",
            "binding",
            "the one tap — against the mock, the client-id header stands in for mobile data",
            ("showcase-binding",),
        ),
        Step(
            "4–6",
            "moments 1–3",
            "lost signal → SIM moved; forwarding; Mom's line, the watch, the buzz, the revoke",
            ("demo", f"DEMO_ARGS={MOMENTS}" + (" --pause" if pause else "")),
        ),
        Step(
            "7",
            "transplant closing story",
            "20 minutes dark → the partner's text; 15 more → the second contact",
            ("showcase-alerts",) + (("SHOWCASE_ARGS=--fast",) if fast else ()),
        ),
        Step("8", "audit", "Mom's view, chain verified", ("showcase-audit",)),
        Step("9", "make demo", "the same transcripts from the terminal — you can run this", ("demo",)),
    ]


def buzz() -> None:
    """After moment 3: the watcher's phone buzz, which locally is Alerts' SMS log line."""
    cmd = [*COMPOSE, "logs", "--no-color", "alerts"]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
    lines = [ln for ln in r.stdout.splitlines() if "SMS to=" in ln]
    print("\n  [alerts] the phone buzz (SMS log sink):")
    for ln in lines[-3:] or ["(no SMS line yet — is the stack up? `make logs`)"]:
        print(f"    {ln.split('|', 1)[-1].strip()}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pause", action="store_true")
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--only", action="append", default=[], help="run only these step numbers (e.g. 1, 4–6)")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    plan = [s for s in steps(pause=a.pause, fast=a.fast) if not a.only or s.number in a.only]
    if a.list:
        for s in plan:
            print(f"{s.number:>4}  {s.label:<26} make {' '.join(s.make)}")
        return 0
    env = os.environ | {"ENV": os.environ.get("ENV", "local")}
    for s in plan:
        print(f"\n===== step {s.number}: {s.label} — “{s.says}” =====", flush=True)
        if a.pause and s.number != "4–6":  # the reference client pauses before each moment itself
            input(f"press enter for step {s.number}: {s.label} ")
        cmd = ["make", "--no-print-directory", *s.make]
        rc = subprocess.run(cmd, cwd=ROOT, env=env, check=False).returncode  # noqa: S603
        if rc != 0:
            print(f"\nshowcase: step {s.number} ({s.label}) failed with exit {rc}", file=sys.stderr)
            return rc
        if s.number == "4–6":
            buzz()
    print("\nshowcase: steps " + ", ".join(s.number for s in plan) + " done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
