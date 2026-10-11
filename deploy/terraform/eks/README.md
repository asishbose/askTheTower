# deploy/terraform/eks — the EKS portability target (prompt 14)

A second Terraform root. It creates one small EKS cluster that runs the same five images as compose and as
AgentCore/Fargate/Lambda (installed from `deploy/helm/umbrella`). The workloads use prompt 13's DynamoDB tables,
KMS keys and SNS topic. It is a separate root, so `make down-eks` destroys only the cluster and `make down ENV=aws`
never touches it.

**Status (autonomous build):** `fmt` clean, `validate` passes (`init -backend=false`), and `terraform test` in
`../modules/eks` passes (mock provider). It has never been planned or applied: there were no AWS credentials.

## What it creates

- `../modules/eks`:
  - EKS (1.31 by default; 1.30+), with API authentication mode (access entries; the identity that applies is
    cluster-admin).
  - Control-plane logs (`api`, `audit`, `authenticator`) to `/aws/eks/<name>/cluster`, with retention.
  - One managed node group of 2 x `t4g.medium` (arm64, `AL2023_ARM_64_STANDARD`), ON_DEMAND.
  - Addons: `vpc-cni` with the network-policy agent on (`enableNetworkPolicy`, so the charts' NetworkPolicies are
    enforced), `kube-proxy` and `coredns`. Optionally `amazon-cloudwatch-observability` (`enable_container_logs`).
  - IRSA: an OIDC provider and one role per service account (`tower-mcp`, `binding-page`, `alerts`, `ref-client` in
    `namespace`; `aws-load-balancer-controller` in `kube-system`). Each role's trust is pinned to
    `system:serviceaccount:<ns>:<sa>` and `aud = sts.amazonaws.com`.
- The AWS Load Balancer Controller (Helm chart 1.11.0 = v2.11.0, the version the module's IAM policy comes from).

Networking: it reuses 13's VPC. 13 has no NAT by default, and its public subnets do not map public IPs on launch.
So the nodes go in the public subnets, and the node launch template requests a public IPv4 address. The nodes'
only security group is the EKS cluster security group. Set `use_private_subnets = true` only together with 13's
`enable_nat_gateway = true`.

13's subnets carry no `kubernetes.io/role/elb` tags. Tagging them from here would make the two roots fight over
the tags. Instead, the charts' Ingresses name their subnets explicitly with `alb.ingress.kubernetes.io/subnets`,
taken from the `public_subnet_ids` output.

## Reads 13's state

`data "terraform_remote_state" "aws"` reads `aws_state_bucket` / `aws_state_key` (default
`ask-the-tower/aws/terraform.tfstate`). From that state it uses `name`, `region`, `vpc_id`, `vpc_cidr`, the subnet
ids, `table_arns`, the KMS ARNs, `sns_topic_arns`, `ecr_repositories` and `table_prefix`. 13's stack must have been
applied with the outputs this prompt added (`vpc_id`, `vpc_cidr`, `public_subnet_ids`, `private_subnet_ids`,
`table_arns`, `enable_nat_gateway`). If its state predates them, re-run `make deploy ENV=aws` (or
`terraform apply -refresh-only`) first.

## Run order

```bash
make deploy ENV=aws                    # 13 first: VPC, tables, keys, SNS, ECR (+ make push for the images)
cp deploy/terraform/eks/envs/eks.tfvars.example deploy/terraform/eks/envs/eks.tfvars    # fill in (gitignored)
terraform -chdir=deploy/terraform/eks init -backend-config="bucket=<b>" -backend-config="dynamodb_table=<t>"
make deploy-eks                        # apply -> scripts/render_values.py -> helm upgrade --install -> seed Job
make kube-context                      # kubectl against the cluster
make demo ENV=eks
make down-eks                          # helm uninstall (ALBs go first) -> terraform destroy of this root only
```

`aws eks get-token` (the AWS CLI) must be on PATH: the Helm provider authenticates with it.

## Cost

The control plane is about $0.10/h. Two `t4g.medium` on-demand nodes are about $0.07/h. Add an ALB per public
Ingress (about $0.02/h each) and the CloudWatch logs. That is roughly $0.20–0.25/h, about $5 for a 24-hour
showcase window. Tear it down after the showcase. Record the measured window cost in `artifacts/cost.md`.

## Teardown

`make down-eks` uninstalls the chart, so the controller deletes its ALBs and target groups, then destroys this
root. Then check that nothing is left: no cluster, node group, load balancer or `eks-*`/`k8s-*` ENIs or security
groups in 13's VPC. If the controller was already gone when the Ingresses were deleted, remove leftover `k8s-*`
load balancers and security groups by hand, otherwise 13's VPC cannot be destroyed later.
