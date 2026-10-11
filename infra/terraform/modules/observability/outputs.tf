output "alarm_topic_arn" {
  value = aws_sns_topic.alarms.arn
}

output "log_group_names" {
  value = [for g in aws_cloudwatch_log_group.this : g.name]
}
