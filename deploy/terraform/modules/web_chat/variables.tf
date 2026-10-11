variable "name" {
  description = "Resource name prefix (<name_prefix>-<environment>)."
  type        = string
}

variable "agent_invoke_url" {
  description = "AGENT_INVOKE_URL: the web chat agent runtime's invocation URL (https)."
  type        = string
}

variable "proxy_source" {
  description = "Path of services/web-chat/proxy/handler.py (zipped as handler.py)."
  type        = string
}

variable "log_retention_days" {
  type    = number
  default = 14
}
