output "public_base_url" {
  description = "https://<cloudfront domain> or the HTTP API endpoint."
  value       = local.public_base_url
}

output "api_endpoint" {
  value = aws_apigatewayv2_api.this.api_endpoint
}

output "role_arns" {
  value = { for k, r in aws_iam_role.fn : k => r.arn }
}

output "function_arns" {
  value = { for k, f in aws_lambda_function.fn : k => f.arn }
}

output "function_names" {
  value = { for k, f in aws_lambda_function.fn : k => f.function_name }
}

output "log_group_names" {
  value = { for k, g in aws_cloudwatch_log_group.fn : k => g.name }
}
