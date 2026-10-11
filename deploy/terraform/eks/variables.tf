# Inputs of the EKS root (prompt 14). Everything shared with 13's stack (VPC, tables, keys, topics, ECR, the name
# prefix) is read from 13's remote state; envs/eks.tfvars.example shows what a human sets.

variable "region" {
  description = "AWS region; must be the region of 13's stack (checked against its state)."
  type        = string
  default     = "us-east-1"
}

variable "tags" {
  description = "Extra tags on every resource."
  type        = map(string)
  default     = {}
}

# --- 13's state ---------------------------------------------------------------------------------------------------

variable "aws_state_bucket" {
  description = "S3 bucket holding 13's state (the same bucket as backend.tf here)."
  type        = string
  default     = "REPLACE-ME-ask-the-tower-tfstate"
}

variable "aws_state_key" {
  description = "Key of 13's state in that bucket."
  type        = string
  default     = "ask-the-tower/aws/terraform.tfstate"
}

variable "aws_state_region" {
  description = "Region of the state bucket (empty = var.region)."
  type        = string
  default     = ""
}

# --- cluster ------------------------------------------------------------------------------------------------------

variable "cluster_version" {
  description = "Kubernetes version (1.30+)."
  type        = string
  default     = "1.31"
}

variable "node_instance_types" {
  type    = list(string)
  default = ["t4g.medium"]
}

variable "node_desired" {
  type    = number
  default = 2
}

variable "capacity_type" {
  description = "ON_DEMAND (default) or SPOT."
  type        = string
  default     = "ON_DEMAND"
}

variable "use_private_subnets" {
  description = <<-EOT
    Put the nodes (and control-plane ENIs) in 13's private subnets. Needs 13's enable_nat_gateway = true (nodes
    must reach ECR, STS and the EKS API). Default false: public subnets, nodes get a public IP, no NAT cost.
  EOT
  type        = bool
  default     = false
}

variable "endpoint_public_access_cidrs" {
  description = "CIDRs allowed to reach the public Kubernetes API endpoint."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "admin_principal_arns" {
  description = "Extra IAM principals given cluster-admin (the identity that applies is admin already)."
  type        = list(string)
  default     = []
}

variable "enable_container_logs" {
  description = "amazon-cloudwatch-observability addon: container logs to CloudWatch (extra cost)."
  type        = bool
  default     = false
}

variable "log_retention_days" {
  type    = number
  default = 7
}

variable "lb_controller_chart_version" {
  description = "aws-load-balancer-controller chart (1.11.0 = controller v2.11.0, matching the module's IAM policy)."
  type        = string
  default     = "1.11.0"
}

# --- workloads ----------------------------------------------------------------------------------------------------

variable "namespace" {
  description = "Namespace of the umbrella chart; the IRSA trust is pinned to it."
  type        = string
  default     = "ask-the-tower"
}

variable "bedrock_model_id" {
  description = "Model the reference client invokes (IRSA allows only this one)."
  type        = string
  default     = "amazon.nova-micro-v1:0"
}

variable "tower_host" {
  description = "DNS name for Tower's ALB ingress (empty = no public host; reach it with port-forward)."
  type        = string
  default     = ""
}

variable "binding_host" {
  description = "DNS name for the binding page's public ALB ingress (TLS with certificate_arn)."
  type        = string
  default     = ""
}

variable "hooks_host" {
  description = "DNS name for Alerts' /hooks/* ingress (CAMARA webhook sinks)."
  type        = string
  default     = ""
}

variable "certificate_arn" {
  description = "ACM certificate covering the hosts above (in var.region), for the ALB HTTPS listeners."
  type        = string
  default     = ""
}
