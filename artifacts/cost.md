# Cost — AWS column (and the EKS line)

> **ESTIMATE, NOT MEASURED.** The autonomous build had no AWS credentials, so nothing was deployed and Cost
> Explorer has nothing to show. The rows below are worked from public on-demand list prices for us-east-1 as the
> build agent understood them. Re-check every rate with the AWS Pricing Calculator before quoting any of them.

## Fixed cost of the Terraform as written (24/7, us-east-1, per month ≈ 730 h)

| Item | Sizing in `deploy/terraform` | ≈ USD / month |
|---|---|---:|
| Internal ALB (mock carrier) | 1 ALB, minimal LCU | 17 |
| Fargate (mock carrier) | 1 task, 0.25 vCPU / 0.5 GB, arm64 | 6 |
| Public IPv4 on the Fargate task | 1 address (no NAT gateway by design) | 3.7 |
| WAF on CloudFront | 1 web ACL + 1 rate rule (`enable_cloudfront_waf`) | 6 |
| KMS | 2 customer keys (symmetric + HMAC), demo request volume | 2 |
| CloudWatch | dashboard (beyond the 3 free), 3 alarms, ~6 custom metrics, small logs | 5 |
| Secrets Manager | 1 secret (the mock's own registry) | 0.4 |
| Route 53 | hosted zone for `mock_domain_name`, if created for this | 0.5 |
| ECR | ~1 GB of images | 0.1 |
| **Fixed subtotal** | | **≈ 40** |

## Usage-priced, at demo volume

| Item | Demo volume | ≈ USD / month |
|---|---|---:|
| AgentCore Runtime (Tower) | billed for active vCPU/GB-seconds: a few hundred short MCP calls | < 1 |
| AgentCore Gateway + Identity | a few thousand tool calls / token fetches | < 1 |
| Lambda (3 functions) + Scheduler | ~19k scheduled invocations + demo traffic (free tier) | 0 |
| API Gateway HTTP API + CloudFront | demo traffic | < 0.1 |
| DynamoDB on-demand (7 tables, PITR on Audit) | kilobytes | < 1 |
| SNS SMS (US) | ~50 messages; + an origination number for two-way replies | 1–3 |
| Bedrock (reference client, dev-time tokens) | Nova Micro, corpus runs | 1–5 |
| Web chat: Cognito user pool (prompt 20) | 2 users, a few dozen sign-ins (free tier: 10k MAU on Lite) | 0 |
| Web chat: agent runtime (AgentCore Runtime, HTTP) | billed per active request: tens of chat turns, ~1–3 s each | < 1 |
| Web chat: Bedrock tokens from the page | ~2 Converse calls × ~1.2 k input tokens per turn, Nova Micro | < 0.1 |
| Web chat: S3 + CloudFront + Lambda URL proxy | 4 objects (~20 KB), demo traffic, free-tier Lambda | < 0.1 |

The web chat (`enable_web_chat`, default true) adds no fixed cost: no ALB, no NAT, no always-on task. Estimate
from list prices, not measured (no AWS in the autonomous build, 2026-10-09).

## Against the deck's "about $12/month"

The claim (10 §7, tracks slide note) does **not** hold for this Terraform running 24/7. The estimate is
**≈ $45–50/month**, and the internal ALB and the WAF dominate it, not AgentCore. There are three honest ways to
state it:

- **Showcase window** (deploy, demo, `make down` within 48 h): ≈ $3–4 in total. That is how prompt 13 uses the
  stack, and `make down` takes it back to zero (KMS keys wait out a 7-day deletion window at about $0.07/day).
- **Always-on at ≈ $12**: needs the mock off the ALB, which means no Gateway private endpoint. Set
  `enable_cloudfront_waf = false` (−$6) and scale the mock service to 0 when idle (−$6). The ALB is the
  irreducible line while the mock sits behind Gateway.
- **Against a carrier sandbox** (`carrier_backend = "sandbox"`): no mock, ALB, Fargate, public IP or Route 53.
  This is ≈ $15/month with WAF and ≈ $9 without, close to the deck's figure.

TODO(human): after a real deploy, record Cost Explorer for 48 h (filter on tag `project = ask-the-tower`) and the
Pricing Calculator link here. Then edit the deck's tracks-slide note if the number moved; this estimate says it
will.

## EKS line (prompt 14, separate stack)

Control plane ($0.10/h) + 2 × t4g.medium on-demand (≈ $0.034/h each) + one ALB for the three ingresses (ingress
group; ≈ $0.0225/h + LCU) + two node public IPv4 addresses (≈ $0.005/h each) + a few GB of EBS (gp3, 2 × 20 GB ≈
$0.005/h) + control-plane logs: ≈ $0.21/h, so ≈ $5 per 24 h showcase window and ≈ $150/month if it were left up —
which is why `make down-eks` is part of the runbook. No NAT gateway (nodes in the public subnets of 13's VPC).
Measured figure: TODO(human) after `make deploy-eks` / `make down-eks` (Cost Explorer, tag `project = ask-the-tower`),
then the one sentence for the tracks slide note: "EKS target demonstrated and torn down; cost of that window: $X".
