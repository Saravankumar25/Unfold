variable "name" {
  description = "Resource name prefix, e.g. unfold-dev."
  type        = string
}

variable "log_group_names" {
  description = "Every log group of the stack (Lambda /aws/lambda/<fn> and the worker group)."
  type        = list(string)
}

variable "log_retention_days" {
  description = "Retention for every log group (14 by design: logs are for debugging a demo, not for audit)."
  type        = number
  default     = 14

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30], var.log_retention_days)
    error_message = "Use a short retention: 1, 3, 5, 7, 14 or 30 days."
  }
}

variable "worker_log_group_name" {
  description = "Log group the metric filters read (must be in log_group_names)."
  type        = string
}

variable "dlq_name" {
  description = "Dead-letter queue name (CloudWatch dimension)."
  type        = string
}

variable "alarmed_function_names" {
  description = "Lambda functions whose Errors metric is alarmed (dispatcher, sweeper)."
  type        = list(string)
}

variable "heartbeat_namespace" {
  description = "CloudWatch namespace the sweeper heartbeat and the log metric filters write to."
  type        = string
  default     = "Unfold"
}

variable "alert_emails" {
  description = "Recipients of alarms and budget alerts. SNS email subscriptions need a confirmation click."
  type        = list(string)

  validation {
    condition     = length(var.alert_emails) > 0
    error_message = "At least one alert e-mail is required; budgets and alarms without a recipient are not alerts."
  }
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget (alerts at 50/80/100 percent actual and 100 percent forecast)."
  type        = string
  default     = "20"
}

variable "daily_budget_usd" {
  description = "Daily cost budget (alert at 100 percent actual)."
  type        = string
  default     = "5"
}
