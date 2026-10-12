#!/usr/bin/env python3
"""Generate 11-demo-ui.drawio (three pages): the demo control room — never hand-edit the XML.

Page 1: ENV=local — the four panes and every endpoint each consumes on the compose stack.
Page 2: ENV=aws — the same UI on the laptop, and what a laptop can reach in the account, with the demo admin path
        (prompt 22, U2: CIDR + bearer, off by default) drawn dashed.
Page 3: the Ops tab (prompt 22, amended by prompt 23 T1–T8) — the local runner and, behind the gate, the aws runner
        over the runbook's make targets; the make loader for .env; the quiet probes; teardown.

Same palette and Page helpers as gen_deploy.py (08) and gen_flows.py (09, 10). The demo UI was built on 2026-10-09
(step 2) with the gaps G1–G4 closed (components/11-demo-ui.md §10), so those boxes and edges are solid; G5 is closed
by the demo admin path (U2, 2026-10-11, specified, not built yet: dashed); G6 stays declined (Asish only on AWS).

    uv run python docs/architecture/diagrams/gen/gen_demo_ui.py            # write + check
    uv run python docs/architecture/diagrams/gen/gen_demo_ui.py --check    # fail if the file is stale
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check as checker  # noqa: E402
from gen_deploy import ROOT, Page, esc  # noqa: E402
from gen_flows import header, titles  # noqa: E402

OUT = HERE.parent / "11-demo-ui.drawio"


def lbl(*lines: str) -> str:
    return "<br>".join(esc(x) for x in lines)


# =====================================================================================================================
# Page 1 — ENV=local
# =====================================================================================================================

PANE_X, PANE_W = 90, 500
TGT_X, TGT_W = 910, 470
LBL_X = 750


def page_local() -> Page:
    p = Page("11_local", "1 ENV=local", 1960, 1360)
    titles(
        p,
        "11 · Demo UI (control room): four panes, ENV=local",
        "Demo tooling, laptop only (built 2026-10-09). It drives only what make demo and the showcase "
        "scripts already drive; it owns nothing.",
        "Teal = the conversation (request path) · amber = the live feed (proactive evidence) · grey = demo "
        "controls and binding · Gn = the gap closed for it (doc 11 §10).",
    )

    p.zone("z_laptop", 30, 100, 1410, 1010, "Presenter's laptop (ENV=local)", kind="grey")
    p.box("browser", 60, 140, 560, 84, "Browser: desktop or phone (mobile layout)", [
        "one static page, HTMX + SSE, no build step · 127.0.0.1:8090 · any other address needs DEMO_UI_TOKEN",
        "top bar: tabs Control room · Ops (/#ops; page 3) · pill: agent mode (scripted in compose, bedrock on the host), ENV",
    ], kind="white")  # fmt: skip
    p.zone("z_ui", 60, 250, 560, 840, "services/demo-ui (FastAPI + HTMX)", kind="white")

    p.box("p1", PANE_X, 300, PANE_W, 160, "1 · Conversation", [
        "type as Asish or Mom · macros: Moment 1 · Moment 2 · Moment 3 · Transplant",
        "ref_client as a library: TowerClient, BedrockAgent, or ScriptedAgent without Bedrock",
        "shows tool call · reason_codes chips (Tower's only) · summary / spoken · next_step",
        "macros: run_demo(STORIES, reset=False, on_step) (G4): per step id, narration,",
        "expected vs actual codes, pass/fail, timing · one timeline, one run at a time",
        "next_step bind_line → url → QR code in pane 4 (D1: nothing texts the link)",
    ], kind="teal")  # fmt: skip
    p.box("p2", PANE_X, 490, PANE_W, 160, "2 · Carrier controls (mock /_admin)", [
        "Reset = the make seed code · clock +1 / +12 / +20 min · fire sim_swap cf_set cf_clear",
        "unreachable reachable by holder (Asish, Mom) and the mock's opaque ref, never by number:",
        "GET /_admin/state?view=refs, POST /_admin/lines/{ref}/events (G2): no number on the wire",
        "faults timeout / 500 / 429 · Bearer MOCK_ADMIN_TOKEN on every mutation (G1)",
        "ENV=aws: off with the reason (decision 2)",
    ], kind="grey")  # fmt: skip
    p.box("p3", PANE_X, 680, PANE_W, 160, "3 · Live feed (SSE, one poller, every 2 s)", [
        "audit: list_lines(owner) → list_for_line(line, owner): Asish's log + Mom's log, newest 50",
        "sms: GET /internal/sent?after=n (G3): template id, role, user id, body; never a number",
        "also per tick: mock /_admin/state?view=refs (pane 2) and binding /_admin/tables (pane 4)",
        "no DynamoDB Streams · the chain check stays on the binding page /me (the UI holds no keys)",
    ], kind="amber")  # fmt: skip
    p.box("p4", PANE_X, 870, PANE_W, 150, "4 · Binding (QR code · grants · revoke)", [
        "QR code of POST /_admin/bind-tokens {user_id} → url + ?as=phone-<who>, drawn by the UI",
        "grants: GET /_admin/tables?format=json · resolve: GET /_admin/resolve?user=..&line=..",
        "revoke / re-grant: POST /_admin/grants {owner, grantee, grant, alias, action}",
        "the QR code is drawn by the UI (segno, SVG); the phone opens the page, never the UI",
    ], kind="grey")  # fmt: skip
    p.box("redact", PANE_X, 1035, PANE_W, 40, "", [
        "every HTML fragment and SSE frame → number filter (demo_ui.redact) · uvicorn access_log off",
    ], kind="white")  # fmt: skip

    p.zone("z_compose", 880, 250, 530, 840, "compose stack (built)", kind="slate")
    p.box("tower", TGT_X, 350, TGT_W, 110, "tower-mcp :8080 /mcp", [
        "MCP tools/list · tools/call · Bearer TOWER_BEARER + X-Tower-User",
        "each turn is an ordinary check: consent read, carrier calls, audit row",
        "the UI adds nothing to this path",
    ], kind="teal")  # fmt: skip
    p.box("mock", TGT_X, 490, TGT_W, 160, "mock-carrier 127.0.0.1:8443 /_admin/* (MOCK_ADMIN=1)", [
        "GET /state?view=refs · GET /scenarios · GET /clock: open (Tower, Alerts)",
        "POST /scenarios/load · POST /clock {advance_s}",
        "POST /lines/{ref}/events {event} · POST /faults · DELETE /faults",
        "every POST / DELETE: Bearer MOCK_ADMIN_TOKEN (G1) · GET /healthz",
        "(routers/admin.py)",
    ], kind="grey")  # fmt: skip
    p.box("alerts", TGT_X, 680, TGT_W, 70, "alerts :8082", [
        "GET /internal/sent?after=n: ALERTS_MODE=local only, internal bearer (G3)",
    ], kind="amber")  # fmt: skip
    p.box("ddb", TGT_X, 770, TGT_W, 70, "DynamoDB Local :8000 (read only)", [
        "Lines by_owner (bind.py:124) · Audit via reader.py:63, owner check :57",
    ], kind="grey")  # fmt: skip
    p.box("binding", TGT_X, 870, TGT_W, 150, "binding-page :8081 /_admin/*", [
        "POST /bind-tokens {user_id} · GET /tables?format=json",
        "GET /resolve?user=..&line=.. · POST /grants {...}",
        "404 unless TOWER_ENV=local and BIND_ADMIN=1 (config.py)",
        "(routes/admin.py) · GET /bind/{token} for the phone",
    ], kind="grey")  # fmt: skip

    p.zone("z_aws", 1470, 250, 460, 200, "AWS: Bedrock only (optional)", kind="amber")
    p.box("bedrock", 1500, 300, 400, 110, "Bedrock Converse (tool use)", [
        "BEDROCK_MODEL_ID (amazon.nova-micro-v1:0) · AWS_PROFILE",
        "the repo's only model call stays in ref_client/agent.py",
        "absent → ScriptedAgent; free text turned off",
    ], kind="white")  # fmt: skip

    header(p, "phone", TGT_X, 1150, TGT_W, 56, "Phone on the same Wi-Fi (scans the QR code)", amber=True)

    p.edge("e_br", "browser", "z_ui", [(560, 224), (560, 250)], lbl("HTMX + SSE GET /events"), at=(420, 237))
    p.edge("e_bed", "p1", "bedrock", [(590, 330), (1500, 330)], lbl("Converse"), kind="teal", at=(LBL_X, 330))
    p.edge("e_tow", "p1", "tower", [(590, 405), (910, 405)], lbl("MCP Streamable HTTP"), kind="teal",
           at=(LBL_X, 405))  # fmt: skip
    p.edge("e_mock", "p2", "mock", [(590, 570), (910, 570)], lbl("/_admin/* + Bearer (G1)"), at=(LBL_X, 570))
    p.edge("e_al", "p3", "alerts", [(590, 715), (910, 715)], lbl("GET /internal/sent (G3)"), kind="amber",
           at=(LBL_X, 715))  # fmt: skip
    p.edge("e_ddb", "p3", "ddb", [(590, 805), (910, 805)], lbl("read as owner; Reset"), kind="amber",
           at=(LBL_X, 805))  # fmt: skip
    p.edge("e_bind", "p4", "binding", [(590, 945), (910, 945)], lbl("/_admin bind-tokens · grants"),
           at=(LBL_X, 945))  # fmt: skip
    p.edge("e_phone", "phone", "binding", [(1145, 1150), (1145, 1020)],
           lbl("GET /bind/{token}?as=phone-asish"), at=(1145, 1125))  # fmt: skip

    p.box("never", 30, 1240, 1900, 96, "Never (doc 11 §8)", [
        "1 decides nothing: no tower_policy, no camara_client, no CAMARA path; chips are Tower's reason_codes · "
        "2 never calls the carrier or the gateway, only the mock's /_admin (a simulation aid)",
        "3 never sends, receives or renders a phone number (refs on the wire, filter on the way out) · "
        "4 writes no consent: grants go through the binding page; Reset is the make seed code",
        "5 reads an audit only as its line's owner · 6 cannot make Alexa+ speak first · 7 is never deployed "
        "to AWS: no ECR repository, no Terraform, values-eks enabled=false · 8 adds nothing to the hot path",
        "Captions, not hidden: D1 (link not texted → QR code) · D15 remainder (the line-holder's own "
        "UNREACHABLE copy; withheld in the demo run anyway)",
    ], kind="white", body_size=10)  # fmt: skip
    return p


# =====================================================================================================================
# Page 2 — ENV=aws: the same UI on the laptop, pointed at the account
# =====================================================================================================================

P2_PX, P2_PW = 90, 580
A_X, A_W = 1040, 420
A_LBL = 835


def page_aws() -> Page:
    p = Page("11_aws", "2 ENV=aws (laptop)", 1960, 1300)
    titles(
        p,
        "11 · Demo UI, ENV=aws: on the laptop, pointed at the account, never deployed into it",
        "The same container (or uv run) reading deploy/.env.aws. Nothing of the UI is created in AWS. Dashed = the "
        "demo admin path: only while demo_admin_cidrs is non-empty (prompt 22, U2), CIDR and bearer on every call.",
        "Solid = reachable from the laptop by default · dashed = only with the admin path open · no edge = no laptop "
        "path. Asish only (decision 3). Ops tab: the local runner is off with ENV=aws (U3); the aws runner is page 3.",
    )

    p.zone("z_laptop", 30, 100, 700, 900, "Presenter's laptop", kind="grey")
    p.zone("z_ui", 60, 140, 640, 820, "services/demo-ui (same image, or uv run)", kind="white")
    p.box("p1", P2_PX, 190, P2_PW, 180, "1 · Conversation: works", [
        "TOWER_URL = the Runtime URL from deploy/.env.aws",
        "TOWER_BEARER must be a JWT; D21: an empty discovery URL means every call gets 401",
        "X-Tower-User is ignored on AWS: Asish only, no persona switch (decision 3)",
        "Bedrock with the att profile, or ScriptedAgent; pill: mode · ENV=aws · admin on|off",
        "admin open: Moment 1–3 on; Transplant stays local (D-I, no partner/neighbour on AWS)",
    ], kind="teal")  # fmt: skip
    p.box("p2", P2_PX, 400, P2_PW, 180, "2 · Carrier controls, Reset: admin path only", [
        "closed (default): off, \"admin path closed: see Ops → Admin path\"",
        "open: MOCK_URL = https://<api>/carrier-admin + Bearer DEMO_ADMIN_BEARER",
        "state (view=refs forced by the gateway), clock, events by ref, faults",
        "Reset (U3, aws_reset.py): mock scenarios/load → Alerts /internal/reset",
        "→ binding /_admin/grants re-grant (Cognito subs); never the local seed module",
    ], kind="grey", dashed=True)  # fmt: skip
    p.box("p3", P2_PX, 610, P2_PW, 180, "3 · Live feed", [
        "audit: DynamoDB with the att profile; TOWER_TABLE_PREFIX from .env.aws",
        "polled every 5 s: about 3 reads/s while the page is open, cents per day",
        "SMS, admin open: GET /internal/sent from the SentSms table (U1), same wire shape",
        "SMS, closed: \"SMS list unavailable\"; the text lands on the real phone",
        "and the audit row with message_ref is the evidence",
    ], kind="amber")  # fmt: skip
    p.box("p4", P2_PX, 820, P2_PW, 120, "4 · Binding", [
        "always: the QR code of Tower's NOT_BOUND next_step.url",
        "admin open: grants (table=Grants), resolve, revoke and re-grant through",
        "/_admin in bearer mode (DEMO_ADMIN_BEARER); /me on the phone is unchanged",
    ], kind="grey")  # fmt: skip

    p.zone("z_aws", 1000, 100, 930, 900, "AWS account (the demo UI is not in it)", kind="slate")
    p.box("bedrock", A_X, 190, A_W, 70, "Bedrock Converse", ["us-east-1 · BEDROCK_MODEL_ID"], kind="white")
    p.box("runtime", A_X, 280, A_W, 90, "Tower on AgentCore Runtime", [
        "MCP over HTTPS · JWT sub → user_id",
        "Gateway + Identity → mock (unchanged)",
    ], kind="teal")  # fmt: skip
    p.box("mock", A_X, 400, A_W, 180, "Mock carrier on Fargate", [
        "internal ALB: CAMARA prefixes, /oauth2, /healthz",
        "admin open: + aws_lb_listener_rule.admin ×2",
        "(/_admin state, scenarios, clock, faults, lines/*;",
        "never /_admin/sink) · MOCK_ADMIN_TOKEN always set · MOCK_CLOCK=wall",
        "ECS Exec kept for aws_seed.py",
    ], kind="grey")  # fmt: skip
    p.box("ddb", A_X, 610, A_W, 80, "DynamoDB (Lines, Audit; SentSms when open)", [
        "Query Lines by_owner · Query Audit, read as owner",
    ], kind="grey")  # fmt: skip
    p.box("alerts", A_X, 710, A_W, 80, "Alerts Lambda + SNS", [
        "/hooks/* always · open: GET /internal/sent, POST /internal/reset (ALERTS_ADMIN=1)",
    ], kind="amber")  # fmt: skip
    p.box("apigw", A_X, 820, A_W, 120, "Binding page: API Gateway + Lambda", [
        "/bind/{token} · /bind/callback · /me (session)",
        "closed: /_admin/* → 404 · open: bearer mode",
        "(BIND_ADMIN=1 + BIND_ADMIN_TOKEN = the bearer; 04 §8)",
    ], kind="grey")  # fmt: skip
    p.box("absent", 1500, 190, 400, 180, "Not in the account, by design", [
        "no demo-ui ECR repository (push stays five)",
        "no Terraform resource for the UI",
        "Helm values-eks.yaml: enabled=false",
        "nothing listening publicly for the UI",
        "a static test in tests/aws/ checks all four",
    ], kind="white")  # fmt: skip
    p.box("gate", 1500, 400, 400, 540, "Demo admin path (only with demo_admin_cidrs)", [
        "public HTTP API, execute-api URL (never CloudFront):",
        "ANY /carrier-admin/_admin/{proxy+} → VPC link → ALB",
        "GET /internal/sent · POST /internal/reset → alerts",
        "ANY /_admin/{proxy+} → binding page",
        "",
        "authorizer admin_gate (Lambda REQUEST): sourceIp in the",
        "CIDRs and Bearer DEMO_ADMIN_BEARER; no header 401, else 403;",
        "X-Forwarded-For ignored; via CloudFront always 403",
        "",
        "DEMO_ADMIN_BEARER: keepers on the CIDRs, never the",
        "internal bearer; each service checks it again",
        "(SSM → make outputs → .env.aws)",
        "",
        "closed (default): no route, no rule, no SentSms → 404",
        "close: demo_admin_cidrs = [] then make deploy",
    ], kind="grey", dashed=True)  # fmt: skip

    header(p, "phone", A_X, 1060, A_W, 56, "Real phone (Wi-Fi off for binding)", amber=True)

    p.edge("e_bed", "p1", "bedrock", [(670, 225), (1040, 225)], lbl("Converse (att profile)"), kind="teal",
           at=(A_LBL, 225))  # fmt: skip
    p.edge("e_rt", "p1", "runtime", [(670, 325), (1040, 325)], lbl("MCP + JWT (Asish)"),
           kind="teal", at=(A_LBL, 325))  # fmt: skip
    p.edge("e_mock", "p2", "mock", [(670, 470), (1040, 470)], lbl("/_admin + Bearer (open)"), dashed=True,
           at=(A_LBL, 470))  # fmt: skip
    p.edge("e_ddb", "p3", "ddb", [(670, 650), (1040, 650)], lbl("Query as owner"), kind="amber",
           at=(A_LBL, 650))  # fmt: skip
    p.edge("e_sent", "p3", "alerts", [(670, 750), (1040, 750)], lbl("GET /internal/sent (open)"), kind="amber",
           dashed=True, at=(A_LBL, 750))  # fmt: skip
    p.edge("e_bind", "p4", "apigw", [(670, 850), (1040, 850)], lbl("QR code of next_step.url"),
           at=(A_LBL, 850))  # fmt: skip
    p.edge("e_badm", "p4", "apigw", [(670, 915), (1040, 915)], lbl("/_admin bearer mode (open)"), dashed=True,
           at=(A_LBL, 915))  # fmt: skip
    p.edge("e_g_mock", "gate", "mock", [(1500, 490), (1460, 490)], "", dashed=True)
    p.edge("e_g_al", "gate", "alerts", [(1500, 750), (1460, 750)], "", dashed=True)
    p.edge("e_g_bind", "gate", "apigw", [(1500, 880), (1460, 880)], "", dashed=True)
    p.edge("e_sms", "alerts", "phone", [(1040, 775), (1020, 775), (1020, 1088), (1040, 1088)],
           lbl("SNS SMS: the real buzz"), kind="amber", at=(1020, 1030))  # fmt: skip
    p.edge("e_phone", "phone", "apigw", [(1250, 1060), (1250, 940)], lbl("bind link over mobile data"),
           at=(1250, 1000))  # fmt: skip

    p.box("note", 30, 1150, 960, 110, "What ENV=aws can demonstrate from the laptop", [
        "default: the conversation as Asish against the real Runtime, the audit feed, the bind link as a QR code",
        "admin path open: also pane 2, Reset, Moment 1–3, the SMS lane and Mom's revoke; Transplant stays local",
        "the video's Alexa+ simulator path does not depend on the UI at all",
    ], kind="white", body_size=10)  # fmt: skip
    return p


# =====================================================================================================================
# Page 3 — the Ops tab: local runner, aws runner behind the gate, stepper, probes, teardown
# =====================================================================================================================


def page_ops() -> Page:
    p = Page("11_ops", "3 Ops tab", 1960, 1420)
    titles(
        p,
        "11 · The Ops tab: the local stack and, behind a gate, the AWS pipeline from the browser",
        "Prompt 22 (O1, O6, U3) amended by prompt 23 (T1–T8, 2026-10-11): Ops is a tab; the aws runner runs the "
        "runbook's make targets with the credentials of the root .env, which only make reads.",
        "Teal = the local runner · amber = the aws runner (dashed zone = only behind the gate) · grey = AWS · "
        "ENV is fixed on every argv; the only run-time parts are FORCE=1 after a typed word and a validated /32.",
    )

    p.zone("z_laptop", 30, 100, 1270, 1160, "Presenter's laptop (make ops-ui on the host, 127.0.0.1:8091/#ops)",
           kind="grey")  # fmt: skip
    p.box("browser", 60, 140, 640, 84, "Browser: top bar tabs Control room · Ops (T1)", [
        "Control room = the Stage and panes 1–4, unchanged (page 1) · Ops loads on first show (ops:open)",
        "POST /ops/run/{name} (confirm, cidr) · POST /ops/stop · HX-Request + same Origin + token rule",
    ], kind="white")  # fmt: skip
    p.zone("z_ui", 60, 250, 640, 830, "services/demo-ui · the Ops tab", kind="white")
    p.box("hdr", 90, 300, 580, 110, "Header (ops_header.html)", [
        "profile att | key pair from .env · region · account 1234-5678-9012 (sts probe)",
        "AWS runs: on | off: <the gate's reason, verbatim>",
        "cost banner: deployed since <tf-outputs.json mtime>, ≈ $0.06/h idle; down keeps ECR",
        "admin path open: close it after the demo",
    ], kind="white")  # fmt: skip
    p.box("run", 90, 430, 580, 170, "Run here: local runner (ops/runner.py, prompt 22)", [
        "on only with ENV=local, the host run, make on PATH, the checkout found (U3)",
        "stack · demo · tests: eleven make <target> ENV=local, env mode scrubbed",
        "one job at a time across both runners and the macros: a second run → 409",
        "the shared log: lines → mask_secrets + mask → ops frames on GET /events (O6)",
        "badge pass | fail | stopped, exit code, duration · plan chips for aws jobs",
        "Stop: local SIGINT → SIGTERM → SIGKILL at 2 s; aws SIGINT, never SIGKILL",
    ], kind="teal")  # fmt: skip
    p.box("steps", 90, 620, 580, 220, "AWS pipeline: the stepper (ops/aws_status.py, no subprocess)", [
        "1 ECR · 2 push · 3 plan (chips) · 4 deploy · 5 outputs · 6 Cognito users · 7 seed",
        "8 web chat upload · 9 web chat URL · 10 bind Asish · 11 demo · 12 latency",
        "13 end to end · 14 test report · each: Run, copy, state dot, evidence, time",
        "evidence: files make wrote, transcripts, GET healthz, the three probes",
        "next step = first not done whose after-steps are done (highlighted)",
        "deploy only on a current plan; type deploy when that plan destroys (T5)",
        "admin box: Open (/32 from checkip, editable) · Close (type close)",
    ], kind="amber")  # fmt: skip
    p.box("gate", 90, 860, 580, 110, "Gate (ops/gate.py, T4): all must hold, else disabled with the reason", [
        "G1 host run, not the container · G2 DEMO_UI_HOST exactly 127.0.0.1",
        "G3 DEMO_UI_AWS_OPS=1 (make ops-ui) · G4 make and checkout · G5 root .env exists",
        "G6 make config-check exits 0 (cached until .env changes) · ENV is not a condition",
    ], kind="white")  # fmt: skip
    p.box("tear", 90, 990, 580, 70, "Teardown (ops_teardown.html), red border, never in the stepper", [
        "Down: type down (keeps ECR; also compose down -v) · Down all: type down-all · then FORCE=1",
    ], kind="white")  # fmt: skip

    p.zone("z_local", 760, 250, 520, 260, "Allowlist, local runner (ops/allowlist.py)", kind="teal", dashed=True)
    p.box("allow_local", 790, 300, 460, 190, "make <target> ENV=local · scrubbed", [
        "stack: up · down · seed",
        "demo: demo · showcase-tower · showcase-alerts · showcase-binding",
        "tests: test-unit · test-integration · test-e2e · test-report",
        "env: allow-listed keys only; AWS_*, *_TOKEN, ENV … never pass",
        "no aws target, never ENV=aws (O1 holds for this runner)",
    ], kind="teal")  # fmt: skip

    p.zone("z_aws_allow", 760, 540, 520, 540, "Allowlist, aws runner: only behind the gate", kind="amber",
           dashed=True)  # fmt: skip
    p.box("allow_aws", 790, 590, 460, 230, "make <target> ENV=aws · dotenv (eighteen)", [
        "pipeline: ecr-up · push · plan · deploy · outputs",
        "cognito-users · seed-aws · web-chat-sync · web-chat-url",
        "seed-aws SEED_ARGS=--bind-asish · demo · latency-aws",
        "test-e2e · test-report",
        "admin: admin-open ADMIN_CIDR=<validated /32> · admin-close",
        "teardown: down · down-all",
        "typed word first: down, down-all, close, deploy if destroy > 0",
        "transcript: artifacts/ops-log/<date>-<name>.log (T7)",
    ], kind="amber")  # fmt: skip
    p.box("loader", 790, 840, 460, 110, "The make loader (envload.py, T3)", [
        "mk/vars.mk reads the root .env and exports it to the recipe",
        "the UI never sources or parses .env; probes: make --eval '__ops_probe: ; @…'",
        "the shell's own AWS_* never reach a child; HOME passes (~/.aws)",
    ], kind="amber")  # fmt: skip
    p.box("probes", 790, 970, 460, 90, "Quiet probes (probes.py): 5 s, filtered, never raise", [
        "facts (profile, region, keys=1) · sts Account · ecr names · tf output (public keys)",
        "make config-check (gate, 20 s) · curl checkip.amazonaws.com (the /32)",
    ], kind="amber")  # fmt: skip

    p.box("files", 760, 1110, 520, 130, "On the laptop, gitignored", [
        "root .env: read only by make (AWS_PROFILE or the key pair, AWS_REGION, DEMO_UI_AWS_OPS)",
        "artifacts/: tf-outputs-ecr.json · image-tag · terraform-plan.txt · tf-outputs.json",
        "cognito-users.json · seed-aws.json · web-chat-sync.json · latency-aws.md · test-report.md",
        "deploy/.env.aws (make outputs) · deploy/terraform/envs/aws.tfvars (admin-open/close)",
    ], kind="white")  # fmt: skip

    p.zone("z_aws", 1330, 250, 600, 830, "AWS account (the UI is not in it)", kind="slate")
    p.box("a_ecr", 1360, 300, 540, 90, "ECR root: five repositories", [
        "ecr-up creates (idempotent) · push fills · down keeps · down-all destroys",
    ], kind="grey")  # fmt: skip
    p.box("a_main", 1360, 410, 540, 200, "Main root (Terraform state in S3)", [
        "Runtime ×2 · Gateway + Identity · Lambdas · HTTP API · CloudFront",
        "Fargate mock + internal ALB · DynamoDB · KMS · Cognito · SNS",
        "plan: artifacts/terraform-plan.txt (chips) · deploy: apply, outputs,",
        "register-gateway, seed-aws · down: destroy (asks: the typed word, then FORCE=1)",
        "≈ $0.06/h idle (deployment-agentcore §4)",
    ], kind="grey")  # fmt: skip
    p.box("a_admin", 1360, 630, 540, 150, "Demo admin path (page 2), off by default", [
        "admin-open: demo_admin_cidrs = [your /32] in envs/aws.tfvars (marker comment)",
        "→ plan → exit 3 if it destroys (bearer rotation, task roll) → deploy",
        "admin-close: [] → plan → deploy (type close) · file missing → refuse",
    ], kind="grey", dashed=True)  # fmt: skip
    p.box("a_users", 1360, 800, 540, 80, "Cognito users, seed rows, web chat page", [
        "cognito-users (passwords from .env) · seed-aws · web-chat-sync",
    ], kind="grey")  # fmt: skip
    p.box("a_read", 1360, 980, 540, 80, "Read by the probes only", [
        "STS GetCallerIdentity · ECR DescribeRepositories · state outputs (S3)",
    ], kind="grey")  # fmt: skip

    p.edge("e_br", "browser", "z_ui", [(380, 224), (380, 250)], lbl("HTMX + SSE"), at=(450, 237))
    p.edge("e_run", "run", "allow_local", [(670, 460), (790, 460)], lbl("exec argv"), kind="teal", at=(730, 460))
    p.edge("e_aws", "steps", "allow_aws", [(670, 700), (790, 700)], lbl("gate on"), kind="amber", at=(730, 700))
    p.edge("e_gate", "gate", "steps", [(380, 860), (380, 840)], "", kind="grey")
    p.edge("e_load", "allow_aws", "loader", [(1020, 820), (1020, 840)], "", kind="amber")
    p.edge("e_apply", "loader", "a_main", [(1250, 880), (1310, 880), (1310, 520), (1360, 520)], lbl("make + .env"),
           kind="amber", at=(1310, 700))  # fmt: skip
    p.edge("e_probe", "probes", "a_read", [(1250, 1020), (1360, 1020)], lbl("read only"), kind="amber",
           at=(1305, 1020))  # fmt: skip

    p.box("never_ui", 30, 1290, 1900, 110, "Never (doc 11 §8.10, §8.11)", [
        "10 no shell, no argv from the browser (the word is compared, never passed; the /32 is validated), no ENV=eks, "
        "no aws/terraform outside the allowlist and the read-only probes; the aws runner only behind G1–G6",
        "11 no change to AWS on a click alone: down, down-all, close and a destroying deploy need their word; the UI's "
        "code never reads an AWS key, never calls an AWS API to change anything, is never deployed",
    ], kind="white", body_size=10)  # fmt: skip
    return p


def build() -> str:
    mx = ET.Element("mxfile", host="app.diagrams.net", agent="Ask the Tower design generator", type="device")
    for page in (page_local(), page_aws(), page_ops()):
        mx.append(page.element())
    ET.indent(mx, space="  ")
    return ET.tostring(mx, encoding="unicode") + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if the file on disk differs from the generator")
    args = ap.parse_args()
    text = build()
    if args.check:
        if not OUT.exists() or OUT.read_text("utf-8") != text:
            print(f"{OUT.name} is stale: run gen_demo_ui.py")
            return 1
    else:
        OUT.write_text(text, "utf-8")
        print(f"wrote {OUT.relative_to(ROOT)}")
    issues = checker.check(OUT)
    for i in issues:
        print("  " + i)
    print(f"{OUT.name}: check {len(issues)} issue(s)")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
