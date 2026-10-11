# EventBridge Scheduler (10 §3). Every poll invokes the same alerts Lambda with `{"profile": ...}`; the Lambda
# queries Watches by profile (alerts.handler -> runner.poll). Reconciliation runs nightly on its own Lambda.
#
# | Schedule              | Payload                  | 10 §3 row                                   |
# | rate(5 minutes)       | {"profile":"transplant"} | reachability, transplant                    |
# | rate(30 minutes)      | {"profile":"care"}       | reachability, care (08:00-22:00 in-handler) |
# | cron(0 8 * * ? *)     | {"profile":"self"}       | SIM swap + call forwarding                  |
# | cron(0 8 * * ? *)     | {"profile":"care"}       | SIM swap + call forwarding                  |
# | cron(0 3 * * ? *)     | {"scheduled_time": ...}  | audit trim + reconciliation (reconcile fn)  |
# | rate(5 minutes)       | {"action":"tick"}        | escalation steps (06 §3, ESCALATE_NEXT 15m) |
#
# "Local time by line" (10 §3) is approximated by one zone (`timezone`) at demo scale; the handler applies
# quiet hours per line.

locals {
  schedules = {
    "poll-transplant" = { expression = "rate(5 minutes)", target = var.alerts_function_arn, input = { profile = "transplant" } }
    "poll-care"       = { expression = "rate(30 minutes)", target = var.alerts_function_arn, input = { profile = "care" } }
    "daily-self"      = { expression = "cron(0 8 * * ? *)", target = var.alerts_function_arn, input = { profile = "self" } }
    "daily-care"      = { expression = "cron(0 8 * * ? *)", target = var.alerts_function_arn, input = { profile = "care" } }
    "escalation-tick" = { expression = "rate(5 minutes)", target = var.alerts_function_arn, input = { action = "tick" } }
    # `now` for the nightly job comes from the event (packages read no clock): Scheduler's context attribute.
    "reconcile" = { expression = "cron(0 3 * * ? *)", target = var.reconcile_function_arn, input = { scheduled_time = "<aws.scheduler.scheduled-time>" } }
  }
}

resource "aws_scheduler_schedule_group" "this" {
  name = var.name
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "invoke" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = distinct([var.alerts_function_arn, var.reconcile_function_arn])
  }
  statement {
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "invoke" {
  name   = "invoke"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.invoke.json
}

resource "aws_scheduler_schedule" "this" {
  for_each = local.schedules

  name                         = each.key
  group_name                   = aws_scheduler_schedule_group.this.name
  schedule_expression          = each.value.expression
  schedule_expression_timezone = startswith(each.value.expression, "cron(") ? var.timezone : "UTC"
  kms_key_arn                  = var.kms_key_arn
  state                        = var.enabled ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = each.value.target
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode(each.value.input)

    retry_policy {
      maximum_retry_attempts       = 0 # a missed poll is caught by the next one; evaluation is idempotent
      maximum_event_age_in_seconds = 300
    }
  }
}
