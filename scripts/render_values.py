#!/usr/bin/env python3
"""Terraform outputs of the EKS root → deploy/helm/umbrella/values-eks.generated.yaml (gitignored; `make deploy-eks`).

    terraform -chdir=deploy/terraform/eks output -json > artifacts/tf-outputs-eks.json
    uv run python scripts/render_values.py [--tag <image tag>] [--outputs <file>] [--out <file>]

The file goes on top of values-eks.yaml (`-f values-eks.yaml -f values-eks.generated.yaml`) and carries only what
Terraform knows: the ECR repositories, the image tag, region, table prefix, both KMS key ARNs, the SNS topic, the
IRSA role per service account, the ALB hosts / ACM certificate / public subnets, the VPC CIDR the ALB reaches the
pods from, and the Bedrock model. Nothing in it is a secret (the chart generates its own Secret in-cluster).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _tf_outputs import ROOT, load_outputs  # noqa: E402

DEFAULT_OUT = ROOT / "deploy" / "helm" / "umbrella" / "values-eks.generated.yaml"
SERVICES = ["mock-carrier", "tower-mcp", "binding-page", "alerts", "ref-client"]
IRSA = ["tower-mcp", "binding-page", "alerts", "ref-client"]  # service-account name = chart name
ALB = "alb.ingress.kubernetes.io/"


def _url(host: str) -> str:
    return f"https://{host}" if host else ""


def ingress(host: str, cert: str, subnets: list[str]) -> dict[str, Any]:
    ann: dict[str, str] = {}
    if cert:
        ann[ALB + "certificate-arn"] = cert
    else:  # no ACM certificate yet: plain HTTP. CAMARA sinks and the bind page then do not work; say so.
        ann[ALB + "listen-ports"] = '[{"HTTP":80}]'
    if subnets:
        ann[ALB + "subnets"] = ",".join(subnets)
    out: dict[str, Any] = {"annotations": ann}
    if host:
        out["host"] = host
    return out


def render(o: dict[str, Any], tag: str) -> dict[str, Any]:
    missing = [
        k
        for k in ("region", "ecr_repositories", "irsa_role_arns", "kms_key_arn", "kms_hmac_key_arn")
        if not o.get(k)
    ]
    if missing:
        raise SystemExit(
            f"terraform outputs lack {', '.join(missing)} (is this the deploy/terraform/eks root?)"
        )
    repos: dict[str, str] = o["ecr_repositories"]
    roles: dict[str, str] = o["irsa_role_arns"]
    cert = str(o.get("certificate_arn") or "")
    subnets = [str(s) for s in o.get("public_subnet_ids") or []]
    tower_host, binding_host = str(o.get("tower_host") or ""), str(o.get("binding_host") or "")
    hooks_host = str(o.get("hooks_host") or "") or tower_host
    sns = o.get("sns_topic_arns") or {}
    sns_arn = str(sns.get("replies", "")) if isinstance(sns, dict) else ""
    binding_url = str(o.get("binding_url") or "") or _url(binding_host)
    values: dict[str, Any] = {
        "global": {
            "imageTag": tag,
            "storeEnv": {
                "AWS_REGION": str(o["region"]),
                "TOWER_TABLE_PREFIX": str(o.get("table_prefix") or ""),
                "TOWER_KMS_KEY_ID": str(o["kms_key_arn"]),
                "TOWER_KMS_HMAC_KEY_ID": str(o["kms_hmac_key_arn"]),
            },
        },
    }
    for svc in SERVICES:
        if svc not in repos:
            raise SystemExit(f"ecr_repositories has no {svc!r}")
        values[svc] = {"image": {"registry": "", "repository": repos[svc]}}
    for svc in IRSA:
        if svc not in roles:
            raise SystemExit(f"irsa_role_arns has no {svc!r}")
        values[svc]["serviceAccount"] = {"annotations": {"eks.amazonaws.com/role-arn": roles[svc]}}
    values["tower-mcp"]["ingress"] = ingress(tower_host, cert, subnets)
    values["tower-mcp"]["env"] = {"BINDING_BASE_URL": binding_url}
    values["binding-page"]["ingress"] = ingress(binding_host, cert, subnets)
    values["binding-page"]["env"] = {
        "BASE_URL": binding_url,
        "BIND_REDIRECT_URI": f"{binding_url}/bind/callback" if binding_url else "",
    }
    values["alerts"]["ingress"] = ingress(hooks_host, cert, subnets)
    values["alerts"]["env"] = {
        "HOOKS_BASE_URL": str(o.get("hooks_base_url") or "") or _url(hooks_host),
        "SNS_TOPIC_ARN": sns_arn,
    }
    if o.get("vpc_cidr"):
        values["alerts"]["networkPolicy"] = {"from": {"cidrs": [str(o["vpc_cidr"])]}}
    values["ref-client"]["env"] = {
        "AWS_REGION": str(o["region"]),
        "BEDROCK_MODEL_ID": str(o.get("bedrock_model_id") or "amazon.nova-micro-v1:0"),
    }
    return values


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--outputs", type=Path, default=None, help="terraform output -json of deploy/terraform/eks"
    )
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument(
        "--tag", default="latest", help="image tag pushed by `make push ENV=eks` (default latest)"
    )
    args = ap.parse_args(argv)
    values = render(load_outputs(args.outputs, env="eks"), args.tag)
    header = "# generated by scripts/render_values.py from deploy/terraform/eks outputs — do not edit, do not commit\n"
    args.out.write_text(header + yaml.safe_dump(values, sort_keys=False), "utf-8")
    shown = args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out
    print(f"wrote {shown}")
    if not values["tower-mcp"]["ingress"]["annotations"].get(ALB + "certificate-arn"):
        print(
            "  warning: no certificate_arn — the ALB listens on HTTP only; CAMARA sinks need https",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
