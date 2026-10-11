# 10 — Scheduler and Infrastructure

**Role:** everything that isn't application code: tables, topics, schedules, secrets, networking, and the one-command run in both environments.
**Tooling:** Terraform for AWS (AgentCore and an optional EKS module); docker compose for local; a Helm chart per service plus an umbrella chart, installed on kind and on EKS.

---

## 1. Three environments, same containers

| | Local (`make up`) | AWS (`make deploy`) | EKS (`make deploy-eks`) |
|---|---|---|---|
| Tower MCP server | container | Bedrock AgentCore Runtime | Deployment + ALB ingress |
| Carrier gateway | `DirectClient` → mock | AgentCore Gateway + Identity → mock on Fargate (or sandbox) | `DirectClient` → mock in-cluster (or Gateway by config) |
| Consent / binding page | container (Lambda emulator) | Lambda + API Gateway + CloudFront | Deployment + public ingress (ACM TLS) |
| Alerts service | container with an in-process scheduler; SNS → log sink | Lambda + EventBridge Scheduler + SNS (SMS) | Deployment (`ALERTS_MODE=k8s`) + CronJobs (`python -m alerts.job`); SNS via IRSA |
| Mock carrier | container | Fargate task, internal ALB, ACM cert | Deployment, ClusterIP |
| Reference client | CLI; HTTP app + web chat page on `127.0.0.1:8083` (compose service `web-chat`) | CLI, and the web chat agent: AgentCore Runtime (HTTP) behind S3/CloudFront + a Lambda function URL, sign-in with Cognito (09 §6) | Job (`make demo ENV=eks`); HTTP service ClusterIP, ingress off |
| Store | DynamoDB Local | DynamoDB on-demand | DynamoDB on-demand (same tables, IRSA) |
| Audit reconciliation | skipped | nightly Lambda over Observability traces | CronJob (shipped off: needs Tower's spans via ADOT) |
| Demo UI ([11](11-demo-ui.md)): demo tooling, laptop only | container on `127.0.0.1:8090` (scripted agent); `make showcase-ui` runs it on the host on `127.0.0.1:8091` with Bedrock | not deployed: runs on the laptop against `deploy/.env.aws` (no ECR repository, no Terraform); carrier pane and macros off | not deployed (`values-eks` `enabled: false`; the chart is for kind only) |

The EKS column is the portability proof: the same images, the same env names, the same transcripts. It is created for the showcase window and torn down (`make down-eks`); the demo default is local, and the Alexa+ track's primary target is AgentCore Runtime.

Locally, "SMS" is a log line plus an optional webhook to your phone via a free SMS gateway for the demo rehearsal; on AWS it is SNS.

Locally, CAMARA webhook sinks must be `https://`, so compose adds one non-application container: `alerts-tls` (Caddy with its own local CA) terminates TLS for Alerts' `/hooks/*`, and the mock trusts that CA's root through `SSL_CERT_FILE`. On AWS, API Gateway terminates TLS. The topology and ports are in `deploy/compose/README.md`.

## 2. AWS resources

| Resource | Purpose | Notes |
|---|---|---|
| AgentCore Runtime ×2 | Tower (MCP) and the web chat agent (the reference client, HTTP; 09 §6) | the MCP server shape the Alexa+ track asks for; the agent runtime has its own role (Bedrock + logs only) and is gated by `enable_web_chat` |
| Cognito (`modules/cognito`) | user pool, Hosted UI domain, app client `web-chat` (PKCE, no secret) | issuer of the bearer for the chat page and Tower (`user_id = sub`); users by `make cognito-users`, not Terraform; free tier |
| S3 + CloudFront + Lambda URL (`modules/web_chat`) | the chat page (private bucket, OAC) and a header-forwarding proxy to the agent runtime (CORS) | `make web-chat-sync` uploads the page; cents per day |
| AgentCore Gateway | CAMARA OpenAPI → MCP tools | specs from `specs/camara/` |
| AgentCore Identity | outbound carrier OAuth | client credentials; auth-code for consented ops |
| DynamoDB (7 tables) | `Users`, `Lines`, `Grants`, `Watches`, `Audit`, `BindTokens` (TTL), `AlertsState` (TTL; Alerts' sink tokens, dedupe, rate-limit, escalation — 06 §7) | on-demand; point-in-time recovery on `Audit` |
| KMS | `msisdn_enc`, `line_id` HMAC key | two keys: a symmetric CMK for the `msisdn_enc` envelope (rotation on) and an `HMAC_256` key for `line_id` and the audit trim marker (KMS computes HMACs only with HMAC keys, and does not rotate them) |
| SNS | SMS to watchers; optional push topic | sandbox SMS limits apply — register the demo numbers |
| EventBridge Scheduler | polls per watch profile | 5-min, 30-min, daily schedules; targets the Alerts Lambda |
| Lambda ×3 | binding page, alerts (hooks + poll + send), reconciliation | Python 3.12, arm64 |
| API Gateway (HTTP) | binding page and webhook sinks | WAF rate limit on `/hooks/*` |
| ECR (5 repositories) | images for every service (the web chat agent reuses `ref-client`; the page and proxy need no image) | own Terraform root and state (`deploy/terraform/ecr`) so `make down` never deletes them; the main root reads them by data source; lifecycle keeps the last 10 images; scan on push |
| Fargate + ALB | mock carrier | internal only; Gateway reaches it over the VPC link |
| Secrets Manager | mock's own client secrets; anything Identity doesn't hold | nothing carrier-side lives here when Identity is used |
| CloudWatch / Observability | traces, metrics, the latency SLI | per-`line_id` labels forbidden on metrics |

## 3. Schedules

| Schedule | Target | Profile |
|---|---|---|
| `rate(5 minutes)` | alerts-poll `transplant` | reachability |
| `rate(30 minutes)` | alerts-poll `care` (08:00–22:00 line-holder local) | reachability |
| `cron(0 8 * * ? *)` local-time by line | alerts-poll `self`, `care` | SIM swap + call forwarding |
| `cron(0 3 * * ? *)` | audit trim + reconciliation (its own Lambda; `now` = the schedule time) | — |
| `rate(5 minutes)` | alerts `{"action": "tick"}` | escalation steps (06 §3) |

Schedules invoke the same Lambda with a `profile` argument; the Lambda queries `Watches` by profile.

## 4. Secrets and credentials

- Carrier OAuth client → AgentCore Identity.
- Mock's client registry → the mock's own config (it *issues* credentials; it doesn't consume any).
- Alexa+ inbound bearer → Runtime config.
- Nothing in images, nothing in the repo; `gitleaks` in CI.

## 5. The one-command run

```
make up            # compose: mock, tower, consent, alerts, dynamodb-local; loads scenarios/demo.yaml
make seed          # reload the demo scenario (resets the mock clock and state)
make logs | logs-save  # tail the stack; write deploy/compose/logs/stack.log (the e2e privacy grep reads it)
make demo          # the three moments + the transplant closing story via the reference client; prints transcripts
make test          # unit + contract + conformance + privacy + latency
make policy-table  # prints the decision table (artifacts/policy-table.md)
make corpus        # tool-selection table from the reference client
make showcase-<x>  # one component's standalone showcase: alexa tower binding gateway alerts audit mock ref infra ui
make showcase-ui   # the demo control room (11) on the host at 127.0.0.1:8091 (SHOWCASE_UI_PORT; compose keeps 8090): Bedrock when credentials resolve, else scripted (needs make up)
make showcase      # all of the above, in testing-and-showcase.md §4 order
make ecr-up        # the five ECR repositories in their own Terraform root/state (once; idempotent) → artifacts/tf-outputs-ecr.json
make ecr-outputs   # rewrite artifacts/tf-outputs-ecr.json from the ECR root (what `make push` reads before the first deploy)
make deploy        # checks the pushed tag exists in ECR; terraform apply (AWS); registers Gateway specs; seeds mock on Fargate
make cognito-users # ENV=aws: create Cognito users asish + mom (scripts/cognito_user.py; password from COGNITO_PASSWORD_<NAME> or a prompt) → artifacts/cognito-users.json (subs)
make web-chat-sync # ENV=aws: render config.js from the outputs, upload services/web-chat/ to the S3 bucket, invalidate CloudFront
make web-chat-url  # ENV=aws: print the chat page URL (output web_chat_url)
make web-chat      # local: open the chat page at http://127.0.0.1:8083/ (needs make up)
make plan          # terraform plan only (main root; needs ecr-up)
make tf-check      # terraform fmt + validate of the main and ecr roots (init -backend=false; no AWS) + tables.auto.tfvars.json current
make down          # ENV=aws: terraform destroy of the main root — the ECR repositories and their images are kept; compose down
make down-all      # after judging: down-eks (if up) + down ENV=aws + destroy the ECR root — the only target that deletes images
make test-unit | test-integration | test-e2e [ENV=local|eks|aws] | test-nightly | test-report
make build | push | sbom | scan        # images (build/sbom/scan: six, incl. demo-ui; push: the five services only); push = buildx linux/arm64 to ECR (sha + latest; records artifacts/image-tag); SBOM; CVE scan (fails on critical)
make helm-lint | helm-template | helm-kind   # charts (lint + helm-unittest + kubeconform); kind install + demo (§6); helm-deps is their internal prerequisite
make deploy-eks | down-eks             # EKS module + umbrella chart; teardown of the cluster only
make showcase-artifacts  # regenerate every generated artefact; fails if any is stale
make docs-check | deck-check | diagrams-png
make secrets-check # gitleaks over the full history + grep of artifacts/ and docs/ — the pre-submission gate
make ps | shell-<svc>                  # local stack status; a shell in one container
make lint | typecheck | fmt | privacy-grep   # ruff; mypy --strict on packages; ruff format+fix; privacy greps
make seed-aws | outputs | latency-aws | register-gateway   # the steps `make deploy` chains, runnable alone
make kube-context                      # point kubectl at the EKS cluster
make build-<svc>                       # one image, e.g. build-tower-mcp
make check-deliverables                # every path in the prompts' Deliverables blocks exists
make config-check  # validate the root .env (the one settings file) and list what is set, secrets masked
make help          # grouped listing of all of the above, with the current ENV
```

`ENV` (default `local`) must be one of `local eks aws`; anything else fails before any target runs. Targets that talk to a running eks/aws deployment depend on the internal `require-env` guard, which fails fast unless `deploy/.env.$ENV` (rendered from terraform outputs) exists. Destructive targets (`down ENV=aws`, `down-eks`, `down-all`) ask first unless `FORCE=1`. `down ENV=aws` keeps exactly the five ECR repositories (and the S3 state bucket, which is outside Terraform); `down-all` returns the account to zero. `plan` and `deploy` pass `image_tag` = the tag the last `make push` completed (`artifacts/image-tag`, else the git sha; override with `IMAGE_TAG=`), so a commit between push and deploy cannot point the stack at an unpushed tag; `down` passes no `image_tag` at all. `QUIET=1` and `NO_COLOR=1` tune output.

Settings and secrets live in one gitignored file, the repo-root `.env` (template `.env.example`). `mk/vars.mk` sets and exports every non-empty `KEY=value` in it before any default, so terraform (`TF_VAR_*`), the aws CLI, boto3, helm and the scripts all read it from the environment; `make KEY=value` still wins over it. `make up` copies the keys local compose uses into `deploy/compose/.env`. Values may not contain `$` or `#` (make stops with the line number); `.dockerignore` keeps every `.env` out of the images.

`make demo` is the reproducibility claim: a judge runs it and sees the same transcripts the video shows.

## 6. Helm and EKS

One chart per service under `deploy/helm/` plus an umbrella chart, with values for `kind` and `eks`. `make helm-kind` proves the charts without a cloud; `make deploy-eks` applies a separate Terraform module (cluster, 2-node arm64 node group, IRSA roles per service with least privilege, load-balancer controller) and installs the umbrella against the same DynamoDB, KMS and SNS as the AgentCore deployment. `make demo ENV=eks` must match the golden transcripts. The EKS stack is its own Terraform root (`deploy/terraform/eks`, reading the AgentCore root's state), so `make down-eks` removes only the cluster. On kind the charts reproduce compose exactly (`TOWER_ENV=local`, DynamoDB Local, Alerts' in-process scheduler, a Helm-generated CA for the https webhook sinks); `deploy/helm/README.md` tabulates what differs per target. Not the demo default; it exists because the deck says the production shape changes hosting, not architecture, and a running cluster with matching transcripts is the proof. Torn down after the showcase.

## 7. Cost

See the tracks slide and its speaker note: about $12/month at demo volume, dominated by AgentCore Runtime and Bedrock dev-time tokens. `make down` returns the account to zero. The EKS target is the exception — a control plane plus two small nodes for the hours of the showcase window — and is reported as its own line in `artifacts/cost.md` and in the tracks slide note.

## 8. Showcase on its own

`make showcase-infra`: `make up && make demo` on a clean machine, timed. Then `make deploy` with the Terraform plan on screen, and `make down`. What it proves: the README is enough. See `testing-and-showcase.md` §2.10.
