#!/usr/bin/env python3
"""Generate docs/architecture/diagrams/12-aws-as-built.drawio — the AWS deployment as it runs after the first apply
(2026-10-13, us-east-2, AgentCore path only; EKS is not drawn). Never hand-edit the XML.

Same palette and helpers as gen_deploy.py (08): teal = the request path, amber = the proactive path, grey =
binding / management. Dashed = exercised only by the smoke test, not yet by `make demo ENV=aws`.

    uv run python docs/architecture/diagrams/gen/gen_aws_as_built.py            # write + check
    uv run python docs/architecture/diagrams/gen/gen_aws_as_built.py --check    # fail if the file is stale
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUT = HERE.parent / "12-aws-as-built.drawio"
sys.path.insert(0, str(HERE))
import check as checker  # noqa: E402
from gen_deploy import MUTED, Page  # noqa: E402


def page_as_built() -> Page:
    p = Page("aws-as-built", "AWS as built (2026-10-13)", 1420, 980)

    p.text("title", 20, 8, 900, 28, "Ask the Tower on AWS — as deployed, us-east-2, account 4715…3540",
           size=16, color="#0E1726", bold=True)
    p.text("subtitle", 20, 40, 1200, 34,
           "AgentCore path only (the EKS variant is not deployed). Teal = a question; amber = a proactive alert; "
           "grey = binding, consent and operations. Dashed = exercised by the smoke test only.")

    # --- zones ----------------------------------------------------------------------------------------------
    p.zone("z_users", 20, 80, 200, 420, "People")
    p.zone("z_edge", 260, 80, 260, 420, "Edge")
    p.zone("z_agentcore", 560, 80, 520, 420, "Amazon Bedrock AgentCore", kind="teal")
    p.zone("z_mgmt", 1120, 80, 280, 200, "Management account 3498…1437", kind="amber")
    p.zone("z_data", 1120, 320, 280, 180, "Data")
    p.zone("z_fn", 20, 540, 500, 320, "Lambda, schedules, SMS")
    p.zone("z_vpc", 560, 540, 520, 320, "VPC", kind="slate")
    p.text("vpc_note", 760, 544, 300, 20, "10.42.0.0/16 · 2 AZs · the mock carrier lives here", size=10)
    p.zone("z_ops", 1120, 540, 280, 320, "Ops: logs, registry, state")

    # --- people ---------------------------------------------------------------------------------------------
    p.box("phone", 40, 120, 160, 80, "Asish's phone",
          ["bind link, mobile data", "receives the SMS"], kind="grey")
    p.box("laptop", 40, 230, 160, 80, "Laptop browser",
          ["web chat page", "sign in as asish / mom"], kind="grey")
    p.box("ops", 40, 340, 160, 130, "Ops tab · terminal",
          ["make push · plan · deploy", "Terraform, local state", "one operator, one laptop"], kind="grey")

    # --- edge -----------------------------------------------------------------------------------------------
    p.box("cognito", 280, 120, 220, 80, "Cognito user pool",
          ["Hosted UI, PKCE client web-chat", "users: asish, mom (sub = user id)"], kind="grey")
    p.box("cf_page", 280, 230, 220, 80, "CloudFront + S3",
          ["the web chat page (static)", "config.js from the outputs"], kind="teal")
    p.box("cf_api", 280, 340, 220, 130, "CloudFront + WAF → HTTP API",
          ["/bind/* /me/* /grants/* → binding-page", "/hooks/* /internal/* → alerts",
           "/carrier/oauth2/authorize → VPC link", "(the mock's authorize page)"], kind="grey")

    # --- agentcore ------------------------------------------------------------------------------------------
    p.box("identity", 580, 120, 220, 80, "AgentCore Identity",
          ["OAuth2 credential providers:", "service (client credentials)", "binding (auth code; unused: 3LO)"],
          kind="teal")
    p.box("proxy", 580, 230, 140, 80, "Lambda URL proxy",
          ["web-chat-proxy", "CORS = the page's origin"], kind="teal")
    p.box("agent", 760, 230, 300, 80, "Runtime · web chat agent",
          ["ref-client, protocol HTTP, JWT authorizer", "REF_AGENT=bedrock · AWS_REGION=us-east-2",
           "BEDROCK_ASSUME_ROLE_ARN → management account"], kind="teal")
    p.box("gateway", 580, 340, 140, 130, "AgentCore Gateway",
          ["5 CAMARA targets", "OpenAPI → MCP tools", "IAM authorizer", "number-verification: none"],
          kind="teal")
    p.box("tower", 760, 340, 300, 130, "Runtime · Tower MCP",
          ["line_is_ok · is_reachable · watch_line", "protocol MCP, JWT authorizer (same pool)",
           "policy, consent, audit — deterministic", "AWS_REGION=us-east-2 · tools discovered"], kind="teal")

    # --- management account ---------------------------------------------------------------------------------
    p.box("role", 1140, 120, 240, 60, "IAM role ask-the-tower-bedrock",
          ["trust: stack account + aws:PrincipalArn", "allows bedrock:InvokeModel only"], kind="amber")
    p.box("nova", 1140, 200, 240, 60, "Bedrock · Amazon Nova Micro",
          ["us.amazon.nova-micro-v1:0 (inference profile)", "stack account's quota is 0 → served here"],
          kind="amber")

    # --- data -----------------------------------------------------------------------------------------------
    p.box("ddb", 1140, 360, 240, 70, "DynamoDB × 7",
          ["Users Lines Grants Watches", "BindTokens AlertsState Audit"], kind="grey")
    p.box("kms", 1140, 440, 240, 44, "KMS × 2", ["main (encrypt) · HMAC (line ids)"], kind="grey")

    # --- lambdas --------------------------------------------------------------------------------------------
    p.box("binding", 40, 580, 220, 90, "Lambda · binding-page",
          ["in the VPC (private subnets)", "DirectClient → number-verification", "auth code exchange itself"],
          kind="grey")
    p.box("alerts", 280, 580, 220, 90, "Lambda · alerts",
          ["SMS via SNS (sandbox number)", "carrier hooks, escalation", "Gateway for subscriptions"], kind="amber")
    p.box("reconcile", 40, 690, 220, 60, "Lambda · reconcile", ["audit reconcile, nightly"], kind="grey")
    p.box("sched", 280, 690, 220, 60, "EventBridge Scheduler",
          ["6 schedules, America/Toronto"], kind="amber")
    p.box("sns", 40, 770, 460, 70, "SNS",
          ["topic sms-replies → alerts · SMS sandbox: one verified number (+1 613 …)"], kind="amber")

    # --- vpc ------------------------------------------------------------------------------------------------
    p.box("alb", 580, 580, 220, 90, "Internal ALB + ACM",
          ["mock.askthetower.xyz", "Route 53 zone (domain at Porkbun)", "HTTPS from inside the VPC only"],
          kind="grey")
    p.box("mock", 840, 580, 220, 90, "Fargate · mock carrier",
          ["CAMARA APIs, OAuth2 server", "admin API (bearer; closed)", "seeded by make seed-aws (ECS exec)"],
          kind="grey")
    p.box("nat", 580, 700, 220, 70, "NAT gateway · public subnets",
          ["egress for the VPC Lambda", "endpoints: S3, DynamoDB"], kind="grey")
    p.box("vpclink", 840, 700, 220, 70, "API Gateway VPC link",
          ["/carrier/oauth2/authorize → ALB", "the phone's one hop into the VPC"], kind="grey")

    # --- ops ------------------------------------------------------------------------------------------------
    p.box("logs", 1140, 580, 240, 70, "CloudWatch",
          ["runtime + Lambda log groups", "dashboard, alarms, metric filters"], kind="grey")
    p.box("ecr", 1140, 670, 240, 70, "ECR × 5",
          ["tag = git sha (+ latest)", "lifecycle: one image per repo"], kind="grey")
    p.box("state", 1140, 760, 240, 80, "Terraform state (local)",
          ["backend \"local\" — the account's SCP", "denies the S3 bucket and lock table",
           "region pinned to us-east-2 by SCP"], kind="grey")

    # --- edges: the question path (teal) ---------------------------------------------------------------------
    p.edge("e_login", "laptop", "cognito", [(200, 250), (240, 250), (240, 160), (280, 160)],
           "sign in (PKCE)", kind="grey", at=(240, 205))
    p.edge("e_page", "laptop", "cf_page", [(200, 280), (280, 280)], kind="teal")
    p.edge("e_ask", "cf_page", "proxy", [(500, 265), (580, 265)], "POST + JWT", kind="teal", at=(540, 255))
    p.edge("e_proxy", "proxy", "agent", [(720, 270), (760, 270)], kind="teal")
    p.edge("e_model", "agent", "nova", [(1060, 270), (1100, 270), (1100, 230), (1140, 230)],
           "Converse", kind="teal", at=(1100, 250), dashed=True)
    p.edge("e_assume", "agent", "role", [(1060, 250), (1090, 250), (1090, 150), (1140, 150)],
           "sts:AssumeRole", kind="grey", at=(1090, 200), dashed=True)
    p.edge("e_tower", "agent", "tower", [(910, 310), (910, 340)], "MCP + the same JWT", kind="teal",
           at=(910, 325))
    p.edge("e_gw", "tower", "gateway", [(760, 400), (720, 400)], kind="teal")
    p.edge("e_ident", "gateway", "identity", [(720, 360), (740, 360), (740, 200)], "token", kind="grey", at=(740, 280))
    p.edge("e_alb", "gateway", "alb", [(660, 470), (660, 580)], "HTTPS, managed VPC resource", kind="teal",
           at=(660, 525))
    p.edge("e_mock", "alb", "mock", [(800, 625), (840, 625)], kind="teal")
    p.edge("e_ddb", "tower", "ddb", [(1060, 380), (1100, 380), (1100, 395), (1140, 395)],
           "consent, audit", kind="grey", at=(1100, 365))
    p.edge("e_kms", "tower", "kms", [(1060, 440), (1090, 440), (1090, 462), (1140, 462)], kind="grey")

    # --- edges: binding (grey) and proactive (amber) ------------------------------------------------------------
    p.edge("e_bind", "phone", "cf_api", [(200, 150), (230, 150), (230, 405), (280, 405)],
           "bind link", kind="grey", at=(230, 330))
    p.edge("e_bindfn", "cf_api", "binding", [(390, 470), (390, 520), (270, 520), (270, 600), (260, 600)],
           "/bind/*", kind="grey", at=(330, 520))
    p.edge("e_authz", "cf_api", "vpclink", [(500, 455), (540, 455), (540, 800), (950, 800), (950, 770)],
           "authorize page via VPC link", kind="grey", at=(700, 800))
    p.edge("e_numver", "binding", "alb", [(250, 580), (250, 530), (625, 530), (625, 580)],
           "number-verification (direct, auth code)", kind="grey", at=(480, 530))
    p.edge("e_hooks", "mock", "alerts", [(950, 670), (950, 690), (520, 690), (520, 610), (500, 610)],
           "hooks (SIM swap, forwarding)", kind="amber", at=(735, 690))
    p.edge("e_sms", "alerts", "sns", [(290, 670), (290, 680), (270, 680), (270, 770)], kind="amber")
    p.edge("e_buzz", "sns", "phone", [(40, 805), (10, 805), (10, 170), (40, 170)],
           "SMS", kind="amber", at=(10, 480))
    p.edge("e_sched", "sched", "alerts", [(390, 690), (390, 670)], kind="amber")
    p.edge("e_recon", "sched", "reconcile", [(280, 720), (260, 720)], kind="grey")
    p.edge("e_ops", "ops", "ecr", [(120, 470), (120, 510), (1100, 510), (1100, 705), (1140, 705)],
           "push · plan · deploy", kind="grey", at=(700, 510))

    # --- notes ----------------------------------------------------------------------------------------------
    p.text("note", 20, 880, 1380, 80,
           "First-apply facts baked in: credential providers use client_authentication_method (the old "
           "token_endpoint_auth_methods is refused); Gateway with an IAM authorizer refuses 3LO targets, so "
           "number-verification is not registered and the binding page does its own auth-code exchange "
           "(binding_carrier_client = \"direct\", which needs the NAT); AgentCore creates the runtime log groups "
           "itself; Runtime does not inject AWS_REGION; on-demand Nova needs the us.* inference profile; a new "
           "account's Bedrock daily token quota is 0, hence the cross-account role.",
           size=10, color=MUTED, valign="top")
    return p


def build() -> str:
    mx = ET.Element("mxfile", host="app.diagrams.net", agent="Ask the Tower design generator", type="device")
    mx.append(page_as_built().element())
    ET.indent(mx, space="  ")
    return ET.tostring(mx, encoding="unicode") + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if the file on disk differs from the generator")
    args = ap.parse_args()
    text = build()
    if args.check:
        if not OUT.exists() or OUT.read_text("utf-8") != text:
            print(f"{OUT.name} is stale: run gen_aws_as_built.py")
            return 1
    else:
        OUT.write_text(text, "utf-8")
        print(f"wrote {OUT.relative_to(ROOT)}")
    issues = checker.check(OUT)
    for i in issues:
        print("  " + i)
    print(f"check: {len(issues)} issue(s)")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
