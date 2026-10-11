# The same names and defaults as the main root, so `make ecr-up` and `make deploy` agree on the repository names
# (`<name_prefix>-<environment>/<service>`) when both read envs/aws.tfvars.

variable "region" {
  description = "AWS region of the repositories; must be the main root's region (Lambda and Runtime pull same-region)."
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Short project prefix for resource names (same as the main root)."
  type        = string
  default     = "att"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,9}$", var.name_prefix))
    error_message = "name_prefix: 2-10 lowercase letters/digits, starting with a letter."
  }
}

variable "environment" {
  description = "Environment name (same as the main root)."
  type        = string
  default     = "dev"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,9}$", var.environment))
    error_message = "environment: 2-10 lowercase letters/digits, starting with a letter."
  }
}

variable "tags" {
  description = "Extra tags on every resource."
  type        = map(string)
  default     = {}
}
