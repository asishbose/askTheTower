variable "name" {
  type = string
}

variable "region" {
  type = string
}

variable "namespace" {
  description = "Custom metric namespace (alerts counts, AuditReconcileMisses, LineIdOnMetric)."
  type        = string
  default     = "AskTheTower"
}

variable "runtime_arn" {
  type    = string
  default = ""
}

variable "runtime_log_group" {
  type = string
}

variable "alerts_log_group" {
  type = string
}

variable "binding_log_group" {
  type = string
}

variable "alerts_function" {
  type = string
}

variable "reconcile_function" {
  type = string
}

variable "latency_budget_ms" {
  type    = number
  default = 400
}
