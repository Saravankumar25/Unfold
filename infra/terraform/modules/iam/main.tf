# Four runtime roles, one per principal, each scoped to the exact ARNs it needs (decision 4.6 / section 6 #10):
#   api            Lambda unfold-api
#   worker_task    Fargate analysis/export task (what the application code may do)
#   task_execution Fargate agent (image pull, log streams, injecting SSM secrets)
#   dispatcher     Lambda dispatcher, task_stopped and sweeper handlers
# The deploy roles live in bootstrap (separate state). Nothing here uses a wildcard action or an access key.

locals {
  ecs_task_definition_arns = [for family in var.task_definition_families : "arn:aws:ecs:${var.region}:${var.account_id}:task-definition/${family}:*"]
  ecs_task_arn             = "arn:aws:ecs:${var.region}:${var.account_id}:task/${var.cluster_name}/*"
  ecs_cluster_arn          = "arn:aws:ecs:${var.region}:${var.account_id}:cluster/${var.cluster_name}"
  lambda_log_groups        = "arn:aws:logs:${var.region}:${var.account_id}:log-group:/aws/lambda/${var.name}-*:*"
  worker_log_group         = "arn:aws:logs:${var.region}:${var.account_id}:log-group:${var.worker_log_group_name}:*"
  table_and_indexes        = [var.table_arn, "${var.table_arn}/index/*"]
}

# --- shared statements ------------------------------------------------------------------------------------------

data "aws_iam_policy_document" "table" {
  statement {
    sid = "ControlTable"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:BatchGetItem",
      "dynamodb:BatchWriteItem",
      "dynamodb:Query",
      "dynamodb:ConditionCheckItem",
    ]
    resources = local.table_and_indexes
  }
}

data "aws_iam_policy_document" "objects" {
  statement {
    sid       = "ObjectsUnderProjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"]
    resources = ["${var.bucket_arn}/projects/*"]
  }

  statement {
    sid       = "ListProjectPrefix"
    actions   = ["s3:ListBucket", "s3:ListBucketMultipartUploads"]
    resources = [var.bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["projects/*", "projects/"]
    }
  }
}

# API and worker read and write project content.
data "aws_iam_policy_document" "data_plane" {
  source_policy_documents = [
    data.aws_iam_policy_document.objects.json,
    data.aws_iam_policy_document.table.json,
  ]
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}

data "aws_iam_policy_document" "ecs_tasks_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}

# --- api ----------------------------------------------------------------------------------------------------------

resource "aws_iam_role" "api" {
  name                 = "${var.name}-api"
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  permissions_boundary = var.permissions_boundary_arn
}

data "aws_iam_policy_document" "api" {
  source_policy_documents = [data.aws_iam_policy_document.data_plane.json]

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [local.lambda_log_groups]
  }

  statement {
    sid       = "EnqueueJobs"
    actions   = ["sqs:SendMessage", "sqs:GetQueueAttributes"]
    resources = [var.queue_arn]
  }

  statement {
    sid     = "ReadSecrets"
    actions = ["ssm:GetParameters", "ssm:GetParameter", "ssm:GetParametersByPath"]
    # GetParametersByPath is evaluated against the path itself as well as the parameters under it
    resources = [var.ssm_parameter_arn_pattern, trimsuffix(var.ssm_parameter_arn_pattern, "/*")]
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "runtime"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

# --- worker task ------------------------------------------------------------------------------------------------

resource "aws_iam_role" "worker_task" {
  name                 = "${var.name}-worker-task"
  assume_role_policy   = data.aws_iam_policy_document.ecs_tasks_trust.json
  permissions_boundary = var.permissions_boundary_arn
}

resource "aws_iam_role_policy" "worker_task" {
  name   = "runtime"
  role   = aws_iam_role.worker_task.id
  policy = data.aws_iam_policy_document.data_plane.json
}

# --- task execution -------------------------------------------------------------------------------------------

resource "aws_iam_role" "task_execution" {
  name                 = "${var.name}-task-execution"
  assume_role_policy   = data.aws_iam_policy_document.ecs_tasks_trust.json
  permissions_boundary = var.permissions_boundary_arn
}

data "aws_iam_policy_document" "task_execution" {
  statement {
    sid       = "EcrAuthorization"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # the action does not support resource-level permissions
  }

  statement {
    sid       = "PullImages"
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
    resources = var.ecr_repository_arns
  }

  statement {
    sid       = "WorkerLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [local.worker_log_group]
  }

  statement {
    sid       = "InjectSecrets"
    actions   = ["ssm:GetParameters"]
    resources = var.worker_secret_arns
  }
}

resource "aws_iam_role_policy" "task_execution" {
  name   = "execution"
  role   = aws_iam_role.task_execution.id
  policy = data.aws_iam_policy_document.task_execution.json
}

# --- dispatcher (dispatcher, task_stopped, sweeper) ------------------------------------------------------------

resource "aws_iam_role" "dispatcher" {
  name                 = "${var.name}-dispatcher"
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  permissions_boundary = var.permissions_boundary_arn
}

data "aws_iam_policy_document" "dispatcher" {
  source_policy_documents = [data.aws_iam_policy_document.table.json]

  # Cleanup only deletes (blobs, dense artifact, multipart parts); the handlers never read or write content.
  statement {
    sid       = "DeleteProjectObjects"
    actions   = ["s3:DeleteObject", "s3:AbortMultipartUpload"]
    resources = ["${var.bucket_arn}/projects/*"]
  }

  statement {
    sid       = "ListProjectPrefixForCleanup"
    actions   = ["s3:ListBucket", "s3:ListBucketMultipartUploads"]
    resources = [var.bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["projects/*", "projects/"]
    }
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [local.lambda_log_groups]
  }

  statement {
    sid       = "ConsumeJobs"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"] # what the event source mapping uses
    resources = [var.queue_arn]
  }

  statement {
    sid       = "ResendJobs"
    actions   = ["sqs:SendMessage"]
    resources = [var.queue_arn]
  }

  statement {
    sid       = "InspectDeadLetters"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [var.dlq_arn]
  }

  statement {
    sid       = "RunTasks"
    actions   = ["ecs:RunTask"]
    resources = local.ecs_task_definition_arns

    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [local.ecs_cluster_arn]
    }
  }

  statement {
    sid       = "TagTasks"
    actions   = ["ecs:TagResource"]
    resources = [local.ecs_task_arn]

    condition {
      test     = "StringEquals"
      variable = "ecs:CreateAction"
      values   = ["RunTask"]
    }
  }

  statement {
    sid       = "ListTasks"
    actions   = ["ecs:ListTasks"]
    resources = ["*"] # list actions take no resource; the condition pins the cluster

    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [local.ecs_cluster_arn]
    }
  }

  statement {
    sid       = "ManageTasks"
    actions   = ["ecs:DescribeTasks", "ecs:StopTask"]
    resources = [local.ecs_task_arn]
  }

  statement {
    sid       = "PassTaskRoles"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.worker_task.arn, aws_iam_role.task_execution.arn]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "dispatcher" {
  name   = "runtime"
  role   = aws_iam_role.dispatcher.id
  policy = data.aws_iam_policy_document.dispatcher.json
}
