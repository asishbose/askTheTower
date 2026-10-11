# EKS portability target (prompt 14): a SECOND Terraform root, so `make down-eks` destroys only the cluster.
#
#   13's root (deploy/terraform)  -- remote state -->  this root
#     VPC + subnets, DynamoDB tables, KMS keys, SNS topics, ECR repositories, name prefix
#
# This root adds the cluster, node group, IRSA roles (../modules/eks) and the AWS Load Balancer Controller. The
# five workloads are installed by `make deploy-eks` with Helm (deploy/helm/umbrella), from values that
# scripts/render_values.py renders out of `terraform output -json` here.

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

data "terraform_remote_state" "aws" {
  backend = "s3"
  config = {
    bucket = var.aws_state_bucket
    key    = var.aws_state_key
    region = var.aws_state_region != "" ? var.aws_state_region : var.region
  }
}

locals {
  aws  = data.terraform_remote_state.aws.outputs
  name = "${local.aws.name}-eks"

  subnet_ids = var.use_private_subnets ? local.aws.private_subnet_ids : local.aws.public_subnet_ids

  url = {
    tower   = var.tower_host != "" ? "https://${var.tower_host}" : ""
    binding = var.binding_host != "" ? "https://${var.binding_host}" : ""
    hooks   = var.hooks_host != "" ? "https://${var.hooks_host}" : ""
  }
}

check "same_region_as_13" {
  assert {
    condition     = local.aws.region == var.region
    error_message = "var.region must equal the region of 13's stack (its tables, keys and VPC live there)."
  }
}

check "private_subnets_need_nat" {
  assert {
    condition     = !var.use_private_subnets || try(local.aws.enable_nat_gateway, false)
    error_message = "use_private_subnets = true needs enable_nat_gateway = true in 13's stack (nodes need egress)."
  }
}

module "eks" {
  source = "../modules/eks"

  name       = local.name
  region     = var.region
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition

  cluster_version              = var.cluster_version
  vpc_id                       = local.aws.vpc_id
  subnet_ids                   = local.subnet_ids
  nodes_public_ip              = !var.use_private_subnets
  endpoint_public_access_cidrs = var.endpoint_public_access_cidrs
  admin_principal_arns         = var.admin_principal_arns

  node_instance_types = var.node_instance_types
  node_desired        = var.node_desired
  capacity_type       = var.capacity_type

  namespace        = var.namespace
  table_arns       = local.aws.table_arns
  kms_key_arn      = local.aws.kms_key_arn
  kms_hmac_key_arn = local.aws.kms_hmac_key_arn
  sns_topic_arns   = values(local.aws.sns_topic_arns)
  bedrock_model_id = var.bedrock_model_id

  log_retention_days    = var.log_retention_days
  enable_container_logs = var.enable_container_logs
  tags                  = var.tags
}

# --- AWS Load Balancer Controller (ALB ingresses for Tower, the binding page and /hooks/*) -------------------------
# 13's subnets carry no kubernetes.io/role/elb tags (tagging them from here would fight 13's root over the tags),
# so the charts' Ingresses name their subnets explicitly (alb.ingress.kubernetes.io/subnets = public_subnet_ids).

resource "helm_release" "aws_load_balancer_controller" {
  name       = "aws-load-balancer-controller"
  repository = "https://aws.github.io/eks-charts"
  chart      = "aws-load-balancer-controller"
  version    = var.lb_controller_chart_version
  namespace  = "kube-system"
  wait       = true
  timeout    = 600

  values = [yamlencode({
    clusterName  = module.eks.cluster_name
    region       = var.region
    vpcId        = local.aws.vpc_id # explicit: pods cannot reach IMDS (hop limit 1), so no metadata lookup
    replicaCount = 1
    serviceAccount = {
      create = true
      name   = "aws-load-balancer-controller"
      annotations = {
        "eks.amazonaws.com/role-arn" = module.eks.irsa_role_arns["aws-load-balancer-controller"]
      }
    }
  })]

  # Nodes (and CoreDNS) must exist before the controller's pods can schedule; on destroy the release goes first,
  # so the controller can still delete the ALBs it created.
  depends_on = [module.eks]
}
