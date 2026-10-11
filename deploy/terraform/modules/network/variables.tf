variable "name" {
  type = string
}

variable "vpc_cidr" {
  type = string
}

variable "az_count" {
  type    = number
  default = 2

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 4
    error_message = "az_count must be 2-4 (an ALB needs two zones)."
  }
}

variable "enable_nat_gateway" {
  type    = bool
  default = false
}
