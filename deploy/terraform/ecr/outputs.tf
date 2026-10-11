# `make ecr-outputs` writes these to artifacts/tf-outputs-ecr.json; scripts/push_images.py falls back to that file
# when artifacts/tf-outputs.json (the main root) does not exist yet, so `make push` works right after `make ecr-up`.

output "ecr_repositories" {
  description = "Service -> ECR repository URL (`make push`)."
  value       = { for k, r in aws_ecr_repository.service : k => r.repository_url }
}

output "registry" {
  description = "The account's registry host (<account>.dkr.ecr.<region>.amazonaws.com), for docker login."
  value       = split("/", aws_ecr_repository.service["tower-mcp"].repository_url)[0]
}

output "region" {
  description = "Region of the repositories (push_images.py logs in to it)."
  value       = var.region
}
