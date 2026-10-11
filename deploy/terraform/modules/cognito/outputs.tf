output "pool_id" {
  value = aws_cognito_user_pool.this.id
}

output "pool_arn" {
  value = aws_cognito_user_pool.this.arn
}

output "client_id" {
  description = "The `web-chat` app client (PKCE, no secret)."
  value       = aws_cognito_user_pool_client.web_chat.id
}

output "issuer" {
  description = "TOWER_JWT_ISSUER."
  value       = local.issuer
}

output "jwks_url" {
  description = "TOWER_JWKS_URL."
  value       = "${local.issuer}/.well-known/jwks.json"
}

output "discovery_url" {
  description = "Runtime custom_jwt_authorizer discovery URL."
  value       = "${local.issuer}/.well-known/openid-configuration"
}

output "hosted_ui_url" {
  description = "The page's COGNITO_DOMAIN: Hosted UI base (/oauth2/authorize, /oauth2/token, /logout)."
  value       = "https://${aws_cognito_user_pool_domain.this.domain}.auth.${var.region}.amazoncognito.com"
}
