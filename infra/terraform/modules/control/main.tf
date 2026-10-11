# Orchestration without Step Functions (decision 4.3): an SQS-triggered dispatcher runs one Fargate task per job
# (RunTask, clientToken = job#attempt), an EventBridge rule reacts to STOPPED tasks, and a scheduled sweeper heals
# whatever is left (outbox gap, stale heartbeats, expired data, counters). All three are the same worker image
# started with a different handler, so there is one build and one place for the logic.

locals {
  functions = {
    dispatcher   = { handler = var.handlers.dispatcher, timeout = var.dispatcher_timeout_seconds, memory = var.memory_mb }
    task_stopped = { handler = var.handlers.task_stopped, timeout = 60, memory = var.memory_mb }
    sweeper      = { handler = var.handlers.sweeper, timeout = var.sweeper_timeout_seconds, memory = var.memory_mb }
  }
}

resource "aws_lambda_function" "this" {
  for_each = local.functions

  function_name = "${var.name}-${each.key}"
  role          = var.role_arn
  package_type  = "Image"
  image_uri     = var.image_uri
  architectures = [var.architecture]
  memory_size   = each.value.memory
  timeout       = each.value.timeout

  # The worker image's entry point runs a job; as a Lambda it must start the runtime interface client instead.
  image_config {
    entry_point = var.entry_point
    command     = [each.value.handler]
  }

  logging_config {
    log_format = "Text"
    log_group  = "/aws/lambda/${var.name}-${each.key}"
  }

  environment {
    variables = var.environment
  }
}

# --- jobs queue -> dispatcher -----------------------------------------------------------------------------------

resource "aws_lambda_event_source_mapping" "jobs" {
  event_source_arn = var.queue_arn
  function_name    = aws_lambda_function.this["dispatcher"].arn
  batch_size       = 1
  enabled          = true

  scaling_config {
    maximum_concurrency = var.dispatcher_max_concurrency
  }
}

# --- ECS task STOPPED -> task_stopped -----------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "task_stopped" {
  name        = "${var.name}-task-stopped"
  description = "Analysis/export tasks of this cluster that stopped (exit code and stop reason drive the retry)."

  event_pattern = jsonencode({
    source        = ["aws.ecs"]
    "detail-type" = ["ECS Task State Change"]
    detail = {
      clusterArn = [var.cluster_arn]
      lastStatus = ["STOPPED"]
    }
  })
}

resource "aws_cloudwatch_event_target" "task_stopped" {
  rule = aws_cloudwatch_event_rule.task_stopped.name
  arn  = aws_lambda_function.this["task_stopped"].arn
}

resource "aws_lambda_permission" "task_stopped" {
  statement_id  = "AllowEventBridgeTaskStopped"
  function_name = aws_lambda_function.this["task_stopped"].function_name
  action        = "lambda:InvokeFunction"
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.task_stopped.arn
}

# --- schedule -> sweeper ---------------------------------------------------------------------------------------

data "aws_iam_policy_document" "scheduler_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name                 = "${var.name}-scheduler"
  assume_role_policy   = data.aws_iam_policy_document.scheduler_trust.json
  permissions_boundary = var.permissions_boundary_arn
}

data "aws_iam_policy_document" "scheduler" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.this["sweeper"].arn]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "invoke-sweeper"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}

resource "aws_scheduler_schedule" "sweeper" {
  name                = "${var.name}-sweeper"
  description         = "Outbox reconciliation, stale-job recovery, expiry cleanup, counter healing."
  schedule_expression = var.sweeper_schedule
  state               = "ENABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.this["sweeper"].arn
    role_arn = aws_iam_role.scheduler.arn

    retry_policy {
      maximum_retry_attempts = 0 # the next run is the retry
    }
  }
}
