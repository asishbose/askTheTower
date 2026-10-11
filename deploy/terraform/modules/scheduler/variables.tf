variable "name" {
  type = string
}

variable "alerts_function_arn" {
  type = string
}

variable "reconcile_function_arn" {
  type = string
}

variable "timezone" {
  type    = string
  default = "America/Toronto"
}

variable "kms_key_arn" {
  type = string
}

variable "enabled" {
  description = "Pause every schedule without destroying anything."
  type        = bool
  default     = true
}
