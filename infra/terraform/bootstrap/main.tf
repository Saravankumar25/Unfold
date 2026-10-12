# Bootstrap (own state, applied once by the owner with a human principal): the remote-state bucket, its lock table,
# the GitHub OIDC provider and the three deploy roles. After this exists, CI never holds an AWS key.
#
# Locking: the S3 backend's native lock file (use_lockfile) needs Terraform >= 1.10; this repository pins 1.9.8, so
# the DynamoDB lock table below is used. Switch to use_lockfile when the pinned version moves to >= 1.10 and
# delete the table in the same change.

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project    = "unfold"
      env        = "bootstrap"
      managed_by = "terraform"
    }
  }
}

locals {
  state_bucket = "unfold-tfstate-${var.account_id}"
  lock_table   = "unfold-tfstate-lock"
  repo         = "${var.github_owner}/${var.github_repo}"
  oidc_arn     = aws_iam_openid_connect_provider.github.arn
  oidc_host    = "token.actions.githubusercontent.com"
}

# --- state ----------------------------------------------------------------------------------------------------

resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

data "aws_iam_policy_document" "state_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.state_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.state]
}

resource "aws_dynamodb_table" "lock" {
  name         = local.lock_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  deletion_protection_enabled = true

  attribute {
    name = "LockID"
    type = "S"
  }

  point_in_time_recovery {
    enabled = false
  }
}

# --- GitHub OIDC ----------------------------------------------------------------------------------------------

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://${local.oidc_host}"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "trust" {
  for_each = {
    # plan: pull requests and the default branch only, read-only (run `terraform plan -lock=false`)
    plan = [
      "repo:${local.repo}:pull_request",
      "repo:${local.repo}:ref:refs/heads/${var.default_branch}",
    ]
    # apply: only a job that runs in a protected GitHub Environment (required reviewers are configured there)
    apply = [for e in var.apply_environments : "repo:${local.repo}:environment:${e}"]
    # images: only the default branch pushes images
    images = ["repo:${local.repo}:ref:refs/heads/${var.default_branch}"]
  }

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "${local.oidc_host}:sub"
      values   = each.value
    }
  }
}

# --- state access shared by plan and apply ----------------------------------------------------------------------

data "aws_iam_policy_document" "state_read" {
  statement {
    sid       = "ListStateBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketVersioning"]
    resources = [aws_s3_bucket.state.arn]
  }

  statement {
    sid       = "ReadState"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.state.arn}/envs/*"]
  }

}

data "aws_iam_policy_document" "state_write" {
  source_policy_documents = [data.aws_iam_policy_document.state_read.json]

  statement {
    sid       = "WriteState"
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.state.arn}/envs/*"]
  }

  statement {
    sid       = "StateLock"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem", "dynamodb:DescribeTable"]
    resources = [aws_dynamodb_table.lock.arn]
  }
}

# Read access to the stack, enough for `terraform plan` (refresh). Secrets are not here on purpose: Terraform
# creates no SSM parameters, and no plan job needs to read a secret value or any object in the data bucket.
data "aws_iam_policy_document" "stack_read" {
  statement {
    sid = "StackRead"
    actions = [
      "ec2:Describe*",
      "ecs:Describe*",
      "ecs:List*",
      "lambda:Get*",
      "lambda:List*",
      "iam:Get*",
      "iam:List*",
      "s3:GetBucketPolicy",
      "s3:GetBucketCORS",
      "s3:GetBucketTagging",
      "s3:GetBucketAcl",
      "s3:GetBucketLocation",
      "s3:GetBucketPublicAccessBlock",
      "s3:GetBucketOwnershipControls",
      "s3:GetBucketVersioning",
      "s3:GetBucketLogging",
      "s3:GetBucketWebsite",
      "s3:GetBucketRequestPayment",
      "s3:GetAccelerateConfiguration",
      "s3:GetReplicationConfiguration",
      "s3:GetLifecycleConfiguration",
      "s3:GetEncryptionConfiguration",
      "s3:GetBucketObjectLockConfiguration",
      "s3:ListAllMyBuckets",
      "dynamodb:Describe*",
      "dynamodb:ListTagsOfResource",
      "sqs:GetQueueAttributes",
      "sqs:GetQueueUrl",
      "sqs:ListQueueTags",
      "sqs:ListQueues",
      "ecr:Describe*",
      "ecr:GetLifecyclePolicy",
      "ecr:GetRepositoryPolicy",
      "ecr:ListTagsForResource",
      "events:Describe*",
      "events:List*",
      "scheduler:Get*",
      "scheduler:List*",
      "logs:Describe*",
      "logs:ListTagsForResource",
      "logs:ListTagsLogGroup",
      "cloudwatch:Describe*",
      "cloudwatch:ListTagsForResource",
      "sns:Get*",
      "sns:List*",
      "budgets:View*",
      "budgets:Describe*",
    ]
    resources = ["*"]
  }
}


# --- runtime permissions boundary --------------------------------------------------------------------------------
# Upper bound for every role the environments create (api, worker task, task execution, dispatcher, scheduler).
# Roles are created with this boundary attached (the apply role may create no role without it), so even a
# compromised apply job cannot mint a role that can do more than this: no IAM, no EC2, no state bucket, no
# resource outside the unfold-* namespace.

data "aws_iam_policy_document" "runtime_boundary" {
  statement {
    sid       = "DataPlane"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"]
    resources = flatten([for e in var.apply_environments : ["arn:aws:s3:::unfold-${e}-*/projects/*"]])
  }

  statement {
    sid       = "ListProjects"
    actions   = ["s3:ListBucket", "s3:ListBucketMultipartUploads"]
    resources = [for e in var.apply_environments : "arn:aws:s3:::unfold-${e}-*"]
  }

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
    resources = flatten([for e in var.apply_environments : [
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/unfold-${e}",
      "arn:aws:dynamodb:${var.region}:${var.account_id}:table/unfold-${e}/index/*",
    ]])
  }

  statement {
    sid       = "Queues"
    actions   = ["sqs:SendMessage", "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility"]
    resources = ["arn:aws:sqs:${var.region}:${var.account_id}:unfold-*"]
  }

  statement {
    sid       = "Tasks"
    actions   = ["ecs:RunTask", "ecs:ListTasks", "ecs:DescribeTasks", "ecs:StopTask", "ecs:TagResource"]
    resources = ["*"] # the stack scopes these further (task definition families, cluster condition)
  }

  statement {
    sid       = "PassTaskRoles"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${var.account_id}:role/unfold-*"]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  statement {
    sid       = "Secrets"
    actions   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = ["arn:aws:ssm:${var.region}:${var.account_id}:parameter/unfold/*"]
  }

  statement {
    sid     = "Logs"
    actions = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [
      "arn:aws:logs:${var.region}:${var.account_id}:log-group:/aws/lambda/unfold-*:*",
      "arn:aws:logs:${var.region}:${var.account_id}:log-group:/unfold/*:*",
    ]
  }

  statement {
    sid       = "ImagePull"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid       = "ImagePullLayers"
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
    resources = ["arn:aws:ecr:${var.region}:${var.account_id}:repository/unfold-*"]
  }

  statement {
    sid       = "ScheduleSweeper"
    actions   = ["lambda:InvokeFunction"]
    resources = ["arn:aws:lambda:${var.region}:${var.account_id}:function:unfold-*"]
  }
}

resource "aws_iam_policy" "runtime_boundary" {
  name        = "unfold-runtime-boundary"
  description = "Upper bound for every role created by the unfold environments."
  policy      = data.aws_iam_policy_document.runtime_boundary.json
}

# --- plan role: read everything, change nothing but state ------------------------------------------------------

resource "aws_iam_role" "plan" {
  name                 = "unfold-deploy-plan"
  assume_role_policy   = data.aws_iam_policy_document.trust["plan"].json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "plan" {
  source_policy_documents = [
    data.aws_iam_policy_document.state_read.json,
    data.aws_iam_policy_document.stack_read.json,
  ]
}

resource "aws_iam_role_policy" "plan" {
  name   = "plan"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.plan.json
}

# --- image push role: ECR upload to unfold-* repositories only -------------------------------------------------

resource "aws_iam_role" "images" {
  name                 = "unfold-deploy-images"
  assume_role_policy   = data.aws_iam_policy_document.trust["images"].json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "images" {
  statement {
    sid       = "EcrAuthorization"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # the action does not support resource-level permissions
  }

  statement {
    sid = "PushImages"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = ["arn:aws:ecr:${var.region}:${var.account_id}:repository/unfold-*"]
  }
}

resource "aws_iam_role_policy" "images" {
  name   = "push"
  role   = aws_iam_role.images.id
  policy = data.aws_iam_policy_document.images.json
}

# --- apply role: manages the unfold-* stack, nothing else ------------------------------------------------------
# Resource-level scoping where the service supports it (name prefix unfold-); EC2 networking cannot be scoped by
# name, so those actions are limited to the verbs Terraform needs for VPC, subnet, route, gateway, security
# group and endpoint management. The role is only assumable from a protected GitHub Environment.

resource "aws_iam_role" "apply" {
  name                 = "unfold-deploy-apply"
  assume_role_policy   = data.aws_iam_policy_document.trust["apply"].json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "apply" {
  source_policy_documents = [
    data.aws_iam_policy_document.state_write.json,
    data.aws_iam_policy_document.stack_read.json,
  ]

  statement {
    sid = "Network"
    actions = [
      "ec2:CreateVpc",
      "ec2:DeleteVpc",
      "ec2:ModifyVpcAttribute",
      "ec2:CreateSubnet",
      "ec2:DeleteSubnet",
      "ec2:ModifySubnetAttribute",
      "ec2:CreateInternetGateway",
      "ec2:DeleteInternetGateway",
      "ec2:AttachInternetGateway",
      "ec2:DetachInternetGateway",
      "ec2:CreateRouteTable",
      "ec2:DeleteRouteTable",
      "ec2:CreateRoute",
      "ec2:DeleteRoute",
      "ec2:AssociateRouteTable",
      "ec2:DisassociateRouteTable",
      "ec2:CreateSecurityGroup",
      "ec2:DeleteSecurityGroup",
      "ec2:AuthorizeSecurityGroupEgress",
      "ec2:RevokeSecurityGroupEgress",
      "ec2:AuthorizeSecurityGroupIngress",
      "ec2:RevokeSecurityGroupIngress",
      "ec2:CreateVpcEndpoint",
      "ec2:DeleteVpcEndpoints",
      "ec2:ModifyVpcEndpoint",
      "ec2:CreateTags",
      "ec2:DeleteTags",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "Bucket"
    actions   = ["s3:CreateBucket", "s3:DeleteBucket", "s3:PutBucket*", "s3:PutLifecycleConfiguration", "s3:PutEncryptionConfiguration", "s3:DeleteBucketPolicy", "s3:DeleteObject", "s3:DeleteObjectVersion", "s3:ListBucket", "s3:ListBucketVersions"]
    resources = flatten([for e in var.apply_environments : ["arn:aws:s3:::unfold-${e}-*", "arn:aws:s3:::unfold-${e}-*/*"]])
  }

  statement {
    sid       = "Table"
    actions   = ["dynamodb:CreateTable", "dynamodb:DeleteTable", "dynamodb:UpdateTable", "dynamodb:UpdateTimeToLive", "dynamodb:UpdateContinuousBackups", "dynamodb:TagResource", "dynamodb:UntagResource"]
    resources = [for e in var.apply_environments : "arn:aws:dynamodb:${var.region}:${var.account_id}:table/unfold-${e}"]
  }

  statement {
    sid       = "Queues"
    actions   = ["sqs:CreateQueue", "sqs:DeleteQueue", "sqs:SetQueueAttributes", "sqs:TagQueue", "sqs:UntagQueue"]
    resources = ["arn:aws:sqs:${var.region}:${var.account_id}:unfold-*"]
  }

  statement {
    sid = "Registry"
    actions = [
      "ecr:CreateRepository",
      "ecr:DeleteRepository",
      "ecr:PutImageScanningConfiguration",
      "ecr:PutImageTagMutability",
      "ecr:PutLifecyclePolicy",
      "ecr:DeleteLifecyclePolicy",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:TagResource",
      "ecr:UntagResource",
    ]
    resources = ["arn:aws:ecr:${var.region}:${var.account_id}:repository/unfold-*"]
  }

  statement {
    sid       = "Compute"
    actions   = ["ecs:CreateCluster", "ecs:DeleteCluster", "ecs:UpdateCluster", "ecs:UpdateClusterSettings", "ecs:RegisterTaskDefinition", "ecs:DeregisterTaskDefinition", "ecs:DeleteTaskDefinitions", "ecs:TagResource", "ecs:UntagResource"]
    resources = ["*"] # task definitions are not addressable before they exist
  }

  statement {
    sid = "Functions"
    actions = [
      "lambda:CreateFunction",
      "lambda:DeleteFunction",
      "lambda:UpdateFunctionCode",
      "lambda:UpdateFunctionConfiguration",
      "lambda:PutFunctionConcurrency",
      "lambda:DeleteFunctionConcurrency",
      "lambda:CreateFunctionUrlConfig",
      "lambda:UpdateFunctionUrlConfig",
      "lambda:DeleteFunctionUrlConfig",
      "lambda:AddPermission",
      "lambda:RemovePermission",
      "lambda:CreateEventSourceMapping",
      "lambda:UpdateEventSourceMapping",
      "lambda:DeleteEventSourceMapping",
      "lambda:TagResource",
      "lambda:UntagResource",
    ]
    resources = [
      "arn:aws:lambda:${var.region}:${var.account_id}:function:unfold-*",
      "arn:aws:lambda:${var.region}:${var.account_id}:event-source-mapping:*",
    ]
  }

  statement {
    sid     = "Events"
    actions = ["events:PutRule", "events:DeleteRule", "events:PutTargets", "events:RemoveTargets", "events:TagResource", "events:UntagResource", "scheduler:CreateSchedule", "scheduler:UpdateSchedule", "scheduler:DeleteSchedule", "scheduler:TagResource", "scheduler:UntagResource"]
    resources = [
      "arn:aws:events:${var.region}:${var.account_id}:rule/unfold-*",
      "arn:aws:scheduler:${var.region}:${var.account_id}:schedule/default/unfold-*",
    ]
  }

  statement {
    sid     = "LogsAndAlarms"
    actions = ["logs:CreateLogGroup", "logs:DeleteLogGroup", "logs:PutRetentionPolicy", "logs:PutMetricFilter", "logs:DeleteMetricFilter", "logs:TagResource", "logs:UntagResource", "cloudwatch:PutMetricAlarm", "cloudwatch:DeleteAlarms", "cloudwatch:TagResource", "cloudwatch:UntagResource"]
    resources = [
      "arn:aws:logs:${var.region}:${var.account_id}:log-group:/aws/lambda/unfold-*",
      "arn:aws:logs:${var.region}:${var.account_id}:log-group:/aws/lambda/unfold-*:*",
      "arn:aws:logs:${var.region}:${var.account_id}:log-group:/unfold/*",
      "arn:aws:logs:${var.region}:${var.account_id}:log-group:/unfold/*:*",
      "arn:aws:cloudwatch:${var.region}:${var.account_id}:alarm:unfold-*",
    ]
  }

  statement {
    sid       = "Notifications"
    actions   = ["sns:CreateTopic", "sns:DeleteTopic", "sns:SetTopicAttributes", "sns:Subscribe", "sns:Unsubscribe", "sns:TagResource", "sns:UntagResource"]
    resources = ["arn:aws:sns:${var.region}:${var.account_id}:unfold-*"]
  }

  statement {
    sid       = "Budgets"
    actions   = ["budgets:ModifyBudget", "budgets:CreateBudgetAction", "budgets:TagResource", "budgets:UntagResource"]
    resources = ["arn:aws:budgets::${var.account_id}:budget/unfold-*"]
  }

  # A role the stack creates must carry the runtime boundary, so an inline policy written onto it can never
  # exceed what the boundary allows (no path from "can write unfold-* roles" to administrator).
  statement {
    sid       = "CreateBoundedRoles"
    actions   = ["iam:CreateRole"]
    resources = ["arn:aws:iam::${var.account_id}:role/unfold-*"]

    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [aws_iam_policy.runtime_boundary.arn]
    }
  }

  statement {
    sid       = "ManageStackRoles"
    actions   = ["iam:DeleteRole", "iam:UpdateAssumeRolePolicy", "iam:TagRole", "iam:UntagRole", "iam:PutRolePolicy", "iam:DeleteRolePolicy"]
    resources = ["arn:aws:iam::${var.account_id}:role/unfold-*"]
  }

  statement {
    sid     = "ProtectDeployRolesBoundaryAndState"
    effect  = "Deny"
    actions = ["iam:*"]
    resources = [
      "arn:aws:iam::${var.account_id}:role/unfold-deploy-*",
      aws_iam_policy.runtime_boundary.arn,
      local.oidc_arn,
    ]
  }

  statement {
    sid       = "NeverDropTheBoundary"
    effect    = "Deny"
    actions   = ["iam:DeleteRolePermissionsBoundary", "iam:PutRolePermissionsBoundary"]
    resources = ["*"]
  }

  statement {
    sid    = "ProtectStateBucketAndLock"
    effect = "Deny"
    actions = [
      "s3:DeleteBucket",
      "s3:PutBucket*",
      "s3:DeleteBucketPolicy",
      "s3:DeleteObjectVersion",
      "s3:PutLifecycleConfiguration",
      "s3:PutEncryptionConfiguration",
      "dynamodb:DeleteTable",
      "dynamodb:UpdateTable",
    ]
    resources = [
      aws_s3_bucket.state.arn,
      "${aws_s3_bucket.state.arn}/*",
      aws_dynamodb_table.lock.arn,
    ]
  }

  statement {
    sid       = "PassStackRoles"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${var.account_id}:role/unfold-*"]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com", "ecs-tasks.amazonaws.com", "scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "apply" {
  name   = "stack"
  role   = aws_iam_role.apply.id
  policy = data.aws_iam_policy_document.apply.json
}
