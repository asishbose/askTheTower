#!/usr/bin/env python3
"""Generate docs/architecture/diagrams/08-agentcore-deployment.drawio (three pages) — never hand-edit the XML.

Palette and mxGraph conventions match 01–07 (agent "Ask the Tower design generator"): teal #2BB3A0 request path,
amber #E8A33D proactive path, grey #6B7583 binding / management, ink #0E1726; zone fills #EAF7F4 / #E4E9F2 /
#FBF3E0 / #EEF0F5; box fills #DDF3EF / #FBEFD9 / #F3F5F8. Dashed border = deferred or unverified in
docs/submission/build-log.md (with its TODO(human) reference); pale grey = intended or alternative, not deployed.

Every edge is given as an absolute polyline (first point on the source border, last on the target border, every
corner listed), so check.py can verify that no edge runs through a box and no label sits on a box.

    uv run python docs/architecture/diagrams/gen/gen_deploy.py            # write + check
    uv run python docs/architecture/diagrams/gen/gen_deploy.py --check    # fail if the file is stale
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUT = HERE.parent / "08-agentcore-deployment.drawio"
sys.path.insert(0, str(HERE))
import check as checker  # noqa: E402

FONT = "#1B2B45"
MUTED = "#6B7A90"
GHOST_FONT = "#8A96A8"
ZONE = {
    "teal": ("#EAF7F4", "#2BB3A0"),
    "slate": ("#E4E9F2", "#0E1726"),
    "amber": ("#FBF3E0", "#E8A33D"),
    "grey": ("#EEF0F5", "#9AA7B5"),
    "white": ("#FFFFFF", "#0E1726"),
    "ghost": ("#FFFFFF", "#C9D3DF"),
}
BOX = {
    "teal": ("#DDF3EF", "#2BB3A0"),
    "amber": ("#FBEFD9", "#E8A33D"),
    "grey": ("#F3F5F8", "#6B7583"),
    "white": ("#FFFFFF", "#6B7583"),
    "ghost": ("#F7F8FA", "#C9D3DF"),
    "head": ("#E4E9F2", "#0E1726"),
}
EDGE = {"teal": ("#2BB3A0", 2), "amber": ("#E8A33D", 2), "grey": ("#6B7583", 1), "ghost": ("#B8C2CF", 1)}


def esc(s: str) -> str:
    return html.escape(s, quote=False)


class Page:
    def __init__(self, pid: str, name: str, w: int, h: int) -> None:
        self.pid, self.name, self.w, self.h = pid, name, w, h
        self.cells: list[ET.Element] = []
        self.geo: dict[str, tuple[float, float, float, float]] = {}

    # --- vertices ---------------------------------------------------------------------------------------------
    def _vertex(self, cid: str, x: float, y: float, w: float, h: float, value: str, style: str) -> None:
        assert cid not in self.geo, cid
        c = ET.Element("mxCell", id=cid, value=value, style=style, vertex="1", parent="1")
        ET.SubElement(c, "mxGeometry", x=f"{x:g}", y=f"{y:g}", width=f"{w:g}", height=f"{h:g}", **{"as": "geometry"})
        self.cells.append(c)
        self.geo[cid] = (x, y, w, h)

    def text(self, cid: str, x: float, y: float, w: float, h: float, value: str, size: int = 11,
             color: str = MUTED, bold: bool = False, valign: str = "middle") -> None:  # fmt: skip
        style = (
            f"text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign={valign};whiteSpace=wrap;"
            f"fontSize={size};fontColor={color};fontStyle={1 if bold else 0};spacing=4;"
        )
        self._vertex(cid, x, y, w, h, value, style)

    def zone(self, cid: str, x: float, y: float, w: float, h: float, label: str, kind: str = "grey",
             dashed: bool = False, size: int = 14) -> None:  # fmt: skip
        fill, stroke = ZONE[kind]
        color = GHOST_FONT if kind == "ghost" else FONT
        style = (
            f"rounded=1;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};verticalAlign=top;align=left;"
            f"spacingLeft=10;spacingTop=4;spacing=2;fontStyle=1;fontSize={size};fontColor={color};"
            f"dashed={int(dashed)};arcSize=12;absoluteArcSize=1;"
        )
        self._vertex(cid, x, y, w, h, label, style)

    def box(self, cid: str, x: float, y: float, w: float, h: float, title: str, body: list[str] | None = None,
            kind: str = "grey", dashed: bool = False, align: str = "left", size: int = 11, body_size: int = 9,
            raw: bool = False) -> None:  # fmt: skip
        fill, stroke = BOX[kind]
        color = GHOST_FONT if kind == "ghost" else FONT
        t = title if raw else esc(title)
        value = f"<b>{t}</b>" if title else ""
        if body:
            lines = "<br>".join(line if raw else esc(line) for line in body)
            value += ("<br>" if title else "") + f'<font style="font-size:{body_size}px">{lines}</font>'
        style = (
            f"rounded=1;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};fontSize={size};"
            f"fontColor={color};align={align};verticalAlign=middle;spacing=4;spacingLeft=6;spacingRight=6;"
            f"dashed={int(dashed)};arcSize=10;absoluteArcSize=1;"
        )
        self._vertex(cid, x, y, w, h, value, style)

    # --- edges ------------------------------------------------------------------------------------------------
    def edge(self, cid: str, src: str, tgt: str, path: list[tuple[float, float]], label: str = "",
             kind: str = "grey", dashed: bool = False, at: tuple[float, float] | None = None,
             size: int = 9) -> None:  # fmt: skip
        sx, sy, sw, sh = self.geo[src]
        tx, ty, tw, th = self.geo[tgt]
        (x0, y0), (x1, y1) = path[0], path[-1]
        ex, ey = (x0 - sx) / sw, (y0 - sy) / sh
        nx, ny = (x1 - tx) / tw, (y1 - ty) / th
        for v, what in ((ex, "exitX"), (ey, "exitY"), (nx, "entryX"), (ny, "entryY")):
            assert -0.001 <= v <= 1.001, f"{cid}: {what}={v:.3f} — path endpoint not on the box"
        color, width = EDGE[kind]
        style = (
            f"edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;strokeColor={color};strokeWidth={width};"
            f"fontSize={size};fontColor=#3C4858;dashed={int(dashed)};endArrow=block;endFill=1;"
            f"labelBackgroundColor=#FFFFFF;jumpStyle=arc;jumpSize=8;"
            f"exitX={ex:.4f};exitY={ey:.4f};exitDx=0;exitDy=0;entryX={nx:.4f};entryY={ny:.4f};entryDx=0;entryDy=0;"
        )
        c = ET.Element("mxCell", id=cid, value=label, style=style, edge="1", parent="1", source=src, target=tgt)
        rel, off = 0.0, (0.0, 0.0)
        if label and at is not None:
            rel, off = _label_position(path, at)
        g = ET.SubElement(c, "mxGeometry", relative="1", **{"as": "geometry"})
        if rel:
            g.set("x", f"{rel:.4f}")
        if len(path) > 2:
            arr = ET.SubElement(g, "Array", **{"as": "points"})
            for px, py in path[1:-1]:
                ET.SubElement(arr, "mxPoint", x=f"{px:g}", y=f"{py:g}")
        if off != (0.0, 0.0):
            ET.SubElement(g, "mxPoint", x=f"{off[0]:g}", y=f"{off[1]:g}", **{"as": "offset"})
        self.cells.append(c)

    def element(self) -> ET.Element:
        d = ET.Element("diagram", id=self.pid, name=self.name)
        m = ET.SubElement(
            d, "mxGraphModel", dx="1422", dy="800", grid="1", gridSize="10", guides="1", tooltips="1", connect="1",
            arrows="1", fold="1", page="1", pageScale="1", pageWidth=str(self.w), pageHeight=str(self.h), math="0",
            shadow="0",
        )  # fmt: skip
        r = ET.SubElement(m, "root")
        ET.SubElement(r, "mxCell", id="0")
        ET.SubElement(r, "mxCell", id="1", parent="0")
        for c in self.cells:
            r.append(c)
        return d


def _label_position(path: list[tuple[float, float]], at: tuple[float, float]) -> tuple[float, tuple[float, float]]:
    """drawio edge-label geometry: x in [-1, 1] along the path, plus an offset to land exactly on `at`."""
    segs = list(zip(path, path[1:], strict=False))
    lengths = [abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in segs]
    total = sum(lengths) or 1.0
    best = (1e18, 0.0, path[0])
    run = 0.0
    for (a, b), L in zip(segs, lengths, strict=False):
        if L:
            r = max(0.0, min(1.0, ((at[0] - a[0]) * (b[0] - a[0]) + (at[1] - a[1]) * (b[1] - a[1])) / (L * L)))
            p = (a[0] + (b[0] - a[0]) * r, a[1] + (b[1] - a[1]) * r)
            d = (p[0] - at[0]) ** 2 + (p[1] - at[1]) ** 2
            if d < best[0]:
                best = (d, (run + r * L) / total, p)
        run += L
    _, frac, p = best
    return frac * 2 - 1, (round(at[0] - p[0]), round(at[1] - p[1]))


# =====================================================================================================================
# Page 1 — target state after `make deploy`
# =====================================================================================================================


WEB_BAND = 290  # height of the web chat band (prompt 20) inserted above ECR on page 1


def page_target() -> Page:
    DY = WEB_BAND  # everything below the web chat band moves down by its height
    p = Page("08_target_state", "1 Target state", 2920, 2260 + DY)
    p.text("t1", 30, 14, 2860, 32, "Ask the Tower on Amazon Bedrock AgentCore — the AWS account after <code>make deploy</code> (as the repo builds it today)", size=20, color=FONT, bold=True)  # fmt: skip
    p.text("t2", 30, 46, 2860, 22, "Every box names the Terraform module / resource that creates it and the image it runs (tag = git sha from <code>make push</code>). Teal = request path (Alexa+ → Runtime → Gateway → mock; web chat W1–W5), amber = proactive path (mock → API → Alerts → SNS), grey = binding, data and management, dashed = deploy-time or deferred, pale = intended or optional, not deployed by default.", size=11)  # fmt: skip
    p.text("t3", 30, 68, 2860, 22, "Status: written and <code>terraform validate</code>-clean; never planned or applied (docs/submission/build-log/13.md). Region us-east-1, name prefix <code>att-dev</code> (name_prefix + environment), tag <code>project=ask-the-tower</code> on everything.", size=11)  # fmt: skip

    # --- zones ------------------------------------------------------------------------------------------------
    p.zone("z_ext", 30, 110, 300, 1750, "External", "grey")
    p.zone("z_aws", 360, 110, 2230, 1700 + DY, "AWS account (us-east-1) — main Terraform root deploy/terraform, state key ask-the-tower/aws/terraform.tfstate", "white")  # fmt: skip
    p.zone("z_ext2", 2620, 110, 270, 1460, "External (alternative)", "ghost")
    p.zone("z_ac", 380, 150, 2190, 370, "Bedrock AgentCore — Runtime · Gateway · Identity · Observability", "teal")
    p.zone("z_data", 380, 550, 2190, 190, "Data", "grey")
    p.zone("z_comp", 380, 770, 2190, 520, "Compute — Lambda · API Gateway · Fargate", "slate")  # fmt: skip
    p.zone("z_vpc", 1360, 805, 1190, 440, "VPC — modules/network", "white", size=12)
    p.zone("z_msg", 380, 1320, 940, 245, "Messaging / scheduling — SNS · EventBridge Scheduler", "amber")
    p.zone("z_manual", 1360, 1320, 1190, 245, "In the account, not created by this Terraform (by hand, per the runbooks)", "ghost", dashed=True)  # fmt: skip
    p.zone("z_web", 380, 1590, 2190, 260, "Web chat (prompt 20, 09 §6) — modules/cognito · modules/web_chat; the agent runtime sits in AgentCore above", "teal")  # fmt: skip
    p.zone("z_ecr", 380, 1590 + DY, 2190, 200, "ECR — own Terraform root deploy/terraform/ecr, state key ask-the-tower/ecr/terraform.tfstate (survives make down)", "grey")
    p.zone("z_dev", 30, 1840 + DY, 2860, 230, "Developer machine (WSL2) — make, terraform, docker buildx, uv run scripts/", "grey")  # fmt: skip

    # --- external -------------------------------------------------------------------------------------------
    p.box("alexa", 50, 230, 230, 160, "Alexa+ MCP Toolkit", [
        "web simulator · Amazon's cloud, not ours",
        "MCP client; phrases results; never speaks first",
        "registered with the output tower_mcp_url",
        "+ OAuth account linking (Cognito)",
        "TODO(human): alexa/registration.md §2–§6",
    ], "teal", dashed=True)  # fmt: skip
    p.box("phone", 50, 820, 230, 150, "Line-holder's phone", [
        "mobile data, Wi-Fi off (rule 7)",
        "opens the binding link and the carrier",
        "authorize page; one tap",
        "TODO(human): §2.6 real-phone run",
    ], "grey", dashed=True)  # fmt: skip
    p.box("watcher", 50, 1380, 230, 140, "Watcher's phone", [
        "receives the templated SMS",
        "never the number just swapped",
        "must be a verified SNS sandbox number",
    ], "amber")  # fmt: skip
    p.box("sandbox", 2640, 820, 230, 170, "Carrier sandbox", [
        "carrier_backend = \"sandbox\":",
        "mock_carrier count = 0; Gateway",
        "targets' servers[0] = sandbox_base_url;",
        "Identity holds sandbox_client_id and",
        "TF_VAR_sandbox_client_secret",
        "(public HTTPS, no VPC resource)",
    ], "ghost", dashed=True)  # fmt: skip

    # --- AgentCore --------------------------------------------------------------------------------------------
    p.box("runtime", 400, 200, 320, 295, "AgentCore Runtime — Tower MCP server", [
        "modules/agentcore_runtime",
        "aws_bedrockagentcore_agent_runtime.tower",
        "image att-dev/tower-mcp:<git sha> (linux/arm64)",
        "server_protocol MCP · :8000/mcp, stateless",
        "network_mode PUBLIC (VPC on the cut line)",
        "inbound: custom_jwt_authorizer (Cognito",
        "discovery URL); Authorization header",
        "allow-listed, Tower re-verifies, sub → user_id",
        "aws_iam_role.runtime: ECR pull, logs, X-Ray,",
        "DynamoDB, KMS, InvokeGateway",
        "aws_cloudwatch_log_group.tower",
        "output tower_mcp_url (?qualifier=DEFAULT)",
    ], "teal")  # fmt: skip
    p.box("gateway", 860, 200, 320, 295, "AgentCore Gateway — CAMARA as MCP tools", [
        "modules/agentcore_gateway",
        "aws_bedrockagentcore_gateway.this",
        "authorizer_type AWS_IAM · protocol MCP",
        "6 × aws_bedrockagentcore_gateway_target",
        ".camara — one per specs/camara/*.yaml,",
        "inline OpenAPI, servers → carrier URL",
        "tools <target>___<operationId>",
        "outbound OAuth: CLIENT_CREDENTIALS;",
        "AUTHORIZATION_CODE (number-verification)",
        "private_endpoint: managed VPC resource",
        "aws_iam_role.gateway",
        "outputs gateway_url, gateway_target_ids",
    ], "teal")  # fmt: skip
    p.box("identity", 1420, 200, 340, 295, "AgentCore Identity — outbound carrier OAuth", [
        "modules/agentcore_identity",
        "2 × aws_bedrockagentcore_oauth2_credential_",
        "provider.this (CustomOauth2):",
        "· service — client credentials, client \"tower\"",
        "· binding — auth code, client \"binding-page\"",
        "client_secret_basic; token URL",
        "https://<mock_domain_name>/oauth2/token",
        "private_endpoint: managed VPC resource",
        "aws_bedrockagentcore_workload_identity",
        ".binding (return URL …/bind/callback)",
        "the only holder of carrier secrets",
    ], "teal")  # fmt: skip
    p.box("obs", 1820, 200, 340, 295, "Observability — CloudWatch", [
        "modules/observability",
        "aws_cloudwatch_dashboard.this",
        "(p95 per tool, alerts, reconcile misses)",
        "aws_cloudwatch_log_metric_filter.alerts,",
        ".line_id_on_metric (guard)",
        "aws_cloudwatch_metric_alarm ×3",
        "traces: aws/spans via CloudWatch",
        "Transaction Search + ADOT in Tower —",
        "both by hand: TODO(human) build-log/13",
        "no metric carries line_id",
    ], "grey")  # fmt: skip
    p.box("refclient", 2200, 200, 340, 295, "AgentCore Runtime — web chat agent", [
        "aws_bedrockagentcore_agent_runtime.ref_client",
        "(enable_web_chat, default true)",
        "image att-dev/ref-client:<git sha>, HTTP app",
        "server_protocol HTTP · :8080 POST /invocations,",
        "GET /ping (09 §6)",
        "inbound: custom_jwt_authorizer (Cognito,",
        "allowed_clients web-chat); Authorization",
        "allow-listed and forwarded to Tower unchanged",
        "aws_iam_role.agent: Bedrock invoke + logs only",
        "(no DynamoDB, KMS or Gateway — 09 §5)",
        "env TOWER_URL, WEB_CHAT_BINDING_BASE_URL",
        "TODO(human) build-log/20 · F9 being resolved",
    ], "teal", dashed=True)  # fmt: skip

    # --- data -------------------------------------------------------------------------------------------------
    p.box("ddb", 400, 595, 560, 125, "DynamoDB on-demand — 7 tables", [
        "modules/dynamodb → aws_dynamodb_table.this (for_each over tables.auto.tfvars.json)",
        "att-dev-Users · Lines · Grants · Watches · Audit (PITR) · BindTokens (TTL) · AlertsState (TTL)",
        "SSE with aws_kms_key.main · keys are HMAC line_id, never a number",
    ], "grey")  # fmt: skip
    p.box("tfsecrets", 1085, 595, 180, 125, "Generated secrets", [
        "random_password",
        ".carrier_client ×3,",
        ".internal_bearer,",
        ".session_secret — in",
        "encrypted S3 state",
    ], "white")  # fmt: skip
    p.box("secrets", 1840, 595, 320, 125, "Secrets Manager", [
        "aws_secretsmanager_secret.registry",
        "(modules/mock_carrier): the mock's own",
        "client registry + JWT secret; recovery 0 d",
        "nothing carrier-side the consumer holds",
    ], "grey")  # fmt: skip
    p.box("kms", 2200, 595, 340, 125, "KMS — two keys", [
        "modules/kms → aws_kms_key.main (symmetric,",
        "rotation on: msisdn_enc, logs, SNS, tables)",
        "aws_kms_key.hmac (HMAC_256: line_id)",
        "aliases; key policy: Runtime + 3 Lambda roles",
    ], "grey")  # fmt: skip

    # --- compute ----------------------------------------------------------------------------------------------
    p.box("cf", 400, 830, 220, 140, "CloudFront + WAF", [
        "aws_cloudfront_distribution.this",
        "aws_wafv2_web_acl.edge",
        "(rate rule on /hooks/*)",
        "modules/lambdas",
        "= public_base_url",
    ], "grey")  # fmt: skip
    p.box("api", 680, 830, 260, 200, "API Gateway HTTP API", [
        "aws_apigatewayv2_api.this, stage",
        "$default → binding-page",
        "ANY /hooks/{proxy+} → alerts",
        "ANY /internal/{proxy+} → alerts",
        "GET /carrier/oauth2/authorize",
        "→ VPC link → mock ALB",
        "outputs binding_url, hooks_base_url",
    ], "grey")  # fmt: skip
    p.box("l_bind", 980, 830, 320, 120, "Lambda — binding page", [
        "aws_lambda_function.fn[\"binding-page\"]",
        "image att-dev/binding-page:<sha> · arm64",
        "awslambdaric binding_page.app.handler",
        "role aws_iam_role.fn: DynamoDB, KMS, Gateway",
    ], "grey")  # fmt: skip
    p.box("l_rec", 980, 975, 320, 115, "Lambda — reconcile (nightly)", [
        "aws_lambda_function.fn[\"reconcile\"]",
        "image att-dev/alerts:<sha> (same image)",
        "tower_audit.aws.reconcile_handler",
        "Logs Insights over aws/spans; trims Audit",
    ], "grey")  # fmt: skip
    p.box("l_alerts", 980, 1115, 320, 115, "Lambda — alerts", [
        "aws_lambda_function.fn[\"alerts\"]",
        "image att-dev/alerts:<sha> · arm64",
        "alerts.handler.lambda_handler: hooks,",
        "/internal/watch, polls, ticks, SMS replies",
    ], "amber")  # fmt: skip
    p.box("alb", 1420, 850, 340, 170, "Internal ALB — mock carrier front", [
        "modules/mock_carrier → aws_lb.this (internal)",
        "aws_lb_listener.this HTTPS :443 →",
        "aws_lb_target_group.this HTTP :8443",
        "aws_acm_certificate.this (DNS-validated)",
        "aws_route53_record.alb (mock_domain_name)",
        "forwards CAMARA paths, /oauth2/*,",
        "/.well-known/*, /healthz — never /_admin/*",
    ], "teal")  # fmt: skip
    p.box("mock", 1840, 850, 320, 170, "ECS Fargate — mock carrier", [
        "aws_ecs_cluster.this · aws_ecs_service.this",
        "(1 task, enable_execute_command)",
        "aws_ecs_task_definition.this ARM64",
        "0.25 vCPU / 0.5 GB · :8443",
        "image att-dev/mock-carrier:<sha>",
        "MOCK_SCENARIO=demo, MOCK_ADMIN=1",
        "public subnet + public IP (no NAT)",
    ], "teal")  # fmt: skip
    p.box("vpclink", 1420, 1080, 340, 140, "VPC link + egress SG", [
        "aws_apigatewayv2_vpc_link.this",
        "(only GET /carrier/oauth2/authorize)",
        "aws_security_group.agentcore_egress —",
        "used by the Gateway / Identity managed",
        "VPC resources and the cut-line Runtime",
    ], "grey")  # fmt: skip
    p.box("net", 1840, 1080, 320, 140, "Network", [
        "aws_vpc.this · 2 public + 2 private subnets",
        "aws_internet_gateway.this",
        "aws_vpc_endpoint.gateway (S3, DynamoDB)",
        "aws_nat_gateway.this only if",
        "enable_nat_gateway (off; cut line needs it)",
    ], "grey")  # fmt: skip

    # --- messaging --------------------------------------------------------------------------------------------
    p.box("sched", 400, 1365, 520, 170, "EventBridge Scheduler", [
        "modules/scheduler → aws_scheduler_schedule_group.this, aws_iam_role.scheduler",
        "aws_scheduler_schedule.this ×6, flexible window off:",
        "poll-transplant rate(5 min) · poll-care rate(30 min) → alerts {profile}",
        "daily-self, daily-care cron 08:00 (alerts_timezone) → alerts {profile}",
        "escalation-tick rate(5 min) → alerts {action: tick}",
        "reconcile cron 03:00 → reconcile {scheduled_time}",
    ], "amber")  # fmt: skip
    p.box("sns", 960, 1365, 340, 170, "SNS — SMS", [
        "modules/sns → aws_sns_sms_preferences.this",
        "(Transactional, spend cap $1/month)",
        "aws_sns_topic.replies → aws_sns_topic_",
        "subscription.replies_to_alerts (two-way)",
        "aws_sns_topic.push (optional, off)",
        "sandbox: verified numbers only",
    ], "amber")  # fmt: skip

    # --- manual ---------------------------------------------------------------------------------------------
    manual = [
        ("m_state", "S3 state + lock table", ["backend.tf commands;", "REPLACE-ME placeholders", "→ -backend-config", "holds generated secrets", "TODO(human) build-log/13"]),
        ("m_cognito", "Cognito users + Alexa client", ["make cognito-users: asish,", "mom (scripts/cognito_user.py)", "alexa-link client by CLI", "(registration.md §2)", "TODO(human) build-log/20, /15"]),
        ("m_r53", "Route 53 public zone", ["input mock_route53_zone_id", "(or mock_certificate_arn)", "ACM DNS validation;", "record → internal ALB IPs"]),
        ("m_sms", "SMS sandbox numbers", ["output sms_sandbox_commands", "create + verify with the", "OTP on each demo phone", "TODO(human) build-log/13"]),
        ("m_xray", "Transaction Search + ADOT", ["aws xray update-trace-", "segment-destination;", "Tower under opentelemetry-", "instrument (image change)", "TODO(human) build-log/13"]),
    ]  # fmt: skip
    for i, (cid, title, body) in enumerate(manual):
        p.box(cid, 1380 + i * 235, 1365, 220, 170, title, body, "ghost", dashed=True)

    # --- web chat band (prompt 20) ------------------------------------------------------------------------------
    p.box("browser", 50, 1640, 230, 140, "Browser — web chat page", [
        "laptop; Asish and Mom in two",
        "browser profiles",
        "no number field, no SMS, no QR",
        "TODO(human): build-log/20",
    ], "teal", dashed=True)  # fmt: skip
    p.box("wc_cdn", 400, 1640, 320, 140, "S3 + CloudFront — the page", [
        "modules/web_chat",
        "aws_s3_bucket (private, force_destroy)",
        "aws_cloudfront_distribution + OAC",
        "index.html · app.js · styles.css · config.js",
        "outputs web_chat_url, web_chat_bucket",
    ], "teal")  # fmt: skip
    p.box("wc_cognito", 760, 1640, 340, 140, "Amazon Cognito — modules/cognito", [
        "aws_cognito_user_pool (admin-create only)",
        "aws_cognito_user_pool_domain (Hosted UI)",
        "aws_cognito_user_pool_client web-chat:",
        "code + PKCE, no secret, callback = page URL",
        "outputs cognito_pool_id, _client_id, _issuer, …",
    ], "teal")  # fmt: skip
    p.box("wc_url", 1140, 1640, 320, 140, "Lambda function URL — proxy", [
        "modules/web_chat · zip proxy/handler.py",
        "auth NONE; CORS = the page origin",
        "forwards Authorization, Content-Type,",
        "session header to the agent runtime only",
        "output agent_url · no credential held",
    ], "teal")  # fmt: skip
    p.box("wc_note", 1500, 1725, 1040, 55, "", [
        "Not Terraform: users (make cognito-users), config.js + upload (make web-chat-sync), URL (make web-chat-url). Tower's TOWER_JWKS_URL, TOWER_JWT_ISSUER and",
        "TOWER_JWT_CLIENT_IDS come from modules/cognito. Local: compose web-chat on 127.0.0.1:8083 serves page and agent, sign-in stub, no Cognito, no proxy.",
    ], "white", size=10)  # fmt: skip
    p.edge("e_w1", "browser", "wc_cdn", [(280, 1680), (400, 1680)], "W1 GET", "teal", at=(340, 1680))
    p.edge("e_w2", "browser", "wc_cognito", [(230, 1780), (230, 1800), (930, 1800), (930, 1780)], "W2 Hosted UI sign-in · code + PKCE", "teal", at=(560, 1800))  # fmt: skip
    p.edge("e_w3", "browser", "wc_url", [(130, 1780), (130, 1830), (1300, 1830), (1300, 1780)], "W3 POST /invocations + Bearer (CORS)", "teal", at=(1000, 1830))  # fmt: skip
    p.edge("e_w4", "wc_url", "refclient", [(1460, 1690), (2580, 1690), (2580, 350), (2540, 350)], "W4 HTTPS /runtimes/{arn}/invocations · Bearer + session header", "teal", at=(1950, 1690))  # fmt: skip
    p.edge("e_w5", "refclient", "runtime", [(2250, 495), (2250, 508), (680, 508), (680, 495)], "W5 MCP · the same Bearer", "teal", at=(2030, 508))  # fmt: skip

    # --- ECR --------------------------------------------------------------------------------------------------
    p.box("ecr", 400, 1635 + DY, 900, 135, "ECR — aws_ecr_repository.service ×5 (deploy/terraform/ecr/main.tf)", [
        "att-dev/tower-mcp · att-dev/mock-carrier · att-dev/binding-page · att-dev/alerts · att-dev/ref-client",
        "aws_ecr_lifecycle_policy.service (keep the last 10) · scan on push · AES256 · force_delete = true",
        "tags <git sha> + latest, single-manifest linux/arm64; pulled by Runtime, Lambda ×3 and the ECS task",
        "make ecr-up creates (once) · make down ENV=aws keeps · make down-all is the only deleter",
    ], "grey")  # fmt: skip
    p.box("ecr_read", 1360, 1635 + DY, 700, 135, "Main root reads it — data \"aws_ecr_repository\".service", [
        "for_each over the five services, by name <name_prefix>-<environment>/<service>;",
        "local.images = <repository_url>:<image_tag> → Runtime, Lambda ×3, ECS task.",
        "Output ecr_repositories kept (push_images.py, the eks root). image_tag = the tag",
        "the last make push recorded (artifacts/image-tag); deploy verifies it in ECR first.",
    ], "white")  # fmt: skip

    # --- developer machine ------------------------------------------------------------------------------------
    dev = [
        ("d_env", 50, 400, "root .env + envs/aws.tfvars", ["mk/vars.mk exports every non-empty .env key:", "AWS keys of IAM user ask-the-tower-dev, TF_VAR_*", "envs/aws.tfvars (gitignored): mock_domain_name,", "mock_route53_zone_id, tower_jwt_*, sms numbers"]),
        ("d_make", 480, 400, "make — mk/aws.mk, mk/build.mk", ["ecr-up · ecr-outputs · plan · deploy · outputs", "register-gateway · seed-aws · latency-aws", "build · push ENV=aws · down ENV=aws · down-all", "image_tag = artifacts/image-tag on plan / deploy"]),
        ("d_buildx", 910, 400, "docker buildx (scripts/push_images.py)", ["docker buildx build --platform linux/arm64", "--provenance=false --sbom=false --push", "-t <repo>:<sha> -t <repo>:latest · QEMU binfmt", "(make build images are host-arch, not pushed)"]),
        ("d_tf", 1340, 420, "terraform ≥ 1.9 · aws ≥ 6.20, < 7 · random", ["-chdir=deploy/terraform and …/ecr (two states)", "S3 backend (backend.tf), lock table", "apply / destroy as the IAM user", "make tf-check: init -backend=false + validate"]),
        ("d_scripts", 1790, 420, "scripts/ (uv run python)", ["register_gateway.py · aws_seed.py", "render_env.py · latency.py · push_images.py", "read artifacts/tf-outputs.json (_tf_outputs.py);", "push falls back to tf-outputs-ecr.json"]),
        ("d_files", 2240, 620, "Generated files (gitignored)", ["artifacts/tf-outputs.json · tf-outputs-ecr.json · image-tag", "artifacts/tfplan · terraform-plan.txt · gateway-tools.json", "deploy/.env.aws (endpoints for ENV=aws targets, no secrets)", "artifacts/latency-aws.md"]),
    ]  # fmt: skip
    for cid, x, w, title, body in dev:
        p.box(cid, x, 1885 + DY, w, 160, title, body, "white")

    # --- edges: request path (teal) ---------------------------------------------------------------------------
    p.edge("e_alexa", "alexa", "runtime", [(280, 300), (400, 300)], "① MCP Streamable<br>HTTP + Bearer JWT", "teal", at=(340, 300))  # fmt: skip
    p.edge("e_rt_gw", "runtime", "gateway", [(720, 300), (860, 300)], "② MCP tools/call<br>SigV4 (AWS_IAM)", "teal", at=(790, 300))  # fmt: skip
    p.edge("e_gw_id", "gateway", "identity", [(1180, 300), (1420, 300)], "③ get token for target<br>(workload identity)", "teal", at=(1300, 300))  # fmt: skip
    p.edge("e_id_mock", "identity", "alb", [(1590, 495), (1590, 850)], "④ HTTPS /oauth2/token<br>client_secret_basic", "teal", at=(1590, 650))  # fmt: skip
    p.edge("e_gw_mock", "gateway", "alb", [(1180, 430), (1340, 430), (1340, 880), (1420, 880)], "⑤ HTTPS + OAuth2 bearer<br>(VPC resource)", "teal", at=(1262, 430))  # fmt: skip
    p.edge("e_alb_mock", "alb", "mock", [(1760, 935), (1840, 935)], "HTTP :8443", "teal", at=(1800, 935))

    # --- proactive path (amber) -------------------------------------------------------------------------------
    p.edge("e_hook", "mock", "cf", [(2160, 990), (2200, 990), (2200, 1272), (510, 1272), (510, 970)], "P1 CloudEvents · HTTPS POST /hooks/{kind}/{sink_token} (token in path; WAF rate rule)", "amber", at=(1850, 1272))  # fmt: skip
    p.edge("e_api_alerts", "api", "l_alerts", [(940, 1015), (960, 1015), (960, 1172), (980, 1172)], "", "amber")
    p.edge("e_sched_alerts", "sched", "l_alerts", [(905, 1365), (905, 1205), (980, 1205)], "P2 invoke<br>role", "amber", at=(905, 1335))  # fmt: skip
    p.edge("e_alerts_gw", "l_alerts", "gateway", [(1300, 1150), (1320, 1150), (1320, 470), (1180, 470)], "P3 MCP · SigV4", "amber", at=(1250, 470))  # fmt: skip
    p.edge("e_alerts_sns", "l_alerts", "sns", [(1140, 1230), (1140, 1365)], "P4 Publish<br>IAM role", "amber", at=(1140, 1338))  # fmt: skip
    p.edge("e_sms", "sns", "watcher", [(1130, 1535), (1130, 1552), (165, 1552), (165, 1520)], "SMS · templated, no number", "amber", at=(640, 1552))  # fmt: skip

    # --- binding, data, management (grey) ---------------------------------------------------------------------
    p.edge("e_phone", "phone", "cf", [(280, 890), (400, 890)], "B1 HTTPS<br>mobile data", "grey", at=(340, 890))
    p.edge("e_cf_api", "cf", "api", [(620, 890), (680, 890)], "HTTPS", "grey", at=(650, 890))
    p.edge("e_api_bind", "api", "l_bind", [(940, 890), (980, 890)], "", "grey")
    p.edge("e_api_link", "api", "vpclink", [(810, 1030), (810, 1252), (1500, 1252), (1500, 1220)], "B2 GET /carrier/oauth2/authorize", "grey", at=(1180, 1252))  # fmt: skip
    p.edge("e_link_alb", "vpclink", "alb", [(1590, 1080), (1590, 1020)], "", "grey")
    p.edge("e_bind_gw", "l_bind", "gateway", [(1060, 830), (1060, 495)], "B3 MCP · SigV4<br>auth code (Identity)", "grey", at=(1060, 535))  # fmt: skip
    p.edge("e_bind_ddb", "l_bind", "ddb", [(1000, 830), (1000, 657), (960, 657)], "", "grey")
    p.edge("e_rt_ddb", "runtime", "ddb", [(560, 495), (560, 595)], "SDK · IAM role: 1 read + audit put", "grey", at=(560, 537))  # fmt: skip
    p.edge("e_rt_alerts", "runtime", "cf", [(400, 470), (372, 470), (372, 940), (400, 940)], "Tower → Alerts<br>/internal/watch · bearer", "grey", at=(372, 538))  # fmt: skip
    p.edge("e_rt_obs", "runtime", "obs", [(560, 200), (560, 182), (1990, 182), (1990, 200)], "OTel spans + logs (ADOT)", "grey", dashed=True, at=(1600, 182))  # fmt: skip
    p.edge("e_mock_sec", "mock", "secrets", [(2000, 850), (2000, 720)], "GetSecretValue<br>(execution role)", "grey", at=(2000, 785))  # fmt: skip
    p.edge("e_sandbox", "mock", "sandbox", [(2160, 900), (2640, 900)], "or: carrier_backend = sandbox", "ghost", dashed=True, at=(2400, 900))  # fmt: skip

    # --- deploy-time (dashed grey) ----------------------------------------------------------------------------
    p.edge("e_push", "d_buildx", "ecr", [(1110, 1885 + DY), (1110, 1770 + DY)], "docker push · ECR token", "grey", dashed=True, at=(1110, 1826 + DY))  # fmt: skip
    p.edge("e_tf", "d_tf", "z_aws", [(1550, 1885 + DY), (1550, 1810 + DY)], "AWS APIs · IAM user keys", "grey", dashed=True, at=(1550, 1826 + DY))  # fmt: skip
    p.edge("e_scripts", "d_scripts", "z_aws", [(2000, 1885 + DY), (2000, 1810 + DY)], "SigV4 · ECS Exec · SDK", "grey", dashed=True, at=(2000, 1826 + DY))  # fmt: skip

    p.text("legend", 30, 2085 + DY, 2860, 160, (
        "<b>Request (teal)</b> ① Alexa+ → Runtime: MCP over Streamable HTTP, bearer JWT from Cognito account linking (Runtime's JWT authorizer, then Tower) · "
        "② Tower → Gateway: MCP tools/call signed SigV4 by the Runtime role (Gateway authorizer AWS_IAM) · ③ Gateway asks Identity for the target's token · "
        "④ Identity → mock /oauth2/token: OAuth2 client credentials over HTTPS through the managed VPC resource · ⑤ Gateway → mock: CAMARA call over HTTPS with that bearer. "
        "Tower → DynamoDB is one consent read and one audit put (SDK, Runtime role) — the hot path adds nothing else.<br>"
        "<b>Proactive (amber)</b> P1 mock → CloudFront/HTTP API → alerts Lambda: CloudEvents webhook, sink token in the path (the task egresses via its public IP) · "
        "P2 Scheduler → alerts / reconcile: Lambda invoke with the scheduler role · P3 alerts → Gateway: MCP SigV4 (facts re-fetched, consent re-checked) · P4 alerts → SNS → SMS to the watcher.<br>"
        "<b>Binding (grey)</b> B1 phone → binding page over mobile data · B2 phone → carrier authorize through the VPC link (the only public route to the mock) · "
        "B3 binding page → Gateway number-verification, auth code via the Identity binding provider.<br>"
        "<b>Web chat (teal, W)</b> W1 browser → CloudFront/S3 page · W2 Cognito Hosted UI, code + PKCE → access token · W3 page → Lambda URL (CORS) · "
        "W4 proxy → agent runtime (JWT authorizer, web-chat client) · W5 agent → Tower with the same bearer; Tower verifies it again. Companion: docs/architecture/deployment-agentcore.md."
    ), size=11)  # fmt: skip
    return p


# =====================================================================================================================
# Page 2 — install sequence
# =====================================================================================================================

LANES = [
    ("dev", "Developer (WSL2)", 110, 270, "teal"),
    ("make", "make target", 390, 200, "grey"),
    ("tf", "Terraform", 600, 330, "slate"),
    ("scripts", "scripts/", 940, 250, "grey"),
    ("aws", "AWS — what exists after", 1200, 230, "amber"),
]


def page_sequence() -> Page:
    steps: list[dict] = [
        dict(n="1", t="Credentials, state, tfvars", w=250,
             todo="TODO(human) build-log/13: state backend; fill envs/aws.tfvars",
             dev=("aws configure --profile att", ["or AWS_ACCESS_KEY_ID / _SECRET_ in", "the root .env (mk/vars.mk exports)", "aws sts get-caller-identity", "create the S3 bucket + lock table", "(commands in backend.tf)", "cp envs/aws.tfvars.example", "envs/aws.tfvars → mock_domain_name,", "mock_route53_zone_id, tower_jwt_*"]),
             make=("make config-check", ["validates the root .env, masks", "secrets; no AWS call"]),
             tf=("deploy/terraform/backend.tf", ["REPLACE-ME bucket / lock table:", "edit, or pass -backend-config", "bucket=… region=…", "dynamodb_table=… at init"]),
             aws=("S3 state bucket + lock table", ["versioned, KMS-encrypted, private", "outside Terraform; never destroyed", "by any make target"])),
        dict(n="2", t="make ecr-up", w=250,
             todo="TODO(human) build-log/13: images (validated offline, not applied)",
             dev=("make ecr-up", ["once; idempotent (re-run = no", "changes). Repos made earlier with", "-target in the main state: state mv", "first (deploy/terraform/README.md)"]),
             make=("ecr-up (mk/aws.mk)", ["TFR = terraform -chdir=", "deploy/terraform/ecr: init →", "apply $(TFR_VARS) → ecr-outputs", "→ artifacts/tf-outputs-ecr.json"]),
             tf=("ecr root: aws_ecr_repository ×5", ["+ aws_ecr_lifecycle_policy.service", "own state ask-the-tower/ecr/…;", "main root reads it by data source", "outputs ecr_repositories,", "registry, region"]),
             aws=("5 empty repositories", ["att-dev/<service>", "kept by make down ENV=aws"])),
        dict(n="3", t="make build", w=230, todo=None,
             dev=("make build", ["all five services, local Docker"]),
             make=("build → build-% (mk/build.mk)", ["docker build -f services/<svc>/", "Dockerfile -t ask-the-tower/<svc>", ":<sha> and :latest .", "host architecture"]),
             aws=("nothing", ["local images only; make push", "rebuilds for arm64, so these", "are not what AWS runs"])),
        dict(n="4", t="make push ENV=aws", w=250,
             todo="TODO(human) build-log/13: images (dry run only so far)",
             dev=("make push ENV=aws", ["needs docker buildx; QEMU", "binfmt for arm64 on amd64", "artifacts/tf-outputs-ecr.json", "from step 2 is enough"]),
             make=("push (mk/build.mk)", ["scripts/push_images.py --env aws", "--tag $(GIT_SHA); on success", "writes artifacts/image-tag", "(the script hard-codes arm64)"]),
             scripts=("scripts/push_images.py", ["aws ecr get-login-password |", "docker login <registry>", "per service: docker buildx build", "--platform linux/arm64", "--provenance=false --sbom=false", "-t <repo>:<sha> -t <repo>:latest", "--push ."]),
             aws=("10 image tags", ["5 arm64 images × (<sha>, latest)", "single manifest, no attestation", "index (Lambda/Runtime-safe)", "scan on push"])),
        dict(n="5", t="make plan", w=240,
             todo="TODO(human) build-log/13: make plan ENV=aws → review",
             dev=("make plan", ["prints the image_tag it uses:", "the tag step 4 recorded", "review artifacts/terraform-plan.txt"]),
             make=("plan (mk/aws.mk)", ["terraform init", "plan -var-file=envs/aws.tfvars", "-var=image_tag=$(IMAGE_TAG)", "-out artifacts/tfplan → show >", "artifacts/terraform-plan.txt"]),
             tf=("init + refresh + plan", ["S3 backend; aws ≥ 6.20 < 7;", "the main root; ECR read by", "data source (needs step 2)"]),
             aws=("nothing new", [])),
        dict(n="6", t="make deploy — terraform apply", w=430,
             todo="TODO(human) build-log/13: make deploy ENV=aws",
             dev=("make deploy", ["one command; chains steps 7–9", "(outputs → register-gateway →", "seed-aws → outputs)", "slow parts: CloudFront, ACM", "DNS validation, ECS service steady"]),
             make=("deploy (mk/aws.mk)", ["push_images.py --verify (tag in ECR)", "→ terraform init → apply -auto-approve", "$(TF_VARS) → make outputs → make", "register-gateway → seed-aws → outputs"]),
             tf=("apply — creation order by dependency", [
                 "1 random_password ×5 · network (VPC, subnets, IGW, endpoints, egress SG,",
                 "  VPC link) · IAM roles (Runtime, Lambda ×3, Gateway, ECS, Scheduler)",
                 "2 kms (key policy names the roles) → dynamodb (7 tables) · SNS topics",
                 "  · Secrets Manager registry",
                 "3 lambdas edge: HTTP API + CloudFront + WAF → public_base_url",
                 "4 mock_carrier: ACM cert + Route 53 validation → internal ALB + listener",
                 "  → ECS cluster, task definition, service (image …/mock-carrier:<sha>)",
                 "5 agentcore_identity: 2 OAuth2 providers (token URL = mock) +",
                 "  binding workload identity (return URL = public_base_url)",
                 "6 agentcore_gateway: Gateway → 6 OpenAPI targets (provider ARNs)",
                 "7 agentcore_runtime (gateway ARN/URL, KMS, tables) · Lambda ×3",
                 "  (gateway URL, SNS topic) · authorize route over the VPC link",
                 "8 SNS subscription · scheduler (6 schedules) · observability",
             ]),
             aws=("everything on page 1", ["Runtime DEFAULT endpoint (tower_mcp_url)", "Gateway with 6 targets; Identity providers", "mock task running scenario demo", "Lambda ×3, HTTP API, CloudFront + WAF", "7 tables (empty), 2 keys, SNS, 6 schedules"])),
        dict(n="7", t="make register-gateway", w=250,
             todo="TODO(human) build-log/13: verify the Gateway contract",
             dev=("(chained by deploy; or alone)", ["make register-gateway"]),
             make=("register-gateway (mk/aws.mk)", ["uv run python", "scripts/register_gateway.py"]),
             scripts=("scripts/register_gateway.py", ["registers nothing: Terraform made", "the targets in step 6. Lists the", "tools over MCP (SigV4) on", "gateway_url, maps them with", "resolve_tool_names → writes", "artifacts/gateway-tools.json", "(--check diffs)"]),
             aws=("no change", ["services resolve the same names", "at warm-up (CARRIER_GATEWAY_", "TOOLS=discover)"])),
        dict(n="8", t="make seed-aws", w=260,
             todo="TODO(human) build-log/13: deploy chain (dry run only so far)",
             dev=("(chained by deploy; or alone)", ["needs the AWS CLI + the", "Session Manager plugin"]),
             make=("seed-aws (mk/aws.mk)", ["uv run python scripts/aws_seed.py"]),
             scripts=("scripts/aws_seed.py", ["1 ecs list-tasks → ecs execute-", "command into the mock task:", "POST 127.0.0.1:8443/_admin/", "scenarios/load {demo}", "2 tower_mcp.seed.seed_demo over", "boto3 with KMS GenerateMac /", "Encrypt: users, two bound lines,", "Mom → Asish watch grant", "idempotent; --dry-run"]),
             aws=("demo state", ["mock reset to scenarios/demo.yaml", "Users, Lines, Grants rows", "(line_id HMAC, msisdn_enc)"])),
        dict(n="9", t="make outputs", w=250,
             todo="TODO(human) build-log/13: needs a real state",
             dev=("make outputs", ["(deploy runs it twice)"]),
             make=("outputs (mk/aws.mk)", ["terraform output -json >", "artifacts/tf-outputs.json", "render_env.py --env aws"]),
             scripts=("scripts/render_env.py", ["→ deploy/.env.aws: TOWER_URL,", "BINDING_URL, ALERTS_URL,", "CARRIER_GATEWAY_URL, TOWER_", "TABLE_PREFIX, TOWER_KMS_*, …", "no secrets; mk/vars.mk includes", "it for every ENV=aws target"]),
             aws=("no change", [])),
        dict(n="10", t="Web chat users and page", w=270,
             todo="TODO(human) build-log/20: users, seed, page upload, sign-in",
             dev=("make cognito-users", ["→ make seed-aws → make web-chat-sync", "→ make web-chat-url", "passwords: COGNITO_PASSWORD_ASISH /", "_MOM in the root .env, or a prompt;", "never written to disk", "sign in as asish and as mom", "(two browser profiles)"]),
             make=("cognito-users · web-chat-sync · web-chat-url", ["(mk/aws.mk, ENV=aws)", "read artifacts/tf-outputs.json"]),
             scripts=("scripts/cognito_user.py", ["create | delete | sub <name>:", "admin_create_user + permanent", "password → artifacts/cognito-users.json", "aws_seed.py --user name=<sub>", "(or that json): Lines, Grants", "web-chat-sync: config.js from the", "outputs, s3 sync, invalidation"]),
             aws=("web chat live", ["Cognito users asish, mom", "demo rows under their subs", "page at web_chat_url", "agent behind agent_url"])),
        dict(n="11", t="Alexa+ registration", w=260,
             todo="TODO(human) alexa/registration.md §2–§6 (build-log/15)",
             dev=("docs/architecture/alexa/registration.md", ["§2 alexa-link client on the", "Terraform pool (aws cognito-idp, CLI;", "secret + toolkit redirect URLs)", "§3 tfvars tower_jwt_allowed_clients", "+= that client → make deploy again", "§4 toolkit form: Server URL =", "tower_mcp_url, Streamable HTTP,", "OAuth account linking · §5–§6"]),
             make=("make showcase-alexa", ["simulator script + Tower log tail", "(no registration target)"]),
             tf=("re-apply", ["Runtime custom_jwt_authorizer", "+ TOWER_JWT_* environment"]),
             aws=("identity wired", ["alexa-link client on the pool", "Runtime authorizer allows it", "toolkit holds the URL + client"])),
        dict(n="12", t="Smoke", w=270,
             todo="TODO(human) build-log/13: latency, test-e2e; build-log/14: ref-client JWT",
             dev=("curl $BINDING_URL/healthz", ["MCP Inspector → TOWER_URL with", "the bearer (Tower's /healthz is", "not on the Runtime URL, which", "proxies /invocations → /mcp —", "unverified)"]),
             make=("make demo ENV=aws", ["make latency-aws", "make test-e2e ENV=aws", "(require-env: deploy/.env.aws)", "make showcase-gateway"]),
             scripts=("scripts/latency.py", ["TOWER_JWT from the root .env", "→ artifacts/latency-aws.md", "pytest tests/aws -m nightly:", "conformance fixtures through", "GatewayClient → Gateway → mock"]),
             aws=("traffic only", ["spans in aws/spans only after", "Transaction Search + ADOT", "SMS only to verified numbers"])),
        dict(n="13", t="make down ENV=aws", w=270,
             todo="TODO(human) build-log/13: make down, confirm zero except ECR",
             dev=("make down ENV=aws", ["asks first; FORCE=1 skips", "also runs compose down -v", "after judging only: make down-all", "(down-eks → down → ECR root)"]),
             make=("down (mk/aws.mk)", ["terraform init → destroy", "-auto-approve (tfvars, no", "image_tag) · compose down -v"]),
             tf=("destroy — reverse order", ["everything in the main state;", "not ECR (other root, read by a", "data source); registry secret", "(0-day), log groups; KMS keys", "→ 7-day pending deletion"]),
             aws=("what remains", ["5 ECR repositories + images,", "S3 state + lock table,", "Route 53 zone, SMS sandbox numbers,", "KMS keys (pending), AWS-made log", "groups — tag project=ask-the-tower"])),
    ]  # fmt: skip

    x0, gap = 200, 12
    width = x0 + sum(s["w"] + gap for s in steps) + 20
    p = Page("08_install_sequence", "2 Install sequence", width, 1560)
    p.text("t1", 30, 14, width - 60, 32, "Install sequence — empty account to working demo, and back (what the repo does today)", size=20, color=FONT, bold=True)  # fmt: skip
    p.text("t2", 30, 46, width - 60, 22, "One column per step, one lane per actor: the Developer types the command; make, Terraform or a script does the work; the AWS lane says what exists afterwards. Teal arrows = the order. Dashed border = deferred or unverified in docs/submission/build-log.md (the TODO(human) is in the step header).", size=11)  # fmt: skip
    p.text("t3", 30, 68, width - 60, 22, "Steps 7–9 run inside step 6 (make deploy chains them) and can be re-run alone. Inputs: credentials (root .env), Terraform outputs (artifacts/tf-outputs.json, artifacts/tf-outputs-ecr.json), envs/aws.tfvars. Companion with preconditions and rollback per step: docs/architecture/deployment-agentcore.md.", size=11)  # fmt: skip

    for lid, label, y, h, kind in LANES:
        p.zone(f"lane_{lid}", 30, y, width - 50, h, "", kind)
        p.text(f"lab_{lid}", 40, y + 10, 150, h - 20, f"<b>{esc(label)}</b>", size=13, color=FONT)

    x = x0
    prev = None
    for s in steps:
        w = s["w"]
        dashed = s["todo"] is not None
        col: list[str] = []
        for lid, _, y, h, _ in LANES:
            cell = s.get(lid)
            if cell is None:
                continue
            title, body = cell
            cid = f"s{s['n']}_{lid}"
            kind = "teal" if lid == "dev" else ("amber" if lid == "aws" else "white")
            if lid == "dev":
                head = f"Step {s['n']} — {s['t']}"
                lines = [f'<b><font style="font-size:12px">{esc(head)}</font></b>']
                if s["todo"]:
                    lines.append(f'<i><font style="font-size:9px" color="#9A6A1C">{esc(s["todo"])}</font></i>')
                lines.append(f'<font style="font-size:10px" face="Courier New">{esc(title)}</font>')
                lines.append(f'<font style="font-size:9px">{"<br>".join(esc(b) for b in body)}</font>')
                p.box(cid, x, y + 12, w, h - 24, "", ["<br>".join(lines)], kind, dashed=dashed, raw=True)
            else:
                p.box(cid, x, y + 12, w, h - 24, title, body, kind, dashed=dashed and lid != "dev", size=10)
            col.append(cid)
        # vertical flow inside the column
        cx = x + w / 2
        for a, b in zip(col, col[1:], strict=False):
            ay = p.geo[a][1] + p.geo[a][3]
            by = p.geo[b][1]
            p.edge(f"v_{a}_{b}", a, b, [(cx, ay), (cx, by)], "", "grey")
        if prev is not None:
            px, py, pw, ph = p.geo[prev]
            y_mid = 110 + 12 + 40
            p.edge(f"n_{prev}", prev, f"s{s['n']}_dev", [(px + pw, y_mid), (x, y_mid)], "", "teal")
        prev = f"s{s['n']}_dev"
        x += w + gap

    p.text("foot", 30, 1450, width - 60, 100, (
        "<b>ECR</b>: its own root (step 2, make ecr-up), so make down ENV=aws keeps the repositories and images and a redeploy skips steps 2–4 unless the code changed; make down-all (EKS → main → ECR) is the only target that deletes images. "
        "<b>Image tag</b>: make push records the pushed sha in artifacts/image-tag; plan and deploy use it (IMAGE_TAG= overrides) and deploy checks it exists in ECR before apply; down passes none, so a later commit cannot break any of the three. "
        "<b>Rollback</b> for steps 5–12 is make down ENV=aws (main stack); there is no per-module destroy target. "
        "<b>Cut line</b> (prompts/00): tower_carrier_client = \"direct\" + enable_nat_gateway = true in envs/aws.tfvars, then steps 5–6 again — see page 3."
    ), size=11)  # fmt: skip
    return p


# =====================================================================================================================
# Page 3 — integrations and config
# =====================================================================================================================

NOT_ENV = {"AWS_IAM", "CLIENT_CREDENTIALS", "AUTHORIZATION_CODE", "TODO", "SYMMETRIC_DEFAULT", "HMAC_256"}
# keys of services/web-chat/config.js (browser config, not env vars; 09 §6.4)
NOT_ENV |= {"COGNITO_DOMAIN", "CLIENT_ID", "REDIRECT_URI", "AGENT_URL"}


def service_env_names() -> set[str]:
    names: set[str] = set()
    for f in sorted((ROOT / "services").glob("*/.env.example")):
        names |= set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", f.read_text("utf-8"), re.M))
    return names


def mark_env(text: str, known: set[str], missing: set[str]) -> str:
    def sub(m: re.Match[str]) -> str:
        tok = m.group(0)
        if tok in NOT_ENV or "_" not in tok:
            return tok
        if tok in known:
            return tok
        missing.add(tok)
        return tok + "†"

    return re.sub(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]*[A-Z0-9]\b", sub, text)


def page_integrations(missing: set[str]) -> Page:
    known = service_env_names()
    rows = [
        ("teal", "Alexa+ → Tower (AgentCore Runtime)",
         "MCP over Streamable HTTP · Bearer JWT (Cognito); Runtime custom_jwt_authorizer, then Tower's auth.py (sub → user_id)",
         "tfvars tower_jwt_discovery_url, tower_jwt_allowed_clients / _audience → Runtime authorizer; tower_jwks_url, tower_jwt_issuer → Runtime env TOWER_JWKS_URL, TOWER_JWT_ISSUER, TOWER_JWT_AUDIENCE, TOWER_JWT_CLIENT_IDS → tower-mcp. Output tower_mcp_url → pasted into the toolkit form.",
         "services/tower-mcp/tests/test_auth.py · registration.md §6 (TODO(human))"),
        ("teal", "Browser → web chat page + Cognito Hosted UI",
         "HTTPS · CloudFront (OAC → private S3) · Cognito authorization code + PKCE, no client secret",
         "module.web_chat page URL → Cognito client callback / logout URL; outputs cognito_hosted_ui_url, cognito_client_id, web_chat_url, agent_url, binding_url → make web-chat-sync → config.js keys COGNITO_DOMAIN, CLIENT_ID, REDIRECT_URI, AGENT_URL, BINDING_BASE_URL",
         "services/web-chat/tests/test_static.py · sign-in on AWS: TODO(human) build-log/20"),
        ("teal", "Web chat page → Lambda URL → agent runtime",
         "HTTPS POST /invocations · Cognito Bearer + Content-Type + Runtime session header, nothing else; CORS = page origin; Runtime custom_jwt_authorizer (allowed_clients = web-chat)",
         "proxy env AGENT_INVOKE_URL = the agent runtime's invocation URL; agent env TOWER_URL = tower_mcp_url, WEB_CHAT_BINDING_BASE_URL = binding_url, BEDROCK_MODEL_ID, TOWER_ENV=aws, REF_AGENT=bedrock",
         "services/web-chat/tests/test_proxy.py · services/ref-client/tests/test_http.py (401 before any model call)"),
        ("teal", "Agent → Tower (bearer pass-through)",
         "MCP over Streamable HTTP · the caller's Bearer, byte for byte; Tower verifies it again",
         "module.cognito issuer, jwks_url, client_id (+ var.tower_jwt_allowed_clients) → Tower env TOWER_JWKS_URL, TOWER_JWT_ISSUER, TOWER_JWT_CLIENT_IDS and both Runtime authorizers",
         "services/ref-client/tests/test_http.py (bearer reaches a fake Tower unchanged) · tests/e2e/test_web_chat_story.py"),
        ("teal", "Reference client / latency run → Tower",
         "MCP · Bearer (TOWER_JWT); SigV4 only if no JWT authorizer is configured",
         "output tower_mcp_url → render_env.py → TOWER_URL in deploy/.env.aws → ref-client, scripts/latency.py; TOWER_JWT from the root .env",
         "make latency-aws → artifacts/latency-aws.md · tests/aws/test_latency_aws.py · tests/e2e/test_make_demo.py (ENV=aws; TODO(human))"),
        ("teal", "Tower → AgentCore Gateway",
         "MCP tools/call · SigV4, authorizer AWS_IAM (Runtime role: bedrock-agentcore:InvokeGateway)",
         "module.agentcore_gateway.gateway_url → Runtime env CARRIER_GATEWAY_URL; CARRIER_CLIENT=gateway, CARRIER_GATEWAY_TOOLS=discover, CARRIER_GATEWAY_AUTH=sigv4, CARRIER_GATEWAY_REGION → camara_client.config. Output gateway_url → render_env.py CARRIER_GATEWAY_URL (+ CARRIER_GATEWAY_TOOLS = artifacts/gateway-tools.json for local runs)",
         "packages/camara-client/tests/test_gateway_live_wiring.py · tests/aws/test_gateway_conformance.py (nightly: conformance fixtures through GatewayClient)"),
        ("teal", "Gateway → Identity → mock /oauth2/token",
         "OAuth2 client credentials, client_secret_basic, HTTPS through the managed VPC resource",
         "random_password.carrier_client[\"tower\"] → Identity provider client_secret and the mock's registry (Secrets Manager → MOCK_CLIENTS_YAML → MOCK_CLIENTS_FILE); var.carrier_target_scopes → target scopes; issuer / token URL = https://<mock_domain_name>",
         "tests/aws/test_terraform_static.py (no carrier secret outside Identity on the gateway path) · tests/aws/test_gateway_conformance.py"),
        ("teal", "Gateway → mock carrier (CAMARA calls)",
         "HTTPS + OAuth2 bearer injected from Identity; internal ALB via managed VPC resource (routing_domain = mock_domain_name)",
         "local.carrier.base_url → each target's servers[0]; output carrier_base_url → render_env.py CARRIER_BASE_URL; Runtime / Lambda env CARRIER_BASE_URL, CARRIER_BACKEND (recorded, never branched on)",
         "tests/aws/test_gateway_conformance.py · tests/aws/test_backend_swap.py (ATT_ALLOW_APPLY=1, sandbox vars)"),
        ("grey", "Phone → binding page (CloudFront → HTTP API → Lambda)",
         "HTTPS over mobile data · single-use token · session cookie signed with SESSION_SECRET",
         "output binding_url (= public_base_url) → render_env.py BINDING_URL; Lambda env BASE_URL, BIND_REDIRECT_URI, SESSION_SECRET ← random_password.session_secret",
         "services/binding-page/tests/test_bind_flow.py, test_lambda.py, test_playwright.py (local) · §2.6 real phone (TODO(human))"),
        ("grey", "Phone → carrier authorize (mock)",
         "HTTPS · GET /carrier/oauth2/authorize on the HTTP API → VPC link → internal ALB (the only public route to the mock)",
         "binding Lambda env CARRIER_AUTHORIZE_URL = <public_base_url>/carrier/oauth2/authorize, CARRIER_TOKEN_URL, CARRIER_CLIENT_ID = binding-page",
         "§2.6 real phone (TODO(human) build-log/13)"),
        ("grey", "Binding page → Gateway (number-verification)",
         "MCP · SigV4; AUTHORIZATION_CODE through the Identity binding provider + workload identity",
         "binding_carrier_client = gateway → CARRIER_CLIENT=gateway, CARRIER_GATEWAY_URL, CARRIER_GATEWAY_TOOLS=discover (direct → CARRIER_SECRET_REF=env:CARRIER_CLIENT_SECRET)",
         "unverified: TODO(human) build-log/13 — real-phone bind; fallback binding_carrier_client = direct"),
        ("grey", "Tower → Alerts /internal/watch",
         "HTTPS through CloudFront · shared bearer",
         "random_password.internal_bearer → Runtime ALERTS_INTERNAL_BEARER and alerts INTERNAL_BEARER; ALERTS_INTERNAL_URL = public_base_url",
         "services/alerts/tests/test_handler.py · tests/e2e/test_alerts.py (local)"),
        ("amber", "Mock carrier → Alerts webhook",
         "HTTPS POST /hooks/{kind}/{sink_token} · CloudEvents · sink token · WAF rate rule + route throttling",
         "alerts env HOOKS_BASE_URL = public_base_url (sinks registered with the carrier); output hooks_base_url → render_env.py ALERTS_URL",
         "services/alerts/tests/test_hooks.py · tests/e2e/test_alerts.py · make showcase-alerts (local)"),
        ("amber", "EventBridge Scheduler → alerts / reconcile",
         "Lambda invoke · aws_iam_role.scheduler · payload {profile} | {action: tick} | {scheduled_time}",
         "var.alerts_timezone → cron timezone and ALERTS_DEFAULT_TZ; ALERTS_MODE=lambda",
         "tests/aws/test_terraform_static.py (schedules match 10 §3)"),
        ("amber", "Alerts → SNS → watcher's phone",
         "SNS Publish (SMS) · Lambda execution role; replies topic → alerts (two-way)",
         "ALERTS_SENDER=sns, SNS_TOPIC_ARN (replies topic); var.sms_monthly_spend_limit → SMS preferences",
         "services/alerts/tests/test_sim_swap_recipients.py · SMS sandbox run (TODO(human))"),
        ("grey", "Tower, Lambdas, aws_seed.py → DynamoDB + KMS",
         "AWS SDK · IAM roles (Runtime, Lambda ×3); IAM user for aws_seed.py",
         "outputs table_prefix, kms_key_arn, kms_hmac_key_arn → env TOWER_TABLE_PREFIX, TOWER_KMS_KEY_ID, TOWER_KMS_HMAC_KEY_ID, TOWER_ENV=aws (Terraform for services; render_env.py and aws_seed.py for local tools)",
         "tests/aws/test_kms_wiring.py · packages/tower-consent tests (moto + DynamoDB Local)"),
        ("grey", "Runtime spans → reconcile Lambda",
         "OTel → aws/spans (Transaction Search) · Logs Insights with the Lambda role",
         "reconcile env TOWER_TRACE_LOG_GROUP, TOWER_METRICS_NAMESPACE, TOWER_RECONCILE_WINDOW_H",
         "packages/tower-audit/tests/test_aws_nightly.py · TODO(human): traces, expect checked > 0"),
        ("grey", "Developer → ECR, mock task (ECS Exec)",
         "docker login with an ECR token · ecs execute-command (SSM) · IAM user",
         "output ecr_repositories → push_images.py; mock_cluster_name / _service_name / _container_name → aws_seed.py (render_env.py also writes MOCK_CLUSTER, MOCK_SERVICE, MOCK_CONTAINER, read by nothing)",
         "tests/aws/test_scripts.py (dry runs against fake outputs)"),
    ]  # fmt: skip

    cols = [("#", 44), ("Producer → consumer", 250), ("Protocol · auth", 330), ("Config: where it is set → how it travels → env var → reader", 640), ("Proof", 390)]  # fmt: skip
    x_tab, gap = 30, 6
    tab_w = sum(w for _, w in cols) + gap * (len(cols) - 1)
    var_x = x_tab + tab_w + 40
    width = var_x + 560 + 30
    row_h = 74
    y0 = 110
    height = y0 + 40 + (row_h + gap) * len(rows) + 150
    p = Page("08_integrations", "3 Integrations and config", width, max(height, 1500))
    p.text("t1", 30, 14, width - 60, 32, "Integrations and config — every edge on page 1, where its settings come from, and what proves it", size=20, color=FONT, bold=True)  # fmt: skip
    p.text("t2", 30, 46, width - 60, 22, "Config travels two ways: Terraform variable / resource → module → service environment (Runtime environment_variables, Lambda environment, ECS task env); and Terraform output → make outputs → artifacts/tf-outputs.json → scripts/render_env.py → deploy/.env.aws → mk/vars.mk exports for ENV=aws targets.", size=11)  # fmt: skip
    p.text("t3", 30, 68, width - 60, 22, "† = env var not in any services/*/.env.example (finding F8 in the companion doc). Row colour: teal request path, amber proactive path, grey binding / data / management.", size=11)  # fmt: skip

    x = x_tab
    for i, (name, w) in enumerate(cols):
        p.box(f"h{i}", x, y0, w, 34, name, None, "head", align="left", size=11)
        x += w + gap
    y = y0 + 34 + gap
    for r, (kind, prod, proto, cfg, proof) in enumerate(rows, start=1):
        x = x_tab
        cells = [str(r), prod, mark_env(proto, known, missing), mark_env(cfg, known, missing), mark_env(proof, known, missing)]
        for i, ((_, w), val) in enumerate(zip(cols, cells, strict=True)):
            k = kind if i < 2 else "white"
            if i == 1:
                p.box(f"r{r}c{i}", x, y, w, row_h, val, None, k, align="left", size=10)
            else:
                p.box(f"r{r}c{i}", x, y, w, row_h, "", [val], k, align="left", size=10, body_size=9 if i else 11)
            x += w + gap
        y += row_h + gap

    # --- cut-line variants (greyed) ---------------------------------------------------------------------------
    p.zone("z_cut", var_x, y0, 560, 880, "Cut-line variants (prompts/00) — not the default", "ghost", dashed=True, size=13)
    a_body = [
        "envs/aws.tfvars: tower_carrier_client = \"direct\" + enable_nat_gateway = true",
        "Runtime network_mode VPC (private subnets, agentcore_egress SG, NAT for ECR/logs)",
        "Runtime env CARRIER_CLIENT=direct, CARRIER_SECRET_REF=env:CARRIER_CLIENT_SECRET,",
        "CARRIER_CLIENT_SECRET — the documented exception: Tower's client secret is",
        "Runtime config (tests/aws/test_terraform_static.py allows it only here)",
        "Tower → mock: HTTPS + OAuth2 client credentials minted by the mock itself",
        "Gateway and Identity are still created; alerts always uses Gateway;",
        "binding uses it unless binding_carrier_client = \"direct\"",
        "render_env.py still writes CARRIER_CLIENT=gateway (finding F7)",
        "cost: + NAT gateway ≈ $0.045/h + data",
    ]
    p.box("cut_a", var_x + 20, y0 + 40, 520, 230, "A · DirectClient from Runtime → Fargate mock (expressible today)", [mark_env(s, known, missing) for s in a_body], "ghost", dashed=True, size=11)  # fmt: skip
    # mini diagram for A
    my = y0 + 300
    p.box("cut_rt", var_x + 20, my, 150, 70, "Runtime (VPC)", ["DirectClient"], "ghost", align="center")
    p.box("cut_alb", var_x + 205, my, 150, 70, "internal ALB", ["HTTPS :443"], "ghost", align="center")
    p.box("cut_mock", var_x + 390, my, 150, 70, "Fargate mock", ["/oauth2/token + CAMARA"], "ghost", align="center")
    p.edge("cut_e1", "cut_rt", "cut_alb", [(var_x + 170, my + 35), (var_x + 205, my + 35)], "", "ghost")
    p.edge("cut_e2", "cut_alb", "cut_mock", [(var_x + 355, my + 35), (var_x + 390, my + 35)], "", "ghost")
    p.box("cut_gw", var_x + 20, my + 110, 520, 60, "Gateway + Identity (still deployed)", ["used by alerts (always) and binding (unless direct) — not by Tower"], "ghost", dashed=True)  # fmt: skip
    b_body = [
        "prompts/00: \"Gateway and Identity become a documented experiment\"",
        "Terraform has no switch for it: modules agentcore_gateway and",
        "agentcore_identity are unconditional, the alerts Lambda always gets",
        "gateway_environment, and agentcore_runtime / lambdas take gateway_arn",
        "Needs: count on both modules, a direct environment for alerts (secret",
        "via Secrets Manager or env), binding_carrier_client = \"direct\" —",
        "a Terraform change, not made here (finding F10)",
        "What would remain: Runtime (VPC) + Fargate mock + Lambda ×3 + data",
    ]
    p.box("cut_b", var_x + 20, my + 200, 520, 220, "B · No Gateway / Identity at all (not expressible today)", b_body, "ghost", dashed=True, size=11)  # fmt: skip
    p.text("cut_note", var_x + 20, my + 440, 520, 120, (
        "Either variant keeps the rules: policy stays deterministic code in Tower, Alexa+ only phrases, the mock stays the "
        "demo default, binding still needs the phone on mobile data, and the hot path stays one DynamoDB read plus the carrier calls."
    ), size=11)  # fmt: skip
    return p


def build() -> tuple[str, set[str]]:
    missing: set[str] = set()
    mx = ET.Element("mxfile", host="app.diagrams.net", agent="Ask the Tower design generator", type="device")
    for page in (page_target(), page_sequence(), page_integrations(missing)):
        mx.append(page.element())
    ET.indent(mx, space="  ")
    return ET.tostring(mx, encoding="unicode") + "\n", missing


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if the file on disk differs from the generator")
    args = ap.parse_args()
    text, missing = build()
    if args.check:
        if not OUT.exists() or OUT.read_text("utf-8") != text:
            print(f"{OUT.name} is stale: run gen_deploy.py")
            return 1
    else:
        OUT.write_text(text, "utf-8")
        print(f"wrote {OUT.relative_to(ROOT)}")
    if missing:
        print("env vars on page 3 not in any services/*/.env.example (marked †):", ", ".join(sorted(missing)))
    issues = checker.check(OUT)
    for i in issues:
        print("  " + i)
    print(f"check: {len(issues)} issue(s)")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
