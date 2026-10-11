# Inputs of the EKS portability target (prompt 14). Everything AWS-side the workloads use (tables, keys, topics,
# ECR, VPC) comes from prompt 13's root through deploy/terraform/eks; this module only adds the cluster and IAM.

variable "name" {
  description = "Cluster name (and prefix for the IAM roles and log group)."
  type        = string
}

variable "region" {
  type = string
}

variable "account_id" {
  type = string
}

variable "partition" {
  description = "aws, aws-cn or aws-us-gov (from aws_partition in the root)."
  type        = string
  default     = "aws"
}

variable "cluster_version" {
  description = "Kubernetes version of the control plane (1.30 or later)."
  type        = string
  default     = "1.31"

  validation {
    condition     = can(regex("^1\\.(3[0-9]|[4-9][0-9])$", var.cluster_version))
    error_message = "cluster_version must be 1.30 or later (e.g. \"1.31\")."
  }
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  description = <<-EOT
    Subnets for the control-plane ENIs and the node group (two AZs). The root passes 13's public subnets by default
    (no NAT: nodes get a public IP from the launch template), or the private ones when use_private_subnets is set.
  EOT
  type        = list(string)

  validation {
    condition     = length(var.subnet_ids) >= 2
    error_message = "EKS needs subnets in at least two availability zones."
  }
}

variable "nodes_public_ip" {
  description = "Give nodes a public IPv4 address (needed in public subnets without NAT, to reach ECR, STS, EKS)."
  type        = bool
  default     = true
}

variable "endpoint_public_access_cidrs" {
  description = "CIDRs allowed to reach the public Kubernetes API endpoint (helm and kubectl from the laptop)."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "admin_principal_arns" {
  description = "Extra IAM principals given cluster-admin through EKS access entries (the creator always is)."
  type        = list(string)
  default     = []
}

variable "node_instance_types" {
  type    = list(string)
  default = ["t4g.medium"]
}

variable "node_ami_type" {
  description = "Must match the instance architecture: arm64 (Graviton) for t4g/m7g."
  type        = string
  default     = "AL2023_ARM_64_STANDARD"
}

variable "node_desired" {
  type    = number
  default = 2
}

variable "node_min" {
  type    = number
  default = 2
}

variable "node_max" {
  type    = number
  default = 3
}

variable "node_disk_gb" {
  type    = number
  default = 20
}

variable "capacity_type" {
  description = "ON_DEMAND (default: a few-hour showcase must not lose nodes mid-demo) or SPOT."
  type        = string
  default     = "ON_DEMAND"

  validation {
    condition     = contains(["ON_DEMAND", "SPOT"], var.capacity_type)
    error_message = "capacity_type must be ON_DEMAND or SPOT."
  }
}

variable "namespace" {
  description = "Namespace the umbrella chart installs into; the IRSA trust is pinned to it."
  type        = string
  default     = "ask-the-tower"
}

variable "table_arns" {
  description = "Table name -> ARN from 13's root (Users, Lines, Grants, Watches, Audit, BindTokens, AlertsState)."
  type        = map(string)

  validation {
    condition = alltrue([
      for t in ["Users", "Lines", "Grants", "Watches", "Audit", "BindTokens", "AlertsState"] : contains(keys(var.table_arns), t)
    ])
    error_message = "table_arns must name all seven tables."
  }
}

variable "kms_key_arn" {
  description = "Symmetric CMK (msisdn_enc envelope): TOWER_KMS_KEY_ID."
  type        = string
}

variable "kms_hmac_key_arn" {
  description = "HMAC_256 key (line_id, audit trim marker): TOWER_KMS_HMAC_KEY_ID."
  type        = string
}

variable "sns_topic_arns" {
  description = "SNS topic ARNs Alerts may publish to (13's replies topic, optional push topic)."
  type        = list(string)
  default     = []
}

variable "bedrock_model_id" {
  description = "Model the reference client invokes; a geo prefix (us., eu., apac., ...) means an inference profile."
  type        = string
  default     = "amazon.nova-micro-v1:0"
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "control_plane_log_types" {
  type    = list(string)
  default = ["api", "audit", "authenticator"]
}

variable "enable_container_logs" {
  description = "Install the amazon-cloudwatch-observability addon (container logs + Container Insights; extra cost)."
  type        = bool
  default     = false
}

variable "tags" {
  type    = map(string)
  default = {}
}
