output "dashboard_name" {
  value = aws_cloudwatch_dashboard.this.dashboard_name
}

output "alarm_names" {
  value = [
    aws_cloudwatch_metric_alarm.line_id_on_metric.alarm_name,
    aws_cloudwatch_metric_alarm.reconcile_misses.alarm_name,
    aws_cloudwatch_metric_alarm.reconcile_errors.alarm_name,
  ]
}
