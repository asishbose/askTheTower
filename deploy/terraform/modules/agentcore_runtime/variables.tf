variable "name" {
  type = string
}

variable "region" {
  type = string
}

variable "account_id" {
  type = string
}

variable "image" {
  description = "tower-mcp image URI (ECR, arm64)."
  type        = string
}

variable "ref_client_image" {
  description = "ref-client image URI: its default command is the web chat agent's HTTP app."
  type        = string
  default     = ""
}

variable "enable_agent" {
  description = "Create the web chat agent runtime (root variable enable_web_chat)."
  type        = bool
  default     = false
}

variable "bedrock_model_id" {
  type    = string
  default = "amazon.nova-micro-v1:0"
}

variable "binding_base_url" {
  description = "WEB_CHAT_BINDING_BASE_URL: the agent drops a bind_line URL outside <this>/bind/ (09 §6.2 rule 5)."
  type        = string
  default     = ""
}

variable "agent_jwt_discovery_url" {
  description = "The agent runtime's custom_jwt_authorizer discovery URL (the Cognito pool)."
  type        = string
  default     = ""
}

variable "agent_jwt_allowed_clients" {
  description = "Allowed client_id values for the agent runtime (the web-chat app client)."
  type        = list(string)
  default     = []
}

variable "environment" {
  description = "Tower's environment (services/tower-mcp/README.md). Holds no carrier credential on the Gateway path."
  type        = map(string)
  sensitive   = true
}

variable "table_arns" {
  type = list(string)
}

variable "kms_key_arns" {
  type = list(string)
}

variable "gateway_arn" {
  type = string
}

variable "jwt_discovery_url" {
  type    = string
  default = ""
}

variable "jwt_allowed_audience" {
  type    = list(string)
  default = []
}

variable "jwt_allowed_clients" {
  type    = list(string)
  default = []
}

variable "vpc" {
  description = "Cut-line only: run the Runtime in these private subnets (DirectClient -> internal ALB)."
  type = object({
    subnet_ids         = list(string)
    security_group_ids = list(string)
  })
  default = null
}

variable "log_retention_days" {
  type    = number
  default = 14
}
