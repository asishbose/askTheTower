#!/usr/bin/env python3
"""Build the five service images for arm64 and push them to ECR (`make push ENV=aws|eks`).

Repositories come from Terraform (`ecr_repositories` output: service → repository URL): the main root's
artifacts/tf-outputs.json when it has them, else the ECR root's artifacts/tf-outputs-ecr.json (`make ecr-up`), so a
push works before the first `make deploy`. Each image is built from the repository root with its own Dockerfile
(the same images compose runs) and tagged with the git sha and `latest`. `make push` records the tag in
artifacts/image-tag and `make plan` / `make deploy` use it as `image_tag`.

Provenance and SBOM attestations are off: with them buildx pushes an OCI image index, and Lambda and AgentCore
Runtime want a single-manifest linux/arm64 image. (`make sbom` produces the SBOMs locally instead.)

    uv run python scripts/push_images.py --env aws --tag "$(git rev-parse --short HEAD)"
    uv run python scripts/push_images.py --env aws --tag dev --dry-run      # print the commands only
    uv run python scripts/push_images.py --env aws --tag <sha> --verify     # check every repo has the tag (deploy)
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _tf_outputs import OUTPUTS, ROOT, load_outputs  # noqa: E402

ECR_OUTPUTS = ROOT / "artifacts" / "tf-outputs-ecr.json"  # `make ecr-outputs` (the ECR root)

SERVICES = ["mock-carrier", "tower-mcp", "binding-page", "alerts", "ref-client"]


def commands(repos: dict[str, str], region: str, tag: str, services: list[str]) -> list[list[str]]:
    registry = next(iter(repos.values())).split("/", 1)[0]
    cmds: list[list[str]] = [
        [
            "sh",
            "-c",
            f"aws ecr get-login-password --region {shlex.quote(region)} | "
            f"docker login --username AWS --password-stdin {shlex.quote(registry)}",
        ]
    ]
    for s in services:
        repo = repos[s]
        cmds.append(
            [
                "docker", "buildx", "build", "--platform", "linux/arm64",
                "--provenance=false", "--sbom=false",
                "-f", f"services/{s}/Dockerfile",
                "-t", f"{repo}:{tag}", "-t", f"{repo}:latest",
                "--push", ".",
            ]
        )  # fmt: skip
    return cmds


def verify_commands(repos: dict[str, str], region: str, tag: str, services: list[str]) -> list[list[str]]:
    """One `aws ecr describe-images` per service: fails (non-zero) when the tag was never pushed."""
    return [
        [
            "aws", "ecr", "describe-images", "--region", region,
            "--repository-name", repos[s].split("/", 1)[1],
            "--image-ids", f"imageTag={tag}", "--query", "imageDetails[0].imageTags", "--output", "text",
        ]
        for s in services
    ]  # fmt: skip


def resolve_outputs(env: str, explicit: Path | None) -> dict[str, Any]:
    """Outputs that carry `ecr_repositories`: --outputs if given, else the env's file, the aws file, the ECR root's."""
    if explicit is not None:
        candidates = [explicit, ECR_OUTPUTS]
    else:
        candidates = [OUTPUTS[env], OUTPUTS["aws"], ECR_OUTPUTS]  # EKS reuses the same ECR
    for path in candidates:
        if path.exists():
            o = load_outputs(path)
            if o.get("ecr_repositories"):
                return o
    tried = ", ".join(str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p) for p in candidates)
    raise SystemExit(f"no ecr_repositories in {tried}: run `make ecr-up` (or `make ecr-outputs`) first")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", choices=["aws", "eks"], required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--only", nargs="*", default=None, help="subset of services")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true", help="only check that every repository has --tag")
    ap.add_argument("--outputs", type=Path, default=None, help="terraform output -json file")
    args = ap.parse_args(argv)

    o = resolve_outputs(args.env, args.outputs)
    repos = {str(k): str(v) for k, v in dict(o["ecr_repositories"]).items()}
    region = str(o["region"])
    services = args.only or SERVICES
    build = verify_commands if args.verify else commands
    for cmd in build(repos, region, args.tag, services):
        print("+", " ".join(shlex.quote(c) for c in cmd))
        if not args.dry_run:
            subprocess.run(cmd, cwd=ROOT, check=True)  # noqa: S603
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
