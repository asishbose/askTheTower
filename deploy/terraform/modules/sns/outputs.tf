output "topic_arns" {
  value = merge(
    { replies = aws_sns_topic.replies.arn },
    var.enable_push_topic ? { push = aws_sns_topic.push[0].arn } : {},
  )
}
