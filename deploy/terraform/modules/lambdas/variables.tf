variable "name" {
  type = string
}

variable "region" {
  type = string
}

variable "account_id" {
  type = string
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "images" {
  description = "Function -> image URI: binding-page, alerts, reconcile (the alerts image)."
  type        = map(string)
}

variable "table_arns" {
  type = list(string)
}

variable "kms_key_arns" {
  type = list(string)
}

variable "kms_key_arn" {
  description = "Key for Lambda environment variables at rest."
  type        = string
}

variable "gateway_arn" {
  type = string
}

variable "sns_topic_arns" {
  description = "Topic name -> ARN (replies, optional push)."
  type        = map(string)
}

variable "trace_log_groups" {
  description = "Log groups the reconciliation Lambda may query (spans and the Runtime log group)."
  type        = list(string)
}

variable "enable_cloudfront_waf" {
  type    = bool
  default = true
}

variable "hooks_rate_limit" {
  type    = number
  default = 300

  validation {
    condition     = var.hooks_rate_limit >= 100
    error_message = "WAF rate-based rules need a limit of at least 100."
  }
}

variable "carrier_authorize_passthrough" {
  description = "Public GET route to the mock's /oauth2/authorize over the VPC link; null for a sandbox."
  type = object({
    vpc_link_id       = string
    listener_arn      = string
    server_name       = string
    upstream_path     = string
    route_path_prefix = string
  })
  default = null
}

variable "binding_vpc" {
  type = object({
    subnet_ids         = list(string)
    security_group_ids = list(string)
  })
  default = null
}

variable "common_environment" {
  type = map(string)
}

variable "gateway_environment" {
  type = map(string)
}

variable "binding_environment" {
  type      = map(string)
  sensitive = true
}

variable "binding_uses_gateway" {
  type    = bool
  default = true
}

variable "alerts_environment" {
  type      = map(string)
  sensitive = true
}

variable "reconcile_environment" {
  type = map(string)
}
