# CloudWatch for the AWS column (10 §2): one dashboard (latency SLI p95 per tool, alert counts, audit reconcile
# misses), metric filters that turn Alerts' log lines into counts, and a guard: a metric filter that counts EMF
# records carrying a line_id, with an alarm on any. Per-line labels on metrics are forbidden (10 §2); the
# dashboard and every metric filter here are checked by tests/aws/test_terraform_static.py for the same rule.

locals {
  # name -> (log group key, pattern). No metric filter below declares dimensions.
  alert_filters = {
    AlertsSent       = "\"via sns\""
    AlertsSendFailed = "\"FAILED (sns)\""
    CarrierMisses    = "\"carrier miss\""
    HooksDropped     = "\"hook dropped\""
  }
  guarded_log_groups = {
    runtime = var.runtime_log_group
    alerts  = var.alerts_log_group
    binding = var.binding_log_group
  }
}

resource "aws_cloudwatch_log_metric_filter" "alerts" {
  for_each       = local.alert_filters
  name           = "${var.name}-${each.key}"
  log_group_name = var.alerts_log_group
  pattern        = each.value

  metric_transformation {
    name          = each.key
    namespace     = var.namespace
    value         = "1"
    default_value = "0"
  }
}

# Guard: an embedded-metric-format record whose text mentions line_id is a metric with a per-line label.
resource "aws_cloudwatch_log_metric_filter" "line_id_on_metric" {
  for_each       = local.guarded_log_groups
  name           = "${var.name}-line-id-on-metric-${each.key}"
  log_group_name = each.value
  pattern        = "\"CloudWatchMetrics\" \"line_id\""

  metric_transformation {
    name          = "LineIdOnMetric"
    namespace     = var.namespace
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "line_id_on_metric" {
  alarm_name          = "${var.name}-line-id-on-metric"
  alarm_description   = "A metric was emitted with a line_id label (forbidden, 10 §2)."
  namespace           = var.namespace
  metric_name         = "LineIdOnMetric"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "reconcile_misses" {
  alarm_name          = "${var.name}-audit-reconcile-misses"
  alarm_description   = "A traced tool call had no audit row (07 §1)."
  namespace           = var.namespace
  metric_name         = "AuditReconcileMisses"
  dimensions          = { component = "audit" }
  statistic           = "Maximum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "reconcile_errors" {
  alarm_name          = "${var.name}-reconcile-errors"
  alarm_description   = "The nightly reconciliation Lambda failed (a miss raises ReconciliationFailed)."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = var.reconcile_function }
  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_dashboard" "this" {
  dashboard_name = "${var.name}-tower"
  dashboard_body = templatefile("${path.module}/dashboard.json.tftpl", {
    name               = var.name
    region             = var.region
    namespace          = var.namespace
    runtime_arn        = var.runtime_arn
    alerts_function    = var.alerts_function
    reconcile_function = var.reconcile_function
    latency_budget_ms  = var.latency_budget_ms
  })
}
