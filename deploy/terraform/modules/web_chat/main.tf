# The web chat page (09 §6.3): a private S3 bucket behind CloudFront (origin access control), and a Lambda
# function URL that forwards the page's POST to the agent runtime (services/web-chat/proxy/handler.py). The
# function URL answers CORS for the page's origin only; the handler forwards three headers and logs nothing.
# `make web-chat-sync` uploads the page and its rendered config.js; this module creates no object in the bucket.

locals {
  page_origin = "https://${aws_cloudfront_distribution.page.domain_name}"
  # AWS managed policies (stable ids): CachingOptimized and SecurityHeadersPolicy.
  caching_optimized_policy_id = "658327ea-f89d-4fab-a63d-7e88639e58f6"
  security_headers_policy_id  = "67f7725c-6f97-4210-82d7-5512b31e9d03"
}

# --- page: S3 (private) + CloudFront (OAC) ----------------------------------------------------------------------

resource "aws_s3_bucket" "page" {
  bucket_prefix = "${var.name}-web-chat-"
  force_destroy = true # `make down` empties it: it only holds the page
}

resource "aws_s3_bucket_public_access_block" "page" {
  bucket                  = aws_s3_bucket.page.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "page" {
  bucket = aws_s3_bucket.page.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "page" {
  bucket = aws_s3_bucket.page.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256" # OAC reads SSE-S3 without a key policy; the page is public content anyway
    }
  }
}

resource "aws_cloudfront_origin_access_control" "page" {
  name                              = "${var.name}-web-chat"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "page" {
  enabled             = true
  comment             = "${var.name} web chat page"
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  http_version        = "http2and3"

  origin {
    origin_id                = "page"
    domain_name              = aws_s3_bucket.page.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.page.id
  }

  default_cache_behavior {
    target_origin_id           = "page"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = local.caching_optimized_policy_id
    response_headers_policy_id = local.security_headers_policy_id
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

data "aws_iam_policy_document" "page_bucket" {
  statement {
    sid       = "CloudFrontReadsThePage"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.page.arn}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.page.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "page" {
  bucket     = aws_s3_bucket.page.id
  policy     = data.aws_iam_policy_document.page_bucket.json
  depends_on = [aws_s3_bucket_public_access_block.page]
}

# --- agent proxy: Lambda function URL -> AgentCore Runtime /invocations -------------------------------------------

data "archive_file" "proxy" {
  type        = "zip"
  source_file = var.proxy_source
  output_path = "${path.root}/../../artifacts/web-chat-proxy.zip"
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "proxy" {
  name               = "${var.name}-web-chat-proxy"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "proxy" {
  # Platform logs only (START/END/REPORT): the handler writes none. No other permission: it holds no credential.
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.proxy.arn}:*"]
  }
}

resource "aws_iam_role_policy" "proxy" {
  name   = "logs"
  role   = aws_iam_role.proxy.id
  policy = data.aws_iam_policy_document.proxy.json
}

resource "aws_cloudwatch_log_group" "proxy" {
  name              = "/aws/lambda/${var.name}-web-chat-proxy"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "proxy" {
  function_name    = "${var.name}-web-chat-proxy"
  role             = aws_iam_role.proxy.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "handler.handler"
  filename         = data.archive_file.proxy.output_path
  source_code_hash = data.archive_file.proxy.output_base64sha256
  timeout          = 60
  memory_size      = 128

  environment {
    variables = {
      AGENT_INVOKE_URL = var.agent_invoke_url
    }
  }

  depends_on = [aws_cloudwatch_log_group.proxy, aws_iam_role_policy.proxy]
}

resource "aws_lambda_function_url" "proxy" {
  function_name      = aws_lambda_function.proxy.function_name
  authorization_type = "NONE" # the caller's Cognito token is checked by the Runtime's JWT authorizer, then Tower

  cors {
    allow_origins = [local.page_origin]
    allow_methods = ["POST"]
    allow_headers = ["authorization", "content-type", "x-amzn-bedrock-agentcore-runtime-session-id"]
    max_age       = 300
  }
}

resource "aws_lambda_permission" "proxy_url" {
  statement_id           = "FunctionUrlPublic"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.proxy.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}
