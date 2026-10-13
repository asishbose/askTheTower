# Prerequisites

What has to be on the machine before `make` does anything. Three tiers: the local demo needs only the first; the AWS deployment (from the terminal or the demo UI's Ops tab) needs all three. Verified on Windows 11 with WSL2 (Ubuntu 24.04); the same packages apply on Linux and macOS.

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
| An AWS account with credentials in the root `.env` | `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` (or `AWS_PROFILE`) and `AWS_REGION`; the IAM user needs AdministratorAccess for the first apply | see [Configuration](README.md#configuration) | `make config-check`; `aws sts get-caller-identity` |
| A public DNS zone in Route 53 | the Fargate mock's internal ALB needs a certificate (`mock_domain_name` + `mock_route53_zone_id` in `deploy/terraform/envs/aws.tfvars`); a domain bought anywhere and delegated to Route 53 works | [`docs/runbooks/aws-first-run.md`](docs/runbooks/aws-first-run.md) §0.4 | `aws route53 list-hosted-zones` |
| Bedrock model access: Amazon Nova Micro | the web chat agent's model; without it every question answers 500 | console → Bedrock → Model access | — |
| SNS SMS sandbox: your number verified | the sandbox drops SMS to unverified numbers, so Moment 3's buzz never arrives | `aws sns create-sms-sandbox-phone-number` → `verify-sms-sandbox-phone-number` | `aws sns list-sms-sandbox-phone-numbers` shows `Verified` |

Notes that cost time the first day:

- **Sourcing `.env` with `set -a`** exports empty keys (`AWS_PROFILE=`) and breaks the CLI with `config profile () could not be found`. Run `make` from a shell that has not sourced `.env` (make loads it itself), or `unset AWS_PROFILE AWS_SESSION_TOKEN` first.
- **AWS Projects / sandbox accounts** may pin a region through an organization SCP and deny S3 or DynamoDB elsewhere; the Terraform state is local (`backend "local"`) for that reason, and the region is set in `.env` and `aws.tfvars`, not assumed. See `docs/submission/product-feedback.md`.
- `terraform init` on a Windows-mounted tree can fail with "cannot install package … because it is a symlink": `rm -rf deploy/terraform/.terraform` and run it again.
