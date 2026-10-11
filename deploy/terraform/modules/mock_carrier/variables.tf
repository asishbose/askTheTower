variable "name" {
  type = string
}

variable "region" {
  type = string
}

variable "image" {
  description = "mock-carrier image URI (ECR)."
  type        = string
}

variable "vpc_id" {
  type = string
}

variable "vpc_cidr" {
  type = string
}

variable "alb_subnet_ids" {
  description = "Private subnets for the internal ALB."
  type        = list(string)
}

variable "task_subnet_ids" {
  description = "Subnets for the task: public (with a public IP) unless the VPC has a NAT gateway."
  type        = list(string)
}

variable "assign_public_ip" {
  type = bool
}

variable "domain_name" {
  type    = string
  default = ""
}

variable "route53_zone_id" {
  type    = string
  default = ""
}

variable "certificate_arn" {
  type    = string
  default = ""
}

variable "scenario" {
  type    = string
  default = "demo"
}

variable "kms_key_arn" {
  type = string
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "client_secrets" {
  description = "Client id -> secret for the mock's registry: tower, alerts, binding-page."
  type        = map(string)
  sensitive   = true
}

variable "binding_redirect_prefixes" {
  description = "Where the mock may send a Number Verification code back to."
  type        = list(string)
}

variable "assume_mobile_data" {
  description = "MOCK_ASSUME_MOBILE_DATA (08 §3): attribute every authorize request to assume_client_id. A simulation aid."
  type        = bool
  default     = false
}

variable "assume_client_id" {
  description = "MOCK_ASSUME_CLIENT_ID: the scenario's mobile_data_client_ids entry assumed (one line is \"the phone\")."
  type        = string
  default     = "phone-asish"
}

variable "admin_token" {
  description = "MOCK_ADMIN_TOKEN (08 §3, G1): the bearer for mutating /_admin routes; key admin_token of the registry secret. The demo admin bearer while the admin path is open (10 §2.1)."
  type        = string
  sensitive   = true
}

variable "admin_paths_enabled" {
  description = "Forward the demo UI's /_admin paths (never /_admin/sink) from the internal ALB: the demo admin path (10 §2.1)."
  type        = bool
  default     = false
}
