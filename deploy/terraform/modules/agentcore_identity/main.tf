# AgentCore Identity: the outbound carrier OAuth clients (05 §3, 10 §4). Identity holds the client secrets in its
# own token vault; nothing carrier-side goes to our Secrets Manager or to any service's environment.
#
# - `service`: client credentials for SIM Swap, Call Forwarding, Reachability (+ their subscriptions).
# - `binding`: authorization code for Number Verification (the one-tap bind over mobile data, rule 7).
#
# Both are "CustomOauth2" providers with explicit authorization-server metadata, so the same module serves the mock
# (issuer = the mock's internal hostname, token endpoint reached through a managed VPC resource) and a sandbox.

locals {
  providers = {
    service = var.service_client
    binding = var.binding_client
  }
}

resource "aws_bedrockagentcore_oauth2_credential_provider" "this" {
  for_each = local.providers

  name                       = "${var.name}-carrier-${each.key}"
  credential_provider_vendor = "CustomOauth2"

  oauth2_provider_config {
    custom_oauth2_provider_config {
      client_id                    = each.value.id
      client_secret                = each.value.secret
      client_authentication_method = "CLIENT_SECRET_BASIC"

      oauth_discovery {
        authorization_server_metadata {
          issuer                      = var.issuer
          authorization_endpoint      = var.authorize_url
          token_endpoint              = var.token_url
          response_types              = ["code"]
          token_endpoint_auth_methods = ["client_secret_basic", "client_secret_post"]
        }
      }

      dynamic "private_endpoint" {
        for_each = var.private_endpoint == null ? [] : [var.private_endpoint]
        content {
          managed_vpc_resource {
            vpc_identifier           = private_endpoint.value.vpc_id
            subnet_ids               = private_endpoint.value.subnet_ids
            security_group_ids       = private_endpoint.value.security_group_ids
            endpoint_ip_address_type = "IPV4"
            routing_domain           = private_endpoint.value.routing_domain
          }
        }
      }
    }
  }
}

# The binding page's workload identity: the return URL Identity may send the user back to after the carrier's
# consent page (the auth-code leg of Number Verification).
resource "aws_bedrockagentcore_workload_identity" "binding" {
  name                                = "${var.name}-binding-page"
  allowed_resource_oauth2_return_urls = var.return_urls
}
