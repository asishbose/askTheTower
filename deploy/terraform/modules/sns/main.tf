# SMS for the Alerts service (06 §3, 10 §2).
#
# - Outbound SMS is `sns.publish(PhoneNumber=...)` from the alerts Lambda (no topic involved), Transactional type.
# - `replies`: the two-way SMS inbound topic. Replies ("OK") from watchers arrive here and are routed to the alerts
#   Lambda (alerts.handler: SNS records -> escalation.handle_reply). Attaching it to an origination number is an
#   AWS End User Messaging step done once by hand (deploy/terraform/README.md).
# - `push`: optional topic for app push (06 §3 "SMS or push").
# - The SMS sandbox: demo phones must be registered and verified by OTP; Terraform cannot do that, so the root
#   output `sms_sandbox_commands` prints the CLI calls.

resource "aws_sns_topic" "replies" {
  name              = "${var.name}-sms-replies"
  kms_master_key_id = var.kms_key_arn
}

resource "aws_sns_topic" "push" {
  count             = var.enable_push_topic ? 1 : 0
  name              = "${var.name}-push"
  kms_master_key_id = var.kms_key_arn
}

resource "aws_sns_topic_subscription" "replies_to_alerts" {
  topic_arn = aws_sns_topic.replies.arn
  protocol  = "lambda"
  endpoint  = var.alerts_function_arn
}

resource "aws_lambda_permission" "replies" {
  statement_id  = "AllowSnsReplies"
  action        = "lambda:InvokeFunction"
  function_name = var.alerts_function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.replies.arn
}

resource "aws_sns_sms_preferences" "this" {
  default_sms_type    = "Transactional"
  monthly_spend_limit = var.sms_monthly_spend_limit
}
