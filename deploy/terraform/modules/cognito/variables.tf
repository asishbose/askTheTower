variable "name" {
  description = "Resource name prefix (<name_prefix>-<environment>)."
  type        = string
}

variable "region" {
  type = string
}

variable "domain_prefix" {
  description = "Hosted UI domain prefix; empty = <name>-<6 random hex> (prefixes are global)."
  type        = string
  default     = ""
}

variable "callback_urls" {
  description = "The web chat page URL (Hosted UI callback and logout URL). Empty = no OAuth flow on the client."
  type        = list(string)
  default     = []
}
