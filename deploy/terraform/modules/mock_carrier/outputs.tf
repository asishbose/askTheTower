output "base_url" {
  description = "The mock's apiRoot as Gateway and Identity see it."
  value       = local.base_url
}

output "listener_arn" {
  value = aws_lb_listener.this.arn
}

output "alb_dns_name" {
  value = aws_lb.this.dns_name
}

output "cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "service_name" {
  value = aws_ecs_service.this.name
}

output "container_name" {
  value = local.container_name
}

output "registry_secret_arn" {
  value = aws_secretsmanager_secret.registry.arn
}
