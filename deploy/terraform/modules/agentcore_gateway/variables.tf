variable "name" {
  type = string
}

variable "account_id" {
  type = string
}

variable "region" {
  type = string
}

variable "specs_dir" {
  description = "specs/camara — the only source of paths and versions."
  type        = string
}

variable "carrier_base_url" {
  description = "apiRoot of the carrier: the mock's https hostname or a sandbox URL. Changing it is the backend swap."
  type        = string
}

variable "service_provider_arn" {
  type = string
}

variable "binding_provider_arn" {
  type = string
}

variable "binding_return_url" {
  type = string
}

variable "target_scopes" {
  description = "API name -> scopes; default [<api name>]."
  type        = map(list(string))
  default     = {}
}

variable "private_endpoint" {
  type = object({
    vpc_id             = string
    subnet_ids         = list(string)
    security_group_ids = list(string)
    routing_domain     = string
  })
  default = null
}

variable "kms_key_arn" {
  type = string
}

variable "identity_secret_arns" {
  type    = list(string)
  default = []
}

variable "skip_apis" {
  description = "API names (specs/camara/*.yaml stems) not registered as targets, e.g. number-verification when the binding page uses DirectClient."
  type        = set(string)
  default     = []
}
