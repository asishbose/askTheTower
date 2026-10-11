#!/usr/bin/env python3
"""Seed Ask the Tower on Kubernetes — the same seed as compose (`make seed`) and AWS (`scripts/aws_seed.py`), run as
a Job in the cluster (the umbrella chart's post-install/post-upgrade hook `<release>-seed`).

    uv run python scripts/k8s_seed.py                  # re-run the seed Job of release `att` and print its log
    uv run python scripts/k8s_seed.py --dry-run        # print the kubectl/helm commands only
    uv run python scripts/k8s_seed.py --sync-chart     # copy the seed scripts into deploy/helm/umbrella/files/
    python /seed/k8s_seed.py --in-cluster              # what the Job runs (tower-mcp image)

In the cluster the Job picks the seed by `TOWER_ENV`, as the stacks do:

- `local` (kind): exactly `deploy/compose/seed/seed.py` (copied next to this file as `compose_seed.py`): DynamoDB
  Local tables, the mock reset to `scenarios/demo.yaml`, both lines bound through the binding page's real one-tap
  flow (simulated mobile-data client ids), Mom's `watch` grant to Asish.
- anything else (EKS): `scripts/aws_seed.py`'s two steps without ECS Exec — the in-cluster mock is reachable on
  its ClusterIP — so (1) `POST <MOCK_URL>/_admin/scenarios/load`, then (2) `tower_mcp.seed.seed_demo` with KMS-made
  line ids (IRSA credentials; Terraform made the tables). The two demo lines are taken from the mock's own state by
  their mobile-data client ids; numbers stay in memory, never printed. Idempotent: an existing binding is reported.
"""

from __future__ import annotations

import argparse
import json
import os
import runpy
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CHART_FILES = ROOT / "deploy" / "helm" / "umbrella" / "files"
COMPOSE_SEED = ROOT / "deploy" / "compose" / "seed" / "seed.py"
PEOPLE = {"asish": "phone-asish", "mom": "phone-mom"}


def say(msg: str) -> None:
    print(f"seed: {msg}", flush=True)


# --- chart files ----------------------------------------------------------------------------------------------


def sync_chart(dest: Path = CHART_FILES) -> list[Path]:
    """Copy this script and the compose seed into the umbrella chart (Helm reads files inside the chart only).
    The copies are generated (gitignored); `make helm-lint`, `helm-kind` and `deploy-eks` run this first."""
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    for src, name in ((Path(__file__).resolve(), "k8s_seed.py"), (COMPOSE_SEED, "compose_seed.py")):
        target = dest / name
        shutil.copyfile(src, target)
        out.append(target)
    return out


# --- in the cluster -------------------------------------------------------------------------------------------


def wait_ok(client: Any, path: str, timeout_s: float = 180.0) -> None:
    import httpx

    deadline = time.monotonic() + timeout_s
    while True:
        try:
            if client.get(path).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            raise SystemExit(f"seed: {client.base_url}{path} did not answer 200 within {timeout_s:.0f}s")
        time.sleep(2)


def demo_lines(state: dict[str, Any]) -> dict[str, str]:
    """{asish|mom: E.164} from the mock's `/_admin/state` dump, by mobile-data client id."""
    found: dict[str, str] = {}
    lines = state.get("lines") or {}
    for line in lines.values() if isinstance(lines, dict) else lines:
        for who, client_id in PEOPLE.items():
            if client_id in (line.get("mobile_data_client_ids") or []):
                found[who] = str(line["msisdn"])
    missing = sorted(set(PEOPLE) - set(found))
    if missing:
        raise SystemExit(
            f"seed: the mock's scenario has no line for {', '.join(missing)} (mobile_data_client_ids)"
        )
    return found


def seed_aws(env: dict[str, str], scenario: str) -> int:
    import httpx

    mock_url = env.get("MOCK_URL", "http://mock-carrier:8443").rstrip("/")
    with httpx.Client(base_url=mock_url, timeout=15.0) as mock:
        wait_ok(mock, "/healthz")
        mock.post("/_admin/scenarios/load", json={"name": scenario}).raise_for_status()
        say(f"mock: scenarios/{scenario}.yaml loaded (clock and lines reset)")
        lines = demo_lines(mock.get("/_admin/state").json())

    from tower_consent import Store, crypto_from_env
    from tower_consent.errors import ConsentError
    from tower_mcp.seed import seed_demo

    store = Store.from_env(env)
    hasher, cipher = crypto_from_env(env)
    say(
        f"store: tables {env.get('TOWER_TABLE_PREFIX', '')}* in {env.get('AWS_REGION', '')}, KMS keys from the env"
    )
    try:
        seeded = seed_demo(
            store, hasher, cipher, asish_e164=lines["asish"], mom_e164=lines["mom"], now=datetime.now(UTC)
        )
    except ConsentError as e:
        say(f"store: already seeded ({type(e).__name__}) — leaving it as is")
        return 0
    say(f"store: seeded users {seeded.asish_user}, {seeded.mom_user}; Mom → Asish watch as 'mom'")
    say("done — `make demo ENV=eks` next")
    return 0


def in_cluster(scenario: str) -> int:
    env = dict(os.environ)
    if env.get("TOWER_ENV", "local") == "local":
        compose_seed = HERE / "compose_seed.py"
        if not compose_seed.exists():
            raise SystemExit(
                f"seed: {compose_seed} missing (run scripts/k8s_seed.py --sync-chart before install)"
            )
        say("TOWER_ENV=local → the compose seed (deploy/compose/seed/seed.py)")
        runpy.run_path(str(compose_seed), run_name="__main__")  # exits with its own status
        return 0
    return seed_aws(env, scenario)


# --- from a laptop: re-run the hook Job -----------------------------------------------------------------------


def hook_docs(release: str, namespace: str) -> list[dict[str, Any]]:
    import yaml

    out = subprocess.run(  # noqa: S603
        ["helm", "get", "hooks", release, "-n", namespace],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    docs = [d for d in yaml.safe_load_all(out) if isinstance(d, dict)]
    return [d for d in docs if d.get("metadata", {}).get("name") == f"{release}-seed"]


def rerun(release: str, namespace: str, dry_run: bool, timeout: str) -> int:
    job = f"{release}-seed"
    kubectl = ["kubectl", "-n", namespace]
    cmds = [
        [*kubectl, "delete", "job", job, "--ignore-not-found", "--wait=true"],
        [*kubectl, "apply", "-f", "-"],
        [*kubectl, "wait", "--for=condition=complete", f"job/{job}", f"--timeout={timeout}"],
        [*kubectl, "logs", f"job/{job}"],
    ]
    if dry_run:
        print(f"+ helm get hooks {release} -n {namespace}   # → ConfigMap + Job {job}")
        for c in cmds:
            print("+", " ".join(c))
        return 0
    docs = hook_docs(release, namespace)
    if not any(d.get("kind") == "Job" for d in docs):
        raise SystemExit(f"release {release} has no seed hook (install with seed.enabled=true)")
    manifest = "\n---\n".join(json.dumps(d) for d in docs)
    subprocess.run(cmds[0], check=True)  # noqa: S603
    subprocess.run(cmds[1], input=manifest, text=True, check=True)  # noqa: S603
    done = subprocess.run(cmds[2], check=False)  # noqa: S603
    subprocess.run(cmds[3], check=False)  # noqa: S603
    return done.returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--in-cluster", action="store_true", help="run the seed (inside the Job)")
    mode.add_argument(
        "--sync-chart", action="store_true", help="copy the seed scripts into the umbrella chart"
    )
    ap.add_argument("--scenario", default=os.environ.get("MOCK_SCENARIO", "demo"))
    ap.add_argument("--release", default=os.environ.get("HELM_RELEASE", "att"))
    ap.add_argument("--namespace", "-n", default=os.environ.get("K8S_NAMESPACE", "ask-the-tower"))
    ap.add_argument("--timeout", default="600s")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if not args.scenario.replace("-", "").replace("_", "").isalnum():
        raise SystemExit(f"bad scenario name {args.scenario!r}")
    if args.sync_chart:
        for p in sync_chart():
            print(f"wrote {p.relative_to(ROOT)}")
        return 0
    if args.in_cluster:
        return in_cluster(args.scenario)
    return rerun(args.release, args.namespace, args.dry_run, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
