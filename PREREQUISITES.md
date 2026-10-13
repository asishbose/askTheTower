# Prerequisites

What has to be on the machine before `make` does anything. Three tiers of tools, then the accounts (§4): the local demo needs only the first tier; the AWS deployment (from the terminal or the demo UI's Ops tab) needs all three and §4. Verified on Windows 11 with WSL2 (Ubuntu 24.04); the same packages apply on Linux and macOS.

Each row has a check. If every check in a tier passes, that tier is done.

## 1. Local demo — `make up`, `make demo`, `make test`

| Tool | Why | Install (Ubuntu / WSL2) | Check |
|---|---|---|---|
| Docker Engine with Compose v2 | the five services, the mock carrier, DynamoDB Local | Docker Desktop with the WSL2 backend, or `apt install docker.io docker-compose-v2` | `docker compose version` |
| `make`, `bash`, `git` | the one-command run; `make push` tags images with the git sha | `apt install make git` | `make --version` |
| [`uv`](https://docs.astral.sh/uv/) | Python 3.12 toolchain for `make test`, the scripts and the demo UI; without it `make demo` falls back to the reference-client container | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | `uv --version` |
| `jq` | the runbooks' one-liners over `artifacts/tf-outputs.json` | `apt install jq` | `jq --version` |

## 2. Pre-submission gates — `make secrets-check`, `make docs-check`

| Tool | Why | Install | Check |
|---|---|---|---|
| [`gitleaks`](https://github.com/gitleaks/gitleaks) ≥ 8.18 | `make secrets-check`: a scan of the full git history before anything is pushed. `.gitleaks.toml` allowlists the planted test fixtures. The `apt` package is older and lacks the `git` subcommand; use the release binary. | `V=8.21.2; curl -sSL https://github.com/gitleaks/gitleaks/releases/download/v$V/gitleaks_${V}_linux_x64.tar.gz \| sudo tar -xz -C /usr/local/bin gitleaks` | `gitleaks version` |
| `drawio` CLI (optional) | regenerates the PNGs under `docs/architecture/diagrams/`; the SVGs/XML are committed, so this is only needed when a diagram changes | Draw.io Desktop | `drawio --version` |

## 3. AWS deployment — the Ops tab or `make ecr-up … deploy ENV=aws`

| Tool | Why | Install (Ubuntu / WSL2) | Check |
|---|---|---|---|
| AWS CLI v2 | every `aws` call in `mk/aws.mk` and the scripts; the Ops tab's probes | `curl -sSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o /tmp/awscliv2.zip && unzip -q /tmp/awscliv2.zip -d /tmp && sudo /tmp/aws/install` | `aws --version` → `aws-cli/2.x` |
| **Session Manager plugin** | `make seed-aws` (Ops step 7, and the tail of `make deploy`) loads the demo scenario with `aws ecs execute-command` into the Fargate mock; without the plugin the CLI stops with `SessionManagerPlugin is not found` | `curl -sSL https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb -o /tmp/smp.deb && sudo dpkg -i /tmp/smp.deb` | `session-manager-plugin` prints "installed successfully" |
| Terraform ≥ 1.9 | the two roots under `deploy/terraform/` (ECR; main stack) | HashiCorp apt repository, or the release zip into `/usr/local/bin` | `terraform -version` |
| Docker Buildx with an arm64 builder | `make push` builds `linux/arm64` images for AgentCore Runtime and Fargate; the default builder on x86 cannot | `docker run --privileged --rm tonistiigi/binfmt --install arm64 && docker buildx create --use --platform linux/arm64` | `docker buildx ls` shows `linux/arm64` |
| An AWS account with credentials in the root `.env` | `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` (or `AWS_PROFILE`) and `AWS_REGION`; the IAM user needs AdministratorAccess for the first apply | §4.1 below; [Configuration](README.md#configuration) | `make config-check`; `aws sts get-caller-identity` |
| A public DNS zone in Route 53 | the Fargate mock's internal ALB needs a certificate (`mock_domain_name` + `mock_route53_zone_id` in `deploy/terraform/envs/aws.tfvars`); a domain bought anywhere and delegated to Route 53 works | [`docs/runbooks/aws-first-run.md`](docs/runbooks/aws-first-run.md) §0.4 | `aws route53 list-hosted-zones` |
| Bedrock model access: Amazon Nova Micro, **and a non-zero daily token quota** | the web chat agent's model; a new account's quota is 0 → §4.2–4.3 (or the scripted agent) | console → Bedrock → Model access | `aws service-quotas get-service-quota --service-code bedrock --quota-code L-D2912E70 --region <region> --query Quota.Value` |
| SNS SMS sandbox: your number verified | the sandbox drops SMS to unverified numbers, so Moment 3's buzz never arrives | `aws sns create-sms-sandbox-phone-number` → `verify-sms-sandbox-phone-number` | `aws sns list-sms-sandbox-phone-numbers` shows `Verified` |

## 4. The AWS accounts — what to set up, by hand, before the first `make plan`

Two accounts were needed on the first run, for one reason: a **new** AWS account starts with a Bedrock daily token quota of 0 (`Model invocation max tokens per day`, e.g. `L-D2912E70` for Nova Micro), marked non-adjustable in Service Quotas, and Converse fails with `ThrottlingException: Too many tokens per day` on a one-word prompt. Only the web chat agent needs Bedrock; Tower, the demo, latency and the tests do not. If your account already has a non-zero quota (`aws service-quotas get-service-quota --service-code bedrock --quota-code L-D2912E70 --region <region> --query Quota.Value`), skip 4.2 and 4.3.

### 4.1 The stack account and its IAM user

The account that runs the stack (here a hackathon-provided *AWS Projects* account). Once, in its console:

1. **IAM → Users → Create user** `ask-the-tower-dev`, no console access needed → attach **AdministratorAccess** (the first apply creates roles, KMS keys, a VPC, Cognito and AgentCore resources; narrow it afterwards if you keep the account) → **Security credentials → Create access key** (CLI) → put the pair in the root `.env` as `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`, with `AWS_REGION` and `AWS_PROFILE=` (empty).
2. **Region.** An organisation's service control policy may pin the account to one region (ours: us-east-2; S3, DynamoDB and Lambda denied elsewhere, Route 53 registration denied). Probe before choosing: `aws s3api list-buckets`, `aws dynamodb list-tables`, `aws bedrock list-foundation-models` in the candidate region — an `AccessDenied` naming an *explicit deny in a service control policy* means move region. The region goes in `.env` (`AWS_REGION`) **and** `deploy/terraform/envs/aws.tfvars` (`region`).
3. **Terraform state** is local (`deploy/terraform/backend.tf`, `backend "local"`): the same SCP denied the S3 bucket and lock table. The state files are gitignored; back them up with the repo folder.
4. **Bedrock → Model access**: enable the Amazon Nova models. On-demand calls need the cross-region inference profile id (`us.amazon.nova-micro-v1:0`), which is the default `bedrock_model_id`.
5. **SNS SMS sandbox**: verify your phone (tier 3, last row).
6. **Route 53**: registration is denied in some sandboxes; a domain bought elsewhere (Porkbun, a few dollars a year for `.xyz`) and delegated to a Route 53 hosted zone works the same: create the zone, copy its four NS records into the registrar. Zone id → `mock_route53_zone_id`.

### 4.2 A management account (only if the stack account is quota-gated)

An AWS account you own, with a payment method and the paid plan, outside the sandbox's organisation. It serves one thing: the model call. Create it at aws.amazon.com (or use an existing one), enable the same Nova models under **Bedrock → Model access** in the **same region** the stack uses, and check that Converse answers there:

```bash
aws bedrock-runtime converse --region us-east-2 --model-id us.amazon.nova-micro-v1:0 \
  --messages '[{"role":"user","content":[{"text":"hi"}]}]' --query 'output.message.content[0].text'
```

Nothing of the stack is deployed in this account; its bill is the model tokens only.

### 4.3 The cross-account Bedrock role

The web chat agent's runtime role (`<name>-agentcore-agent`, in the stack account) assumes a role in the management account for `bedrock:InvokeModel` and nothing else. Files with placeholders: `deploy/terraform/envs/bedrock-role/`. In the **management account's CloudShell** (console → CloudShell icon; it carries that account's identity, so no keys to juggle):

```bash
STACK=<stack account id>; ME=$(aws sts get-caller-identity --query Account --output text)
cat > trust.json <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"AWS":"arn:aws:iam::$STACK:root"},"Action":"sts:AssumeRole","Condition":{"StringEquals":{"aws:PrincipalArn":"arn:aws:iam::$STACK:role/att-dev-agentcore-agent"}}}]}
EOF
cat > policy.json <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"],"Resource":["arn:aws:bedrock:*::foundation-model/*","arn:aws:bedrock:*:$ME:inference-profile/*"]}]}
EOF
aws iam create-role --role-name ask-the-tower-bedrock --assume-role-policy-document file://trust.json --query Role.Arn --output text
aws iam put-role-policy --role-name ask-the-tower-bedrock --policy-name invoke-model --policy-document file://policy.json
```

Then in `deploy/terraform/envs/aws.tfvars`:

```hcl
web_chat_agent          = "bedrock"
bedrock_assume_role_arn = "arn:aws:iam::<management account id>:role/ask-the-tower-bedrock"
```

`make deploy` gives the agent role `sts:AssumeRole` on that ARN and sets `BEDROCK_ASSUME_ROLE_ARN` on the agent runtime; `ref_client.agent.bedrock_client` assumes it with botocore's refreshing credentials. The role name `att-dev-agentcore-agent` follows `name_prefix`-`environment` in `aws.tfvars`.

**Why the trust names the account, not the role:** IAM pins a role-ARN principal to that role's internal id, so a `make down` + `make deploy` (which recreates the agent role) would silently break it — the agent would answer 503 with `AccessDenied` on `sts:AssumeRole`. The `:root` principal plus the `aws:PrincipalArn` condition is the same restriction as a string match, and survives any number of rebuilds.

**Proof from your laptop (optional, two minutes):** add your IAM user to the trust policy's `Principal` list for the test, then from WSL `aws sts assume-role --role-arn <the ARN> --role-session-name wsl-test`, export the three credentials it prints, run the Converse line of 4.2, and put the trust policy back to the runtime role only. The deploy is the real test.

**Fallback without a second account:** `web_chat_agent = "scripted"` — the agent runs the demo phrasebook (the same agent `make demo` uses); every demo line works, free-form phrasing does not. Say so in the submission.

### 4.4 Teardown order, the day after judging

`make down-all` (Ops: *Destroy everything, ECR included*) → `aws sns delete-sms-sandbox-phone-number` → in the management account `aws iam delete-role-policy --role-name ask-the-tower-bedrock --policy-name invoke-model && aws iam delete-role --role-name ask-the-tower-bedrock` → the Route 53 hosted zone (Terraform does not own it) → the IAM user's access key → close the sandbox account from its organisation if it was created for this.

Notes that cost time the first day:

- **Sourcing `.env` with `set -a`** exports empty keys (`AWS_PROFILE=`) and breaks the CLI with `config profile () could not be found`. Run `make` from a shell that has not sourced `.env` (make loads it itself), or `unset AWS_PROFILE AWS_SESSION_TOKEN` first.
- **AWS Projects / sandbox accounts** may pin a region through an organization SCP and deny S3 or DynamoDB elsewhere; the Terraform state is local (`backend "local"`) for that reason, and the region is set in `.env` and `aws.tfvars`, not assumed. See `docs/submission/product-feedback.md`.
- `terraform init` on a Windows-mounted tree can fail with "cannot install package … because it is a symlink": `rm -rf deploy/terraform/.terraform` and run it again.
