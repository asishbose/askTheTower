output "runtime_arn" {
  value = aws_bedrockagentcore_agent_runtime.tower.agent_runtime_arn
}

output "runtime_id" {
  value = aws_bedrockagentcore_agent_runtime.tower.agent_runtime_id
}

output "role_arn" {
  value = aws_iam_role.runtime.arn
}

output "invoke_url" {
  description = "MCP endpoint: the Runtime invocation URL for the DEFAULT endpoint."
  value       = local.tower_invoke
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.tower.name
}

output "agent_runtime_arn" {
  description = "The web chat agent runtime (empty without enable_agent)."
  value       = var.enable_agent ? aws_bedrockagentcore_agent_runtime.ref_client[0].agent_runtime_arn : ""
}

output "agent_invoke_url" {
  description = "The agent runtime's invocation URL (the web chat proxy's AGENT_INVOKE_URL); empty without enable_agent."
  value       = var.enable_agent ? "https://bedrock-agentcore.${var.region}.amazonaws.com/runtimes/${urlencode(aws_bedrockagentcore_agent_runtime.ref_client[0].agent_runtime_arn)}/invocations?qualifier=DEFAULT" : ""
}

output "agent_role_arn" {
  value = var.enable_agent ? aws_iam_role.agent[0].arn : ""
}

output "agent_log_group_name" {
  value = local.agent_log_group
}
