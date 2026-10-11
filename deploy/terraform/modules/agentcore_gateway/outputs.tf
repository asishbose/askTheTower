output "gateway_url" {
  value = aws_bedrockagentcore_gateway.this.gateway_url
}

output "gateway_id" {
  value = aws_bedrockagentcore_gateway.this.gateway_id
}

output "gateway_arn" {
  value = aws_bedrockagentcore_gateway.this.gateway_arn
}

output "target_ids" {
  value = { for k, t in aws_bedrockagentcore_gateway_target.camara : k => t.target_id }
}

output "apis" {
  description = "API names registered as targets (one per specs/camara/*.yaml)."
  value       = sort(keys(local.payloads))
}
