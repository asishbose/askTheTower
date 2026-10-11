output "service_provider_arn" {
  value = aws_bedrockagentcore_oauth2_credential_provider.this["service"].credential_provider_arn
}

output "binding_provider_arn" {
  value = aws_bedrockagentcore_oauth2_credential_provider.this["binding"].credential_provider_arn
}

output "binding_callback_url" {
  description = "Identity's OAuth callback for the auth-code provider (registered with the carrier as a redirect)."
  value       = aws_bedrockagentcore_oauth2_credential_provider.this["binding"].callback_url
}

output "client_secret_arns" {
  description = "The Identity-managed secrets behind the providers (the Gateway role may read them; nothing else)."
  value       = [for p in aws_bedrockagentcore_oauth2_credential_provider.this : p.client_secret_arn]
}

output "binding_workload_identity_arn" {
  value = aws_bedrockagentcore_workload_identity.binding.workload_identity_arn
}
