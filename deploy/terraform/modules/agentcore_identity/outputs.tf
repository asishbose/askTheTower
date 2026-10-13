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
  # aws provider >= 6.6x exposes client_secret_arn as a nested block (list of {secret_arn}), not a string
  value       = flatten([for p in aws_bedrockagentcore_oauth2_credential_provider.this : [for s in p.client_secret_arn : s.secret_arn]])
}

output "binding_workload_identity_arn" {
  value = aws_bedrockagentcore_workload_identity.binding.workload_identity_arn
}
