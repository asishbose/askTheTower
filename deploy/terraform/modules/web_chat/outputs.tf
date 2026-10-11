output "page_url" {
  description = "The page (CloudFront), with the trailing slash: the Hosted UI callback and REDIRECT_URI."
  value       = "${local.page_origin}/"
}

output "agent_url" {
  description = "The page's AGENT_URL: the Lambda function URL + invocations."
  value       = "${trimsuffix(aws_lambda_function_url.proxy.function_url, "/")}/invocations"
}

output "bucket" {
  value = aws_s3_bucket.page.id
}

output "distribution_id" {
  value = aws_cloudfront_distribution.page.id
}
