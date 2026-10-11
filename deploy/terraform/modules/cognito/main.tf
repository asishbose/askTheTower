# Cognito for the web chat page and Tower (prompt 20; 09 §6, 01 §4, bind-and-alert-flows D-A).
#
# One user pool, admin-create-only (users come from scripts/cognito_user.py, `make cognito-users`), a Hosted UI
# domain, and one app client `web-chat`: authorization code + PKCE, no secret, scope openid. Tower and both
# Runtime authorizers trust this pool: user_id = the access token's `sub`, client checked via `client_id`
# (Cognito access tokens carry no `aud`). The Alexa account-linking client is added by hand (registration.md §2).

locals {
  domain_prefix = var.domain_prefix != "" ? var.domain_prefix : "${var.name}-${random_id.domain.hex}"
  issuer        = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.this.id}"
  oauth         = length(var.callback_urls) > 0
}

resource "random_id" "domain" {
  # Hosted UI prefixes are global across AWS accounts; 6 hex characters keep two deployments apart.
  byte_length = 3
}

resource "aws_cognito_user_pool" "this" {
  name                = "${var.name}-users"
  deletion_protection = "INACTIVE" # `make down` removes it with the rest

  admin_create_user_config {
    allow_admin_create_user_only = true # no self sign-up: the demo has two people
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = false
    temporary_password_validity_days = 1
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "admin_only" # no e-mail or phone attribute is collected
      priority = 1
    }
  }

  mfa_configuration = "OFF"
}

resource "aws_cognito_user_pool_domain" "this" {
  domain       = local.domain_prefix
  user_pool_id = aws_cognito_user_pool.this.id
}

resource "aws_cognito_user_pool_client" "web_chat" {
  name         = "web-chat"
  user_pool_id = aws_cognito_user_pool.this.id

  generate_secret                      = false # a public client in the browser: PKCE instead of a secret
  allowed_oauth_flows_user_pool_client = local.oauth
  allowed_oauth_flows                  = local.oauth ? ["code"] : null
  allowed_oauth_scopes                 = local.oauth ? ["openid"] : null
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = local.oauth ? var.callback_urls : null
  logout_urls                          = local.oauth ? var.callback_urls : null

  explicit_auth_flows           = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true

  # Default units: hours for the access and id tokens, days for the refresh token. One hour, then sign in again.
  access_token_validity  = 1
  id_token_validity      = 1
  refresh_token_validity = 1
}
