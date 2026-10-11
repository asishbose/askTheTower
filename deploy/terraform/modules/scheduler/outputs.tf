output "schedule_names" {
  value = { for k, s in aws_scheduler_schedule.this : k => s.name }
}

output "schedule_expressions" {
  value = { for k, s in aws_scheduler_schedule.this : k => s.schedule_expression }
}
