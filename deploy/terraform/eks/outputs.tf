# `terraform -chdir=deploy/terraform/eks output -json` -> artifacts/tf-outputs-eks.json -> scripts/render_values.py
# -> deploy/helm/umbrella/values-eks.generated.yaml. The names are a contract with that script; nothing is secret.

output "region" {
  value = var.region
}

output "cluster_name" {
  value = module.eks.cluster_name
}

output "cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "namespace" {
  value = var.namespace
}

output "vpc_id" {
  value = local.aws.vpc_id
}

output "vpc_cidr" {
  description = "NetworkPolicies admit the ALB (target type ip) from this CIDR."
  value       = local.aws.vpc_cidr
}

output "public_subnet_ids" {
  description = "For the Ingresses' alb.ingress.kubernetes.io/subnets (13's subnets carry no elb role tags)."
  value       = local.aws.public_subnet_ids
}

output "irsa_role_arns" {
  description = "Service account -> IRSA role ARN (tower-mcp, binding-page, alerts, ref-client, aws-load-balancer-controller)."
  value       = module.eks.irsa_role_arns
}

output "ecr_repositories" {
  description = "Service -> ECR repository URL (from 13)."
  value       = local.aws.ecr_repositories
}

output "table_prefix" {
  value = local.aws.table_prefix
}

output "kms_key_arn" {
  value = local.aws.kms_key_arn
}

output "kms_hmac_key_arn" {
  value = local.aws.kms_hmac_key_arn
}

output "sns_topic_arns" {
  description = "Topic name -> ARN (from 13; `replies` always, `push` when enabled)."
  value       = local.aws.sns_topic_arns
}

output "bedrock_model_id" {
  value = var.bedrock_model_id
}

output "tower_host" {
  value = var.tower_host
}

output "binding_host" {
  value = var.binding_host
}

output "hooks_host" {
  value = var.hooks_host
}

output "certificate_arn" {
  value = var.certificate_arn
}

output "tower_url" {
  value = local.url.tower
}

output "binding_url" {
  value = local.url.binding
}

output "hooks_base_url" {
  value = local.url.hooks
}

output "mock_url" {
  description = "Empty: the mock carrier is in-cluster only (its Service DNS name is set by the chart)."
  value       = ""
}

output "carrier_backend" {
  value = "mock"
}
