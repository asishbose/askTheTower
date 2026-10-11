variable "name" {
  type = string
}

variable "kms_key_arn" {
  type = string
}

variable "alerts_function_arn" {
  type = string
}

variable "alerts_function_name" {
  type = string
}

variable "sms_monthly_spend_limit" {
  type    = number
  default = 1
}

variable "enable_push_topic" {
  type    = bool
  default = false
}
