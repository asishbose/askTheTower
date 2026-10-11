#!/usr/bin/env python3
"""Run the schemathesis CLI (`--checks all`) against a *running* mock, one vendored spec at a time —
the CI form of tests/test_conformance.py (which runs the same checks in-process).

    uv run python services/mock-carrier/scripts/conformance.py [--url http://localhost:8443]

Tokens come from the mock's own /oauth2 endpoints; Number Verification uses an auth-code token from a
simulated mobile-data device (X-Mock-Client-Id). Exit code is non-zero if any API fails. JUnit
reports land in artifacts/schemathesis/."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

SERVICE = Path(__file__).resolve().parents[1]
ROOT = SERVICE.parents[1]
APIS = [
    "sim-swap",
    "sim-swap-subscriptions",
    "call-forwarding-signal",
    "number-verification",
    "device-reachability-status",
    "device-reachability-status-subscriptions",
]
REDIRECT = "http://localhost:8081/cb"


def tokens(url: str) -> dict[str, str]:
    with httpx.Client(base_url=url, timeout=5) as c:
        cc = c.post(
            "/oauth2/token", data={"grant_type": "client_credentials"}, auth=("tower", "local-dev-tower")
        )
        cc.raise_for_status()
        r = c.get(
            "/oauth2/authorize",
            params={"client_id": "binding-page", "redirect_uri": REDIRECT},
            headers={"X-Mock-Client-Id": "phone-asish"},
        )
        code = httpx.URL(r.headers["location"]).params["code"]
        nv = c.post(
            "/oauth2/token",
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT},
            auth=("binding-page", "local-dev-binding"),
        )
        nv.raise_for_status()
        return {"cc": cc.json()["access_token"], "nv": nv.json()["access_token"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8443")
    ap.add_argument("--max-examples", default="30")
    args = ap.parse_args()
    exe = shutil.which("schemathesis") or shutil.which("st")
    if exe is None:
        print("schemathesis CLI not installed (uv sync installs it as a dev dependency)")
        return 2
    toks = tokens(args.url)
    report_dir = ROOT / "artifacts" / "schemathesis"
    failed = []
    for api in APIS:
        tok = toks["nv" if api == "number-verification" else "cc"]
        cmd = [
            exe,
            "--config-file",
            str(SERVICE / "schemathesis.toml"),
            "run",
            f"{args.url}/openapi/{api}.json",
            "--checks",
            "all",
            "--max-examples",
            args.max_examples,
            "-H",
            f"Authorization: Bearer {tok}",
            "--report",
            "junit",
            "--report-dir",
            str(report_dir / api),
        ]
        print(f"\n=== {api} ===", flush=True)
        if subprocess.call(cmd) != 0:  # noqa: S603 — fixed argv
            failed.append(api)
    print("\nconformance:", "all passed" if not failed else f"FAILED: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
