# AWS first run — build, push, deploy, test, tear down

A human runbook. Every step here spends money or touches the account, so none of it is automated; the agent only wrote the code. Run everything from the repo root in WSL with `AWS_PROFILE=att` (or the keys in the root `.env`). Expect a half day the first time, 20 minutes after that.

**Tab or terminal (prompt 23).** Every `make` line below is also a button in the demo UI's Ops tab: `make ops-ui`, open `http://127.0.0.1:8091/#ops`. The tab runs the same command (`make <target> ENV=aws`) with the credentials from the root `.env`, streams the log, turns the step green from its evidence, highlights the next step, and appends a redacted transcript to `artifacts/ops-log/<date>-<name>.log` (paste that to the agent when an apply fails). The two are interchangeable: a step done in the terminal shows green in the tab. Its AWS buttons are enabled only when the UI runs on the host on `127.0.0.1` with `DEMO_UI_AWS_OPS=1` (what `make ops-ui` sets) and `make config-check` passes; `down`, `down-all`, closing the admin path and a deploy whose plan destroys anything need a typed word. The button for each line is in its `# tab:` comment.

Source of truth for details: [`docs/architecture/deployment-agentcore.md`](../architecture/deployment-agentcore.md) (steps 1–13) and the prompt-20 build log (outside the repo).

## 0. One-time, days ahead (console / CLI, nothing deployed yet)

| # | What | How | Why it blocks later |
|---|---|---|---|
| 0.1 | AWS CLI works | `aws sts get-caller-identity --profile att` → your account id | everything |
| 0.2 | Bedrock model access | Console → Bedrock → Model access → enable **Amazon Nova Micro** (us-east-1) | the agent's model; without it the web chat answers 500 |
| 0.2a | Bedrock quota | Console → Service Quotas → Amazon Bedrock → the on-demand requests/tokens per minute for Nova Micro in us-east-1; the new-account default is low enough that a full `make test-e2e` with the Bedrock agent hits `ThrottlingException` (5 e2e tests failed that way on 2026-10-11). Request an increase, or keep `REF_AGENT=scripted` in the root `.env` (the `.env.example` default) for test runs and set `auto` only for the Bedrock run. | e2e reds that are quota, not code; the web chat answering 500 under load |
| 0.3 | SNS SMS sandbox | `aws sns create-sms-sandbox-phone-number --region us-east-1 --phone-number <your E.164>` → wait for the SMS → `aws sns verify-sms-sandbox-phone-number --region us-east-1 --phone-number <E.164> --one-time-password <OTP>` → confirm with `aws sns list-sms-sandbox-phone-numbers --region us-east-1` | the sandbox silently drops SMS to unverified numbers; no buzz on camera |
| 0.4 | **A hostname for the mock** | The Fargate mock sits behind an internal ALB that Gateway/Identity call over HTTPS, so it needs a certificate. Either a public Route 53 zone you own (`mock_domain_name` + `mock_route53_zone_id`, Terraform issues the ACM cert) **or** an existing ACM certificate in us-east-1 (`mock_certificate_arn`). | `make plan` fails without one. If you own no domain: register a cheap one in Route 53 (~$12/yr, propagates in minutes) — do it today |
| 0.5 | Terraform state backend (by hand, once) | Follow the header of `deploy/terraform/backend.tf`: create a private, versioned, KMS-encrypted S3 bucket and a DynamoDB lock table; put their names in `backend.tf` (the `REPLACE-ME` values). | `make plan` / `make ecr-up` need it |
| 0.6 | `deploy/terraform/envs/aws.tfvars` | `cp envs/aws.tfvars.example envs/aws.tfvars`; set region/profile, `mock_domain_name` + zone id (or cert arn), `sms_sandbox_numbers = ["<your E.164>"]`, leave `mock_assume_mobile_data = true` (simulation, documented), `demo_admin_cidrs = []` for now. Gitignored — never commit it. | — |
| 0.7 | Root `.env` | `COGNITO_PASSWORD_ASISH`, `COGNITO_PASSWORD_MOM` (any strong value; never written to disk by the scripts). `make config-check` must pass (it is also the Ops tab's gate). For the tab: `DEMO_UI_AWS_OPS=1` here, or start it with `make ops-ui`. | step 6 |
| 0.8 | Toolchain in WSL | Every tool with its install line and check: [`PREREQUISITES.md`](../../PREREQUISITES.md) §3 — Terraform ≥ 1.9, an arm64 Buildx builder, `jq`, the AWS CLI **and its Session Manager plugin** (`make seed-aws` needs it). Corporate proxy: the same `SSL_CERT_FILE` export you use for Node applies to Terraform's provider download. | steps 2–3, seed |

## 1. Local proof first (10 min)

```bash
make up                                   # rebuild every image from the current tree
REF_AGENT=scripted make test-e2e ENV=local   # 30 passed expected
make demo                                  # "demo ok: 4 stories"
```

Push only from a tree that passes this — the pushed `ref-client` image must be known good (build-log R1).

## 2. Registry and images (15 min)

```bash
git status --short | head             # clean tree: `make push` tags images with `git rev-parse --short HEAD`
make ecr-up                           # ECR root, own state; idempotent; writes artifacts/tf-outputs-ecr.json      # tab: 1 · ECR repositories
make push ENV=aws                     # five images, linux/arm64, provenance off; tags <sha> + latest; writes artifacts/image-tag   # tab: 2 · Push
aws ecr describe-images --repository-name att-dev/tower-mcp --region us-east-1 --query 'imageDetails[].imageTags' | head
```

## 3. Plan, deploy (30–45 min; AgentCore runtimes are the slow part)

```bash
make plan                             # writes artifacts/terraform-plan.txt — READ IT    # tab: 3 · terraform plan (chips: add / change / destroy)
```

Expected in the plan: DynamoDB tables, KMS (two keys), `module.cognito` (pool, domain, client `web-chat`), `module.web_chat[0]` (S3 + OAC, CloudFront, proxy Lambda + function URL), two `agentcore_runtime` resources (Tower MCP, ref-client HTTP with the JWT authorizer), Gateway + Identity, the Fargate mock + internal ALB + ACM cert, Lambda alerts + EventBridge Scheduler, SNS. Check: no `*_password` / `*_secret` in clear; Cognito callback URL = the CloudFront `https://…/` URL.

```bash
make deploy                           # checks the tag in ECR → apply → outputs → register-gateway → seed-aws (reloads the mock only, no users yet)   # tab: 4 · Deploy (type deploy if the plan destroys)
make outputs                          # prints outputs, writes deploy/.env.aws (gitignored)   # tab: 5 · Write deploy/.env.aws
```

If `make deploy` stops, fix the one thing it names and run `make deploy` again — apply is idempotent. The four things only this first apply can confirm (and the symptom of each) are in the prompt-20 build log: Runtime session header (400), authorizer passing `Authorization` through (agent answers 401 to a valid sign-in), Lambda URL permission (403 from the function URL), provider lock platform. Paste the error to the VS Code agent with "fix against this apply output; no git".

## 4. Users, seed, page (10 min)

```bash
make cognito-users                    # creates asish + mom; writes artifacts/cognito-users.json ({name: sub}, gitignored)   # tab: 6 · Cognito users
make seed-aws                         # Mom's line bound, Mom → Asish watch grant; Asish's line left UNBOUND for Flow 1      # tab: 7 · Seed
make web-chat-sync                    # renders config.js from tf-outputs, uploads services/web-chat, invalidates CloudFront   # tab: 8 · Upload the web chat page
make web-chat-url                     # the page URL                                                                          # tab: 9 · Web chat URL
```

## 5. Test on AWS

**5.1 Smoke (5 min)**

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$(jq -r .agent_url.value artifacts/tf-outputs.json)"   # 401 expected, no bearer
```

Open the page, sign in as `asish` → "Is my line OK?" → `NOT_BOUND` + a `/bind/` link. Open the link **on the phone** (Wi-Fi off) → Verify → "Line connected" → ask again → an answer. (`mock_assume_mobile_data = true` makes the Fargate mock accept the phone; it is the one simulation in this path and the doc says so.)

**5.2 The three moments, by hand (10 min)** — the admin calls go to the Fargate mock; `make showcase-tower ENV=aws` prints the exact `curl` lines with the bearer from `deploy/.env.aws`.

1. As `asish`: "Is my line OK?" → OK. Fire `sim_swap` on Asish's line → ask again → `SIM_SWAPPED_RECENT`.
2. Fire `cf_set` → "Is anything forwarding my calls?" → `CALL_FORWARDING_SET`.
3. "Is Mom's line OK?" → OK. "Watch Mom's line." → watching. Fire `sim_swap` on Mom's line → **your phone buzzes** (the SMS from SNS). Sign in as `mom` in a second browser profile → binding page → revoke Asish's watch → fire `sim_swap` again → no SMS; "who checked my line?" shows `SUPPRESSED_REVOKED`.

**5.3 The repeatable proof (10 min)**

```bash
make seed-aws SEED_ARGS=--bind-asish  # the stories need Asish bound                                                  # tab: 10 · Bind Asish
make admin-open ENV=aws               # the stories drive the mock's /_admin and the binding page's admin: open the path first  # tab: Open the admin path
make demo ENV=aws                     # same four stories; transcripts must match local on tool calls + reason codes  # tab: 11 · make demo on AWS
make latency-aws                      # 50 calls per path → artifacts/latency-aws.md (p95 is the slide's number)    # tab: 12 · Latency
make test-e2e ENV=aws                 # the e2e layer against the deployment                                          # tab: 13 · End to end on AWS
make test-report                      # artifacts/test-report.md with the ENV=aws row                                 # tab: 14 · test report
```

**5.4 Control room against AWS (optional, after prompt 22)** — `make admin-open` (tab: Admin path → **Open**; the `/32` comes from `checkip.amazonaws.com`, or pass `ADMIN_CIDR=<ip>/32`): it writes `demo_admin_cidrs = ["<ip>/32"]` into `aws.tfvars` with a marker comment, plans, and deploys (which re-runs `make outputs`). Changing the list replaces the admin bearer and rolls the mock task; the gate recognises that roll and deploys in one go. Only if the plan would destroy anything else does it stop (the tab shows `confirm` with the chips): read the plan and run `make deploy` (tab: **4 · Deploy**, type `deploy`). Then restart the UI as `make showcase-ui ENV=aws`: Moment 1–3 run from the UI against the real deployment (Transplant stays local). When done: `make admin-close` (tab: **Close**, type `close`).

## 6. Record, then tear down

```bash
# evidence for the submission
ls artifacts/transcripts/ artifacts/latency-aws.md
# cost: hours up × the rates in artifacts/cost.md; add the bill delta from Billing → Bills
make down ENV=aws                     # destroys the main root; ECR repositories and images are KEPT (also compose down -v)   # tab: Teardown → Down (type down)
aws resourcegroupstaggingapi get-resources --tag-filters Key=project,Values=ask-the-tower --query 'ResourceTagMappingList[].ResourceARN'
#   → only the five att-dev/* ECR repositories should remain
```

Next morning: `make deploy` again is all it takes (images are still in ECR). After judging: `make down-all` (deletes ECR too; tab: Teardown → **Down all**, type `down-all`), delete the sandbox phone numbers (`aws sns delete-sms-sandbox-phone-number`), and the state bucket + lock table last, by hand.

## If something refuses

| Symptom | Likely cause | Where |
|---|---|---|
| `make push`: `no match for platform` | buildx builder not arm64 | 0.8 |
| `make push`: `exec /bin/sh: exec format error` | arm64 emulation (binfmt) lost after a Docker/WSL restart; `push` re-registers it before building | `docker run --privileged --rm tonistiigi/binfmt --install arm64` |
| `make plan`: certificate / zone error | 0.4 not done | `aws.tfvars` |
| `make deploy`: `image tag not found` | tree changed since `make push` | re-run `make push ENV=aws` |
| Web chat: 500 on every question | Nova Micro access not enabled | 0.2 |
| Web chat: 401 after a valid sign-in | authorizer not passing `Authorization` | build-log/20 unverified #2 |
| Bind page refuses the phone | `mock_assume_mobile_data` false, or phone on Wi-Fi | `aws.tfvars`, phone |
| No SMS arrives | number not verified in the SNS sandbox | 0.3 |
| `make demo ENV=aws`: Asish `NOT_BOUND` | seed without `--bind-asish` | 5.3 |
| Ops tab: AWS buttons disabled | the gate: not `make ops-ui` (or not on `127.0.0.1`, or in the container), or `make config-check` fails; the reason is on every button | 0.7, `make ops-ui` |
| Ops tab: "account unknown" | `aws` CLI missing, or no credentials in the root `.env` | 0.1, 0.8 |
| `make admin-open` exits 3 | its plan destroys something (bearer rotation, mock task roll): expected | read the plan, then `make deploy` (tab: type `deploy`) |
