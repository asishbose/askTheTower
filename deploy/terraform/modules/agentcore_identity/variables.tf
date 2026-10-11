variable "name" {
  type = string
}

variable "issuer" {
  type = string
}

variable "token_url" {
  type = string
}

variable "authorize_url" {
  type = string
}

variable "service_client" {
  description = "{id, secret} for the client-credentials APIs."
  type        = object({ id = string, secret = string })
  sensitive   = true
}

variable "binding_client" {
  description = "{id, secret} for Number Verification (auth code)."
  type        = object({ id = string, secret = string })
  sensitive   = true
}

variable "return_urls" {
  description = "Allowed OAuth return URLs (the binding page callback)."
  type        = list(string)
}

variable "private_endpoint" {
  description = "Managed VPC resource for a carrier that is only reachable inside the VPC (the mock); null for a sandbox."
  type = object({
    vpc_id             = string
    subnet_ids         = list(string)
    security_group_ids = list(string)
    routing_domain     = string
  })
  default = null
}
