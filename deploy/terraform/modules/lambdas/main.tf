# The three Lambdas of 10 §2 (arm64, Python 3.12, container images from ECR) and the public edge:
#
#   binding-page  binding_page.app.handler          $default route (/bind/*, /me/*, /grants/*, /healthz)
#   alerts        alerts.handler.lambda_handler     /hooks/* (CAMARA sinks), /internal/* (Tower's watch API),
#                                                   EventBridge Scheduler polls, SNS two-way SMS replies
#   reconcile     tower_audit.aws.reconcile_handler nightly trim + reconciliation (10 §3 cron 03:00)
#
# Images are the same ones compose runs; Lambda enters them through the runtime interface client
# (`python -m awslambdaric <handler>`), so no second Dockerfile exists.
#
# Edge: HTTP API (+ CloudFront + WAF when enable_cloudfront_waf). WAF cannot attach to an HTTP API, hence
# CloudFront (10 §1 already lists it for the binding page). The WAF rate rule is scoped to /hooks/*; API Gateway
# route throttling on /hooks/* applies either way. The carrier's /oauth2/authorize is passed through to the mock's
# internal ALB over the VPC link: the phone must reach it over mobile data (rule 7), Gateway must not.

locals {
  functions = {
    "binding-page" = { handler = "binding_page.app.handler", timeout = 15, memory = 512 }
    "alerts"       = { handler = "alerts.handler.lambda_handler", timeout = 60, memory = 512 }
    "reconcile"    = { handler = "tower_audit.aws.reconcile_handler", timeout = 300, memory = 512 }
  }
  index_arns      = [for a in var.table_arns : "${a}/index/*"]
  public_base_url = var.enable_cloudfront_waf ? "https://${aws_cloudfront_distribution.this[0].domain_name}" : trimsuffix(aws_apigatewayv2_api.this.api_endpoint, "/")

  environments = {
    "binding-page" = merge(
      var.common_environment,
      var.binding_uses_gateway ? var.gateway_environment : {},
      var.binding_environment,
      {
        BASE_URL          = local.public_base_url
        BIND_REDIRECT_URI = "${local.public_base_url}/bind/callback"
      },
    )
    "alerts" = merge(
      var.common_environment,
      var.gateway_environment,
      var.alerts_environment,
      {
        HOOKS_BASE_URL = local.public_base_url
        SNS_TOPIC_ARN  = lookup(var.sns_topic_arns, "replies", "")
      },
    )
    "reconcile" = merge(
      { for k, v in var.common_environment : k => v if !startswith(k, "CARRIER_") },
      var.reconcile_environment,
    )
  }
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "fn" {
  for_each           = local.functions
  name               = "${var.name}-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy_attachment" "basic" {
  for_each   = local.functions
  role       = aws_iam_role.fn[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "vpc" {
  count      = var.binding_vpc == null ? 0 : 1
  role       = aws_iam_role.fn["binding-page"].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

data "aws_iam_policy_document" "fn" {
  for_each = local.functions

  statement {
    sid = "ConsentStore"
    actions = each.key == "reconcile" ? [
      "dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan", "dynamodb:PutItem", "dynamodb:UpdateItem",
      "dynamodb:DeleteItem", "dynamodb:TransactWriteItems", "dynamodb:ConditionCheckItem",
      ] : concat([
        "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Query",
        "dynamodb:ConditionCheckItem", "dynamodb:TransactWriteItems", "dynamodb:BatchGetItem",
      ],
      # alerts' tick lists pending escalations with a filtered Scan of AlertsState (alerts.state)
    each.key == "alerts" ? ["dynamodb:Scan"] : [])
    resources = concat(var.table_arns, local.index_arns)
  }

  statement {
    sid       = "Keys"
    actions   = each.key == "reconcile" ? ["kms:GenerateMac", "kms:VerifyMac", "kms:Decrypt", "kms:DescribeKey"] : ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "kms:GenerateMac", "kms:VerifyMac", "kms:DescribeKey"]
    resources = var.kms_key_arns
  }

  dynamic "statement" {
    for_each = each.key == "alerts" || (each.key == "binding-page" && var.binding_uses_gateway) ? [1] : []
    content {
      sid       = "CarrierGateway"
      actions   = ["bedrock-agentcore:InvokeGateway"]
      resources = [var.gateway_arn]
    }
  }

  dynamic "statement" {
    for_each = each.key == "alerts" ? [1] : []
    content {
      sid       = "Sms"
      actions   = ["sns:Publish"]
      resources = ["*"] # publishing to a phone number has no resource ARN
    }
  }

  dynamic "statement" {
    for_each = each.key == "reconcile" ? [1] : []
    content {
      sid       = "Traces"
      actions   = ["logs:StartQuery", "logs:GetQueryResults", "logs:StopQuery", "logs:DescribeLogGroups"]
      resources = ["*"] # StartQuery takes log-group names; GetQueryResults has no resource
    }
  }

  statement {
    sid       = "Metrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["AskTheTower"]
    }
  }
}

resource "aws_iam_role_policy" "fn" {
  for_each = local.functions
  name     = "app"
  role     = aws_iam_role.fn[each.key].id
  policy   = data.aws_iam_policy_document.fn[each.key].json
}

resource "aws_cloudwatch_log_group" "fn" {
  for_each          = local.functions
  name              = "/aws/lambda/${var.name}-${each.key}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "fn" {
  for_each = local.functions

  function_name = "${var.name}-${each.key}"
  role          = aws_iam_role.fn[each.key].arn
  package_type  = "Image"
  image_uri     = var.images[each.key]
  architectures = ["arm64"]
  timeout       = each.value.timeout
  memory_size   = each.value.memory
  kms_key_arn   = var.kms_key_arn # environment variables at rest

  image_config {
    entry_point       = ["/venv/bin/python", "-m", "awslambdaric"]
    command           = [each.value.handler]
    working_directory = "/app"
  }

  environment {
    variables = local.environments[each.key]
  }

  dynamic "vpc_config" {
    for_each = each.key == "binding-page" && var.binding_vpc != null ? [var.binding_vpc] : []
    content {
      subnet_ids         = vpc_config.value.subnet_ids
      security_group_ids = vpc_config.value.security_group_ids
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.fn[each.key].name
  }

  depends_on = [aws_iam_role_policy.fn, aws_iam_role_policy_attachment.basic]
}

# --- HTTP API ----------------------------------------------------------------------------------------------------

resource "aws_apigatewayv2_api" "this" {
  name          = "${var.name}-public"
  protocol_type = "HTTP"
  description   = "Binding page, CAMARA webhook sinks, Tower -> Alerts internal API"
}

resource "aws_apigatewayv2_integration" "fn" {
  for_each               = toset(["binding-page", "alerts"])
  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.fn[each.key].invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
}

resource "aws_apigatewayv2_route" "default" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.fn["binding-page"].id}"
}

resource "aws_apigatewayv2_route" "alerts" {
  for_each  = toset(["ANY /hooks/{proxy+}", "ANY /internal/{proxy+}"])
  api_id    = aws_apigatewayv2_api.this.id
  route_key = each.key
  target    = "integrations/${aws_apigatewayv2_integration.fn["alerts"].id}"
}

resource "aws_apigatewayv2_integration" "carrier_authorize" {
  count                  = var.carrier_authorize_passthrough == null ? 0 : 1
  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "HTTP_PROXY"
  integration_method     = "GET"
  integration_uri        = var.carrier_authorize_passthrough.listener_arn
  connection_type        = "VPC_LINK"
  connection_id          = var.carrier_authorize_passthrough.vpc_link_id
  payload_format_version = "1.0"

  tls_config {
    server_name_to_verify = var.carrier_authorize_passthrough.server_name
  }

  request_parameters = {
    "overwrite:path"        = var.carrier_authorize_passthrough.upstream_path
    "overwrite:header.host" = var.carrier_authorize_passthrough.server_name
  }
}

resource "aws_apigatewayv2_route" "carrier_authorize" {
  count     = var.carrier_authorize_passthrough == null ? 0 : 1
  api_id    = aws_apigatewayv2_api.this.id
  route_key = "GET ${var.carrier_authorize_passthrough.route_path_prefix}"
  target    = "integrations/${aws_apigatewayv2_integration.carrier_authorize[0].id}"
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/apigateway/${var.name}-public"
  retention_in_days = var.log_retention_days
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 100
    throttling_rate_limit  = 50
  }

  route_settings {
    route_key              = "ANY /hooks/{proxy+}"
    throttling_burst_limit = 50
    throttling_rate_limit  = 20
  }

  # Access log without the query string or headers: no token, no code, no number ends up in logs.
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api.arn
    format = jsonencode({
      requestId = "$context.requestId"
      routeKey  = "$context.routeKey"
      status    = "$context.status"
      latencyMs = "$context.responseLatency"
    })
  }

  depends_on = [aws_apigatewayv2_route.alerts]
}

resource "aws_lambda_permission" "api" {
  for_each      = toset(["binding-page", "alerts"])
  statement_id  = "AllowHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn[each.key].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.this.execution_arn}/*/*"
}

# --- CloudFront + WAF (rate rule on /hooks/*) -----------------------------------------------------------------

data "aws_cloudfront_cache_policy" "disabled" {
  count = var.enable_cloudfront_waf ? 1 : 0
  name  = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "all_viewer" {
  count = var.enable_cloudfront_waf ? 1 : 0
  name  = "Managed-AllViewerExceptHostHeader"
}

resource "aws_wafv2_web_acl" "edge" {
  count    = var.enable_cloudfront_waf ? 1 : 0
  provider = aws.us_east_1

  name        = "${var.name}-edge"
  description = "Rate limit on the CAMARA webhook sinks"
  scope       = "CLOUDFRONT"

  default_action {
    allow {}
  }

  rule {
    name     = "hooks-rate"
    priority = 1

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = var.hooks_rate_limit
        aggregate_key_type = "IP"

        scope_down_statement {
          byte_match_statement {
            search_string         = "/hooks/"
            positional_constraint = "STARTS_WITH"
            field_to_match {
              uri_path {}
            }
            text_transformation {
              priority = 0
              type     = "NONE"
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "HooksRate"
      sampled_requests_enabled   = false
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "Edge"
    sampled_requests_enabled   = false
  }
}

resource "aws_cloudfront_distribution" "this" {
  count = var.enable_cloudfront_waf ? 1 : 0

  enabled         = true
  comment         = "${var.name}: binding page and webhook sinks"
  price_class     = "PriceClass_100"
  web_acl_id      = aws_wafv2_web_acl.edge[0].arn
  is_ipv6_enabled = true

  origin {
    origin_id   = "http-api"
    domain_name = replace(aws_apigatewayv2_api.this.api_endpoint, "https://", "")

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id         = "http-api"
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.disabled[0].id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer[0].id
    compress                 = true
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}
