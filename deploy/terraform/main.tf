# Ask the Tower — the AWS column of docs/architecture/components/10 §1:
#
#   Tower MCP server  -> Bedrock AgentCore Runtime                       (modules/agentcore_runtime)
#   Carrier gateway   -> AgentCore Gateway + Identity -> mock on Fargate (modules/agentcore_gateway, _identity,
#                                                                         mock_carrier, network)
#   Binding page      -> Lambda + API Gateway (+ CloudFront/WAF)          (modules/lambdas)
#   Alerts            -> Lambda + EventBridge Scheduler + SNS             (modules/lambdas, scheduler, sns)
#   Store             -> DynamoDB on-demand, 7 tables                     (modules/dynamodb)
#   Keys              -> KMS                                              (modules/kms)
#   Dashboards        -> CloudWatch                                       (modules/observability)
#   Web chat (09 §6)  -> Cognito pool + agent runtime (HTTP) + S3/CloudFront + Lambda URL
#                                                                        (modules/cognito, agentcore_runtime, web_chat)
#
# The EKS portability target (prompt 14) is a separate root under deploy/terraform/eks that reads this stack's
# outputs (tables, KMS keys, SNS topic) — it is not a module of this root, so `make down` here never touches it.

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

locals {
  name         = "${var.name_prefix}-${var.environment}"
  table_prefix = "${local.name}-"
  account_id   = data.aws_caller_identity.current.account_id

  services = toset(["tower-mcp", "mock-carrier", "binding-page", "alerts", "ref-client"])
  images   = { for s in local.services : s => "${data.aws_ecr_repository.service[s].repository_url}:${var.image_tag}" }

  mock = var.carrier_backend == "mock"

  public_base_url = module.lambdas.public_base_url
  bind_callback   = "${local.public_base_url}/bind/callback"

  mock_base_url = local.mock ? module.mock_carrier[0].base_url : ""

  # One config block (05 §4): only URLs and credentials differ between mock and sandbox.
  carrier = local.mock ? {
    base_url      = local.mock_base_url
    issuer        = local.mock_base_url
    token_url     = "${local.mock_base_url}/oauth2/token"
    authorize_url = "${local.public_base_url}/carrier/oauth2/authorize" # the phone must reach it: public route
    } : {
    base_url      = var.sandbox_base_url
    issuer        = var.sandbox_issuer
    token_url     = var.sandbox_token_url
    authorize_url = var.sandbox_authorize_url
  }

  # Carrier-side credentials. With the mock, the mock *issues* them (its own registry, in Secrets Manager) and
  # AgentCore Identity *holds* the consuming side. With a sandbox, only Identity holds them.
  service_client = local.mock ? {
    id     = "tower"
    secret = random_password.carrier_client["tower"].result
    } : {
    id     = var.sandbox_client_id
    secret = var.sandbox_client_secret
  }
  binding_client = local.mock ? {
    id     = "binding-page"
    secret = random_password.carrier_client["binding-page"].result
    } : {
    id     = var.sandbox_binding_client_id
    secret = var.sandbox_binding_client_secret
  }

  # Gateway and Identity reach the mock's internal ALB through a managed VPC resource; a sandbox is public.
  carrier_private_endpoint = local.mock ? {
    vpc_id             = module.network.vpc_id
    subnet_ids         = module.network.private_subnet_ids
    security_group_ids = [module.network.agentcore_egress_security_group_id]
    routing_domain     = var.mock_domain_name
  } : null

  table_arns = values(module.dynamodb.table_arns)

  # Inbound identity (01 §4, D-A): the Terraform Cognito pool unless a hand-made issuer is given (overrides).
  # Tower and the agent runtime accept the web-chat client plus tower_jwt_allowed_clients (the Alexa client).
  jwt_discovery_url = var.tower_jwt_discovery_url != "" ? var.tower_jwt_discovery_url : module.cognito.discovery_url
  jwt_jwks_url      = var.tower_jwks_url != "" ? var.tower_jwks_url : module.cognito.jwks_url
  jwt_issuer        = var.tower_jwt_issuer != "" ? var.tower_jwt_issuer : module.cognito.issuer
  jwt_clients       = distinct(concat([module.cognito.client_id], var.tower_jwt_allowed_clients))
}

# --- container registry (read only) -------------------------------------------------------------------------------
# The five repositories live in their own root, deploy/terraform/ecr (`make ecr-up`), so `make down ENV=aws` keeps
# them and their images; only `make down-all` deletes them. This root reads them by name and never manages them.

data "aws_ecr_repository" "service" {
  for_each = local.services
  name     = "${local.name}/${each.key}"
}

# --- generated secrets (never in the repo; state is private and encrypted — backend.tf) ---------------------------

resource "random_password" "carrier_client" {
  # The mock's client registry (10 §4: the mock issues credentials). Ignored when carrier_backend = sandbox.
  for_each = toset(["tower", "alerts", "binding-page"])
  length   = 40
  special  = false
}

resource "random_password" "internal_bearer" {
  # Tower -> Alerts /internal/watch (02 §5); not a carrier credential.
  length  = 48
  special = false
}

resource "random_password" "session_secret" {
  # Binding page session cookie signing key; not a carrier credential.
  length  = 48
  special = false
}

# --- modules ------------------------------------------------------------------------------------------------------

module "kms" {
  source = "./modules/kms"

  name       = local.name
  account_id = local.account_id
  partition  = data.aws_partition.current.partition
  user_role_arns = compact([
    module.agentcore_runtime.role_arn,
    module.lambdas.role_arns["binding-page"],
    module.lambdas.role_arns["alerts"],
    module.lambdas.role_arns["reconcile"],
  ])
  service_principals = ["logs.${var.region}.amazonaws.com", "sns.amazonaws.com", "events.amazonaws.com"]
}

module "dynamodb" {
  source = "./modules/dynamodb"

  tables      = var.dynamodb_tables
  prefix      = local.table_prefix
  kms_key_arn = module.kms.key_arn
}

module "network" {
  source = "./modules/network"

  name               = local.name
  vpc_cidr           = var.vpc_cidr
  az_count           = var.az_count
  enable_nat_gateway = var.enable_nat_gateway
}

module "mock_carrier" {
  source = "./modules/mock_carrier"
  count  = local.mock ? 1 : 0

  name               = local.name
  region             = var.region
  image              = local.images["mock-carrier"]
  vpc_id             = module.network.vpc_id
  vpc_cidr           = var.vpc_cidr
  alb_subnet_ids     = module.network.private_subnet_ids
  task_subnet_ids    = var.enable_nat_gateway ? module.network.private_subnet_ids : module.network.public_subnet_ids
  assign_public_ip   = !var.enable_nat_gateway
  domain_name        = var.mock_domain_name
  route53_zone_id    = var.mock_route53_zone_id
  certificate_arn    = var.mock_certificate_arn
  scenario           = var.mock_scenario
  assume_mobile_data = var.mock_assume_mobile_data
  kms_key_arn        = module.kms.key_arn
  log_retention_days = var.log_retention_days

  client_secrets = { for k, v in random_password.carrier_client : k => v.result }
  # Where the mock may redirect a Number Verification code: the binding page's callback and Identity's callback.
  binding_redirect_prefixes = compact([
    local.public_base_url,
    module.agentcore_identity.binding_callback_url,
  ])
}

module "agentcore_identity" {
  source = "./modules/agentcore_identity"

  name             = local.name
  issuer           = local.carrier.issuer
  token_url        = local.carrier.token_url
  authorize_url    = local.carrier.authorize_url
  service_client   = local.service_client
  binding_client   = local.binding_client
  return_urls      = [local.bind_callback]
  private_endpoint = local.carrier_private_endpoint
}

module "agentcore_gateway" {
  source = "./modules/agentcore_gateway"

  name                 = local.name
  account_id           = local.account_id
  region               = var.region
  specs_dir            = "${path.module}/../../specs/camara"
  carrier_base_url     = local.carrier.base_url
  service_provider_arn = module.agentcore_identity.service_provider_arn
  binding_provider_arn = module.agentcore_identity.binding_provider_arn
  binding_return_url   = local.bind_callback
  target_scopes        = var.carrier_target_scopes
  private_endpoint     = local.carrier_private_endpoint
  kms_key_arn          = module.kms.key_arn
  identity_secret_arns = module.agentcore_identity.client_secret_arns
}

module "agentcore_runtime" {
  source = "./modules/agentcore_runtime"

  name               = local.name
  region             = var.region
  account_id         = local.account_id
  image              = local.images["tower-mcp"]
  ref_client_image   = local.images["ref-client"]
  enable_agent       = var.enable_web_chat
  bedrock_model_id   = var.bedrock_model_id
  binding_base_url   = local.public_base_url
  log_retention_days = var.log_retention_days

  table_arns   = local.table_arns
  kms_key_arns = [module.kms.key_arn, module.kms.hmac_key_arn]
  gateway_arn  = module.agentcore_gateway.gateway_arn

  jwt_discovery_url    = local.jwt_discovery_url
  jwt_allowed_audience = var.tower_jwt_allowed_audience
  jwt_allowed_clients  = local.jwt_clients

  agent_jwt_discovery_url   = local.jwt_discovery_url
  agent_jwt_allowed_clients = local.jwt_clients

  # Cut line (prompts/00): Runtime in the VPC with DirectClient -> the mock's internal ALB.
  vpc = var.tower_carrier_client == "direct" ? {
    subnet_ids         = module.network.private_subnet_ids
    security_group_ids = [module.network.agentcore_egress_security_group_id]
  } : null

  environment = merge(
    {
      TOWER_ENV              = "aws"
      TOWER_TABLE_PREFIX     = local.table_prefix
      TOWER_KMS_KEY_ID       = module.kms.key_arn
      TOWER_KMS_HMAC_KEY_ID  = module.kms.hmac_key_arn
      TOWER_TZ               = var.tower_timezone
      TOWER_JWKS_URL         = local.jwt_jwks_url
      TOWER_JWT_ISSUER       = local.jwt_issuer
      TOWER_JWT_AUDIENCE     = join(",", var.tower_jwt_allowed_audience)
      TOWER_JWT_CLIENT_IDS   = join(",", local.jwt_clients)
      BINDING_BASE_URL       = local.public_base_url
      ALERTS_INTERNAL_URL    = local.public_base_url
      ALERTS_INTERNAL_BEARER = random_password.internal_bearer.result
      CARRIER_BACKEND        = var.carrier_backend
      CARRIER_BASE_URL       = local.carrier.base_url
      CARRIER_CLIENT_ID      = local.service_client.id
      CARRIER_PROFILE        = "request"
      CARRIER_SCOPES         = join(" ", ["sim-swap", "call-forwarding-signal", "device-reachability-status"])
      LOG_LEVEL              = "info"
    },
    var.tower_carrier_client == "gateway" ? {
      CARRIER_CLIENT         = "gateway"
      CARRIER_GATEWAY_URL    = module.agentcore_gateway.gateway_url
      CARRIER_GATEWAY_TOOLS  = "discover"
      CARRIER_GATEWAY_AUTH   = "sigv4"
      CARRIER_GATEWAY_REGION = var.region
      } : {
      # Cut-line only: Identity is not in use on this path, so the client secret is Runtime config (10 §4).
      CARRIER_CLIENT        = "direct"
      CARRIER_SECRET_REF    = "env:CARRIER_CLIENT_SECRET"
      CARRIER_CLIENT_SECRET = local.service_client.secret
    },
  )
}

module "cognito" {
  source = "./modules/cognito"

  name          = local.name
  region        = var.region
  domain_prefix = var.cognito_domain_prefix
  # The Hosted UI sends the browser back to the page; Terraform sets it, so nothing is typed in the console.
  callback_urls = var.enable_web_chat ? [module.web_chat[0].page_url] : []
}

module "web_chat" {
  source = "./modules/web_chat"
  count  = var.enable_web_chat ? 1 : 0

  name               = local.name
  agent_invoke_url   = module.agentcore_runtime.agent_invoke_url
  proxy_source       = "${path.module}/../../services/web-chat/proxy/handler.py"
  log_retention_days = var.log_retention_days
}

module "sns" {
  source = "./modules/sns"

  name                    = local.name
  kms_key_arn             = module.kms.key_arn
  alerts_function_arn     = module.lambdas.function_arns["alerts"]
  alerts_function_name    = module.lambdas.function_names["alerts"]
  sms_monthly_spend_limit = var.sms_monthly_spend_limit
  enable_push_topic       = var.enable_push_topic
}

module "lambdas" {
  source = "./modules/lambdas"
  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1
  }

  name               = local.name
  region             = var.region
  account_id         = local.account_id
  log_retention_days = var.log_retention_days

  images = {
    "binding-page" = local.images["binding-page"]
    "alerts"       = local.images["alerts"]
    "reconcile"    = local.images["alerts"] # same image, different handler (tower_audit ships in it)
  }

  table_arns     = local.table_arns
  kms_key_arns   = [module.kms.key_arn, module.kms.hmac_key_arn]
  kms_key_arn    = module.kms.key_arn
  gateway_arn    = module.agentcore_gateway.gateway_arn
  sns_topic_arns = module.sns.topic_arns
  trace_log_groups = [
    "aws/spans",
    module.agentcore_runtime.log_group_name,
  ]

  enable_cloudfront_waf = var.enable_cloudfront_waf
  hooks_rate_limit      = var.hooks_rate_limit

  # Public passthrough of the carrier's authorize endpoint (the phone opens it over mobile data, rule 7).
  carrier_authorize_passthrough = local.mock ? {
    vpc_link_id       = module.network.vpc_link_id
    listener_arn      = module.mock_carrier[0].listener_arn
    server_name       = var.mock_domain_name
    upstream_path     = "/oauth2/authorize"
    route_path_prefix = "/carrier/oauth2/authorize"
  } : null

  # Binding page in the VPC only when it talks to the mock directly (binding_carrier_client = "direct").
  binding_vpc = var.binding_carrier_client == "direct" && local.mock ? {
    subnet_ids         = module.network.private_subnet_ids
    security_group_ids = [module.network.agentcore_egress_security_group_id]
  } : null

  common_environment = {
    TOWER_ENV             = "aws"
    TOWER_TABLE_PREFIX    = local.table_prefix
    TOWER_KMS_KEY_ID      = module.kms.key_arn
    TOWER_KMS_HMAC_KEY_ID = module.kms.hmac_key_arn
    CARRIER_BACKEND       = var.carrier_backend
    CARRIER_BASE_URL      = local.carrier.base_url
    LOG_LEVEL             = "info"
  }

  gateway_environment = {
    CARRIER_CLIENT         = "gateway"
    CARRIER_GATEWAY_URL    = module.agentcore_gateway.gateway_url
    CARRIER_GATEWAY_TOOLS  = "discover"
    CARRIER_GATEWAY_AUTH   = "sigv4"
    CARRIER_GATEWAY_REGION = var.region
  }

  binding_environment = merge(
    {
      SESSION_SECRET         = random_password.session_secret.result
      CARRIER_CLIENT_ID      = local.binding_client.id
      CARRIER_AUTHORIZE_URL  = local.carrier.authorize_url
      CARRIER_TOKEN_URL      = local.carrier.token_url
      ALERTS_INTERNAL_URL    = local.public_base_url # 04 §9.2: re-subscribe after a watch-settings save
      ALERTS_INTERNAL_BEARER = random_password.internal_bearer.result
      SESSION_TTL_S          = tostring(var.binding_session_ttl_s) # 04 §6, D-H: 24 h on the demo day
    },
    var.binding_carrier_client == "direct" ? {
      CARRIER_CLIENT        = "direct"
      CARRIER_SECRET_REF    = "env:CARRIER_CLIENT_SECRET"
      CARRIER_CLIENT_SECRET = local.binding_client.secret
    } : {},
  )
  binding_uses_gateway = var.binding_carrier_client == "gateway"

  alerts_environment = {
    ALERTS_MODE       = "lambda"
    ALERTS_SENDER     = "sns"
    INTERNAL_BEARER   = random_password.internal_bearer.result
    ALERTS_DEFAULT_TZ = var.alerts_timezone
  }

  reconcile_environment = {
    TOWER_TRACE_LOG_GROUP    = "aws/spans"
    TOWER_METRICS_NAMESPACE  = "AskTheTower"
    TOWER_RECONCILE_WINDOW_H = "24"
  }
}

module "scheduler" {
  source = "./modules/scheduler"

  name                   = local.name
  alerts_function_arn    = module.lambdas.function_arns["alerts"]
  reconcile_function_arn = module.lambdas.function_arns["reconcile"]
  timezone               = var.alerts_timezone
  kms_key_arn            = module.kms.key_arn
}

module "observability" {
  source = "./modules/observability"

  name               = local.name
  region             = var.region
  namespace          = "AskTheTower"
  runtime_arn        = module.agentcore_runtime.runtime_arn
  runtime_log_group  = module.agentcore_runtime.log_group_name
  alerts_log_group   = module.lambdas.log_group_names["alerts"]
  binding_log_group  = module.lambdas.log_group_names["binding-page"]
  reconcile_function = module.lambdas.function_names["reconcile"]
  alerts_function    = module.lambdas.function_names["alerts"]
  latency_budget_ms  = 400
}
