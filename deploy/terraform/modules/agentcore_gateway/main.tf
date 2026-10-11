# AgentCore Gateway: the vendored CAMARA OpenAPI files -> MCP tools (05 §3). One target per spec file; Gateway
# generates one tool per operation and makes the HTTPS call to `carrier_base_url` (the mock's internal hostname,
# or a sandbox — the backend swap is this one variable, 05 §4 / testing-and-showcase §2.5).
#
# - Inbound: AWS_IAM. Tower (Runtime role) and the Lambdas sign MCP requests with SigV4
#   (camara_client.aws.SigV4Auth, CARRIER_GATEWAY_AUTH=sigv4).
# - Outbound: AgentCore Identity — client credentials for the service-level APIs, authorization code for
#   Number Verification (05 §3). CIBA is not supported by Gateway; it stays on DirectClient if a sandbox needs it.
# - Tool names: AgentCore names tools `<target>___<operationId>`; the client never hard-codes them. After apply,
#   `make register-gateway` lists the tools and writes gateway-tools.json; at runtime services use
#   CARRIER_GATEWAY_TOOLS=discover (camara_client.gateway.resolve_tool_names over tools/list).
#
# The specs are sent as-is except `servers`, which is rewritten from `{apiRoot}/<api>/<version>` to the concrete
# carrier URL — Gateway calls whatever `servers[0].url` says.

locals {
  spec_files = fileset(var.specs_dir, "*.yaml")
  specs = {
    for f in local.spec_files : trimsuffix(f, ".yaml") => yamldecode(file("${var.specs_dir}/${f}"))
  }
  payloads = {
    for api, spec in local.specs : api => jsonencode(merge(spec, {
      servers = [{ url = replace(spec.servers[0].url, "{apiRoot}", trimsuffix(var.carrier_base_url, "/")) }]
    }))
  }
  auth_code_apis = toset(["number-verification"])
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_iam_role" "gateway" {
  name               = "${var.name}-agentcore-gateway"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "gateway" {
  statement {
    sid = "IdentityTokens"
    actions = [
      "bedrock-agentcore:GetWorkloadAccessToken",
      "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
      "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
      "bedrock-agentcore:GetResourceOauth2Token",
    ]
    resources = [
      "arn:aws:bedrock-agentcore:${var.region}:${var.account_id}:workload-identity-directory/default",
      "arn:aws:bedrock-agentcore:${var.region}:${var.account_id}:workload-identity-directory/default/workload-identity/*",
      "arn:aws:bedrock-agentcore:${var.region}:${var.account_id}:token-vault/default",
      "arn:aws:bedrock-agentcore:${var.region}:${var.account_id}:token-vault/default/*",
    ]
  }

  statement {
    sid       = "IdentityClientSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = length(var.identity_secret_arns) > 0 ? var.identity_secret_arns : ["arn:aws:secretsmanager:${var.region}:${var.account_id}:secret:bedrock-agentcore-identity!*"]
  }

  statement {
    sid       = "Kms"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "gateway" {
  name   = "identity-outbound"
  role   = aws_iam_role.gateway.id
  policy = data.aws_iam_policy_document.gateway.json
}

resource "aws_bedrockagentcore_gateway" "this" {
  name            = "${var.name}-carrier"
  description     = "CAMARA OpenAPI -> MCP tools for Ask the Tower (backend: ${var.carrier_base_url})"
  role_arn        = aws_iam_role.gateway.arn
  authorizer_type = "AWS_IAM"
  protocol_type   = "MCP"

  depends_on = [aws_iam_role_policy.gateway]
}

resource "aws_bedrockagentcore_gateway_target" "camara" {
  for_each = local.payloads

  name               = each.key
  gateway_identifier = aws_bedrockagentcore_gateway.this.gateway_id
  description        = "CAMARA ${each.key} (${local.specs[each.key].info.version})"

  target_configuration {
    mcp {
      open_api_schema {
        inline_payload {
          payload = each.value
        }
      }
    }
  }

  credential_provider_configuration {
    oauth {
      provider_arn       = contains(local.auth_code_apis, each.key) ? var.binding_provider_arn : var.service_provider_arn
      scopes             = lookup(var.target_scopes, each.key, [each.key])
      grant_type         = contains(local.auth_code_apis, each.key) ? "AUTHORIZATION_CODE" : "CLIENT_CREDENTIALS"
      default_return_url = contains(local.auth_code_apis, each.key) ? var.binding_return_url : null
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
