# `make outputs` writes these to artifacts/tf-outputs.json and scripts/render_env.py turns them into
# deploy/.env.aws (the endpoints `make demo ENV=aws` and `make test-e2e ENV=aws` use). Nothing here is a secret;
# the generated bearer/session secrets stay in state and are read with `terraform output -raw` only when needed.

output "region" {
  value = var.region
}

output "name" {
  description = "Resource name prefix (<name_prefix>-<environment>)."
  value       = local.name
}

output "tower_mcp_url" {
  description = "Tower's MCP endpoint on AgentCore Runtime (Streamable HTTP; the Alexa+ track's target)."
  value       = module.agentcore_runtime.invoke_url
}

output "tower_runtime_arn" {
  value = module.agentcore_runtime.runtime_arn
}

output "tower_log_group" {
  value = module.agentcore_runtime.log_group_name
}

output "agent_runtime_arn" {
  description = "The web chat agent runtime (protocol HTTP); empty with enable_web_chat = false."
  value       = module.agentcore_runtime.agent_runtime_arn
}

# --- web chat (09 §6): read by `make web-chat-sync`, `make web-chat-url` and scripts/cognito_user.py ------------

output "cognito_pool_id" {
  value = module.cognito.pool_id
}

output "cognito_client_id" {
  description = "The web-chat app client (PKCE, no secret)."
  value       = module.cognito.client_id
}

output "cognito_issuer" {
  value = module.cognito.issuer
}

output "cognito_jwks_url" {
  value = module.cognito.jwks_url
}

output "cognito_hosted_ui_url" {
  description = "The page's COGNITO_DOMAIN."
  value       = module.cognito.hosted_ui_url
}

output "web_chat_url" {
  description = "The page (CloudFront); also the Hosted UI callback URL and the page's REDIRECT_URI."
  value       = var.enable_web_chat ? module.web_chat[0].page_url : ""
}

output "web_chat_bucket" {
  value = var.enable_web_chat ? module.web_chat[0].bucket : ""
}

output "web_chat_distribution_id" {
  value = var.enable_web_chat ? module.web_chat[0].distribution_id : ""
}

output "agent_url" {
  description = "The page's AGENT_URL: the Lambda function URL proxy + /invocations."
  value       = var.enable_web_chat ? module.web_chat[0].agent_url : ""
}

output "binding_url" {
  description = "Public base URL of the binding page (CloudFront when enabled, else the HTTP API)."
  value       = local.public_base_url
}

output "hooks_base_url" {
  description = "Base of the CAMARA webhook sinks: <hooks_base_url>/hooks/{kind}/{sink_token}."
  value       = local.public_base_url
}

output "api_endpoint" {
  description = "The raw HTTP API endpoint behind CloudFront."
  value       = module.lambdas.api_endpoint
}

output "gateway_url" {
  description = "AgentCore Gateway MCP endpoint (IAM-authorised)."
  value       = module.agentcore_gateway.gateway_url
}

output "gateway_id" {
  value = module.agentcore_gateway.gateway_id
}

output "gateway_target_ids" {
  description = "Gateway target id per CAMARA API."
  value       = module.agentcore_gateway.target_ids
}

output "carrier_backend" {
  value = var.carrier_backend
}

output "carrier_base_url" {
  description = "The apiRoot Gateway calls (the mock's internal hostname, or the sandbox)."
  value       = local.carrier.base_url
}

output "mock_cluster_name" {
  description = "ECS cluster of the mock (scripts/aws_seed.py uses `aws ecs execute-command` against it)."
  value       = local.mock ? module.mock_carrier[0].cluster_name : ""
}

output "mock_service_name" {
  value = local.mock ? module.mock_carrier[0].service_name : ""
}

output "mock_container_name" {
  value = local.mock ? module.mock_carrier[0].container_name : ""
}

output "ecr_repositories" {
  description = "Service -> ECR repository URL (`make push`; read from the ecr root's repositories by name)."
  value       = { for k, r in data.aws_ecr_repository.service : k => r.repository_url }
}

output "table_prefix" {
  value = local.table_prefix
}

output "table_names" {
  value = module.dynamodb.table_names
}

output "kms_key_arn" {
  description = "TOWER_KMS_KEY_ID (msisdn_enc envelope key)."
  value       = module.kms.key_arn
}

output "kms_hmac_key_arn" {
  description = "TOWER_KMS_HMAC_KEY_ID (line_id HMAC; also signs audit trim markers)."
  value       = module.kms.hmac_key_arn
}

output "sns_topic_arns" {
  value = module.sns.topic_arns
}

output "lambda_function_names" {
  value = module.lambdas.function_names
}

output "dashboard_name" {
  value = module.observability.dashboard_name
}

output "sms_sandbox_commands" {
  description = "Register each demo phone in the SNS SMS sandbox (needs the OTP the phone receives)."
  sensitive   = true
  value = flatten([
    for n in var.sms_sandbox_numbers : [
      "aws sns create-sms-sandbox-phone-number --region ${var.region} --phone-number ${n}",
      "aws sns verify-sms-sandbox-phone-number --region ${var.region} --phone-number ${n} --one-time-password <OTP>",
    ]
  ])
}

# --- read by the EKS root (deploy/terraform/eks) through terraform_remote_state (prompt 14) -----------------------

output "vpc_id" {
  value = module.network.vpc_id
}

output "vpc_cidr" {
  description = "The VPC CIDR (EKS NetworkPolicies admit the ALB from it)."
  value       = var.vpc_cidr
}

output "public_subnet_ids" {
  value = module.network.public_subnet_ids
}

output "private_subnet_ids" {
  value = module.network.private_subnet_ids
}

output "table_arns" {
  description = "Table name -> ARN (the EKS IRSA policies scope DynamoDB to these and their /index/*)."
  value       = module.dynamodb.table_arns
}

output "enable_nat_gateway" {
  description = "Whether the private subnets have egress (the EKS root may place nodes there only if so)."
  value       = var.enable_nat_gateway
}
