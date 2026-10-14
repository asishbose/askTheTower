"""What each Ops target does, in plain words: the hover card on every step (doc 11 §2). Display text only — it never
changes what runs; the allowlist does. Every target in `allowlist.TARGETS` has an entry (checked by the tests)."""

from __future__ import annotations

from collections.abc import Mapping

EXPLAIN: Mapping[str, tuple[str, ...]] = {
    # --- local stack ---------------------------------------------------------------------------------------
    "up": (
        "docker compose up: mock carrier, Tower, binding page, Alerts, DynamoDB Local, web chat",
        "Generates deploy/compose/.env (local bearers) on first run",
        "Waits until every service answers its health check",
    ),
    "down": ("docker compose down -v: stops every local container and deletes their volumes (local data)",),
    "seed": (
        "Reloads the mock's demo scenario (clock back to the start)",
        "Rewrites the demo rows: Asish and Mom bound, Mom's watch grant to Asish",
    ),
    "demo": (
        "ref-client runs the three moments and the transplant story against the local stack",
        "Clock jumps, SIM swap / call forwarding events and the revoke come from the script",
        "Writes artifacts/transcripts/*.json and compares the reason codes",
    ),
    "showcase-tower": (
        "Tower on its own: Inspector, the three tools, refusals and latency (runs until Stop)",
    ),
    "showcase-alerts": ("Alerts on its own: watch, fire, buzz (SMS log), revoke, suppressed, escalation",),
    "showcase-binding": ("The binding page on a phone, with simulated mobile data (runs until Stop)",),
    "test-unit": (
        "pytest -m unit: pure code, no I/O (~8 min)",
        "Writes artifacts/junit-unit.xml and coverage",
    ),
    "test-integration": (
        "pytest -m integration: component pairs over real interfaces (moto / DynamoDB Local)",
    ),
    "test-e2e": ("pytest -m e2e against the running local stack: demo vs golden, alerts, binding, chaos",),
    "test-report": (
        "Collects junit, coverage, latency, conformance and privacy into artifacts/test-report.md",
    ),
    # --- AWS pipeline --------------------------------------------------------------------------------------
    "aws-ecr-up": (
        "terraform apply on the ECR root: the five image repositories (idempotent)",
        "Lifecycle: keep only the newest image, expire untagged after a day",
        "Survives `down`; only `down-all` deletes it",
        "Writes artifacts/tf-outputs-ecr.json",
    ),
    "aws-push": (
        "Checks arm64 emulation (re-registers it after a Docker/WSL restart)",
        "docker buildx: builds mock-carrier, tower-mcp, binding-page, alerts, ref-client for linux/arm64",
        "Pushes each to ECR tagged <git sha> + latest; writes artifacts/image-tag",
        "Needs Zscaler off (it breaks the TLS of the build)",
    ),
    "aws-plan": (
        "terraform plan of the main stack with the pushed image tag",
        "Saves artifacts/tfplan and terraform-plan.txt; the chips show + add / ~ change / − destroy",
        "Read it: a destroy you did not expect means stop",
    ),
    "aws-deploy": (
        "Checks every repository has the image tag",
        "terraform apply: VPC + NAT, DynamoDB, KMS, Cognito, AgentCore Runtime (Tower, web chat agent), "
        "Gateway + Identity, Fargate mock, Lambdas, CloudFront page",
        "Writes the outputs (deploy/.env.aws), registers the CAMARA specs with Gateway, seeds the Fargate mock",
        "Asks for `deploy` when the plan destroys something",
    ),
    "aws-outputs": (
        "terraform output → artifacts/tf-outputs.json",
        "Renders deploy/.env.aws: Tower URL, binding/alerts URLs, admin path URLs and bearer when open",
        "Already part of 4 · Deploy; run it alone after a manual change",
    ),
    "aws-cognito-users": (
        "Creates (or resets) the Cognito users asish and mom with the passwords in the root .env",
        "Records their subs in artifacts/cognito-users.json (the user ids on AWS)",
    ),
    "aws-seed": (
        "Loads the mock's demo scenario on Fargate (ECS Exec)",
        "Writes the demo rows under the Cognito subs: Mom bound, Mom's watch grant to Asish; Asish left unbound",
    ),
    "aws-web-chat-sync": (
        "Renders the page's config.js from the outputs (Cognito, proxy URL)",
        "Uploads services/web-chat/ to S3 and invalidates CloudFront",
    ),
    "aws-web-chat-url": ("Prints the web chat page URL (sign in as asish or mom)",),
    "aws-seed-bind-asish": (
        "Same seed as 7, plus binds Asish's line so the stories can run",
        "Self-healing: a line still held by a stale user from an earlier deploy is cleared and rebound",
        "artifacts/seed-aws.json shows both lines bound",
    ),
    "aws-demo": (
        "ref-client runs Moments 1–3 against Tower on AWS (the transplant story stays local)",
        "Signs Asish in to Cognito; Tower answers through AgentCore → Gateway → the Fargate mock",
        "Clock jumps, events and Mom's revoke go through the admin path — open it first",
        "Writes artifacts/transcripts/moment-*.json",
    ),
    "aws-latency": (
        "50 calls per path, one warm AgentCore session: line_is_ok (self, Mom), is_reachable, watch_line",
        "p50/p95/p99 → artifacts/latency-aws.md; red when a p95 is over 400 ms",
        "Measured from this laptop: includes the trip to us-east-2",
    ),
    "aws-test-e2e": (
        "pytest -m e2e against AWS: make demo vs golden (twice) and the chaos matrix (carrier faults)",
        "Local-only checks skip with their reason",
        "Needs the admin path open (~4 min)",
    ),
    "aws-test-report": (
        "Collects every junit file, coverage, latency, conformance and privacy into artifacts/test-report.md",
        "Red when a gate is missed (today: the latency p95)",
    ),
    "aws-admin-open": (
        "Writes your public address /32 into envs/aws.tfvars, then plan + deploy",
        "Opens the mock's, binding page's and Alerts' admin routes to that address, with one bearer",
        "Needed by 11 and 13 and the Control room on AWS; Zscaler off (it changes your address)",
    ),
    "aws-admin-close": (
        "Empties the address list in envs/aws.tfvars, then plan + deploy: the admin routes 404 again",
    ),
    "aws-down": (
        "terraform destroy of the main stack; ECR repositories and images are kept",
        "Also stops the local compose stack",
        "Cognito users are recreated by 6 next time (new subs; 10 rebinds)",
    ),
    "aws-down-all": (
        "Everything in `down`, then destroys the ECR repositories and every image",
        "After judging only",
    ),
}


def explain(name: str) -> tuple[str, ...]:
    return EXPLAIN.get(name, ())
