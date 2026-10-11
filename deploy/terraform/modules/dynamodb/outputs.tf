output "table_names" {
  value = { for k, t in aws_dynamodb_table.this : k => t.name }
}

output "table_arns" {
  description = "Table ARNs; IAM policies add \"<arn>/index/*\" for the GSIs."
  value       = { for k, t in aws_dynamodb_table.this : k => t.arn }
}
