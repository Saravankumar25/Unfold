# Minimum viable operations (decision 4.11): log groups with short retention, four alarms, two metric filters, and
# two budgets. No dashboards, no X-Ray, no Container Insights: they cost money and nothing here needs them.

# --- logs ---------------------------------------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "this" {
  for_each = toset(var.log_group_names)

  name              = each.value
  retention_in_days = var.log_retention_days
}

# --- notifications ------------------------------------------------------------------------------------------------

resource "aws_sns_topic" "alarms" {
  name = "${var.name}-alarms"
}

resource "aws_sns_topic_subscription" "email" {
  for_each = toset(var.alert_emails)

  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = each.value
}

# --- alarms (4) ---------------------------------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  alarm_name          = "${var.name}-jobs-dlq-not-empty"
  alarm_description   = "A job message exhausted its redrive count; the sweeper marks the job failed, look at why."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = var.dlq_name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each = toset(var.alarmed_function_names)

  alarm_name          = "${each.value}-errors"
  alarm_description   = "Lambda ${each.value} reported an error."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = each.value }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

# The sweeper emits an EMF heartbeat every run (15 min). Three silent periods in a row, or no data at all, alarms:
# a dead scheduler would otherwise leave stuck jobs and expired data unnoticed.
resource "aws_cloudwatch_metric_alarm" "sweeper_heartbeat" {
  alarm_name          = "${var.name}-sweeper-heartbeat-missing"
  alarm_description   = "No sweeper heartbeat in 45 minutes."
  namespace           = var.heartbeat_namespace
  metric_name         = "sweeper_heartbeat"
  statistic           = "Sum"
  period              = 900
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

# --- metric filters (2) over structlog JSON lines -----------------------------------------------------------------

resource "aws_cloudwatch_log_metric_filter" "key_disabled" {
  name           = "${var.name}-llm-key-disabled"
  log_group_name = var.worker_log_group_name
  pattern        = "{ $.event = \"llm.key_disabled\" }"

  metric_transformation {
    namespace = var.heartbeat_namespace
    name      = "llm_key_disabled"
    value     = "1"
  }

  depends_on = [aws_cloudwatch_log_group.this]
}

resource "aws_cloudwatch_log_metric_filter" "job_failed" {
  name           = "${var.name}-job-failed"
  log_group_name = var.worker_log_group_name
  pattern        = "{ $.event = \"job.failed\" }"

  metric_transformation {
    namespace = var.heartbeat_namespace
    name      = "job_failed"
    value     = "1"
  }

  depends_on = [aws_cloudwatch_log_group.this]
}

# --- budgets (2): alerts, not caps (the hard stops are the application quotas) ----------------------------------

resource "aws_budgets_budget" "monthly" {
  name         = "${var.name}-monthly"
  budget_type  = "COST"
  limit_amount = var.monthly_budget_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = [50, 80, 100]

    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = var.alert_emails
    }
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = var.alert_emails
  }
}

# A runaway job or an abuse burst shows up in a day, not at the month boundary.
resource "aws_budgets_budget" "daily" {
  name         = "${var.name}-daily"
  budget_type  = "COST"
  limit_amount = var.daily_budget_usd
  limit_unit   = "USD"
  time_unit    = "DAILY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.alert_emails
  }
}
