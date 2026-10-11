variable "name" {
  type = string
}

variable "account_id" {
  type = string
}

variable "partition" {
  type    = string
  default = "aws"
}

variable "user_role_arns" {
  description = "Roles that encrypt/decrypt and MAC: the Runtime role and the three Lambda roles."
  type        = list(string)
  default     = []
}

variable "service_principals" {
  description = "AWS services that encrypt with the main key (CloudWatch Logs, SNS, EventBridge)."
  type        = list(string)
  default     = []
}

variable "deletion_window_in_days" {
  description = "7 is the minimum; `make down` schedules deletion, it cannot be immediate."
  type        = number
  default     = 7
}
