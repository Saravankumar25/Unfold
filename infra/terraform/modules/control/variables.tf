variable "name" {
  description = "Resource name prefix, e.g. unfold-dev."
  type        = string
}

variable "account_id" {
  type = string
}

variable "role_arn" {
  description = "Dispatcher role (iam module): SQS, ECS RunTask, PassRole, S3 and DynamoDB."
  type        = string
}

variable "image_uri" {
  description = "ECR image URI of the worker target (also contains the control handlers)."
  type        = string
}

variable "queue_arn" {
  type = string
}

variable "cluster_arn" {
  type = string
}

variable "handlers" {
  description = "Image command (handler path) per function; the Part II worker.dispatch module defines them."
  type = object({
    dispatcher   = string
    task_stopped = string
    sweeper      = string
  })
  default = {
    dispatcher   = "unfold.worker.dispatch.dispatcher_handler"
    task_stopped = "unfold.worker.dispatch.task_stopped_handler"
    sweeper      = "unfold.worker.dispatch.sweeper_handler"
  }
}

variable "entry_point" {
  description = "Replaces the worker image entry point so the handlers run under the Lambda runtime interface client."
  type        = list(string)
  default     = ["python", "-m", "awslambdaric"]
}

variable "environment" {
  description = "Settings for all three handlers (queue url, cluster, task definitions, subnets, limits, table, bucket)."
  type        = map(string)
  default     = {}
}

variable "architecture" {
  type    = string
  default = "x86_64"

  validation {
    condition     = contains(["x86_64", "arm64"], var.architecture)
    error_message = "architecture must be x86_64 or arm64."
  }
}

variable "memory_mb" {
  type    = number
  default = 512
}

variable "dispatcher_timeout_seconds" {
  description = "Must stay below the queue visibility timeout."
  type        = number
  default     = 30
}

variable "sweeper_timeout_seconds" {
  type    = number
  default = 300
}

variable "dispatcher_max_concurrency" {
  description = "Parallel dispatcher invocations from the queue (2 = at most two RunTask calls at once)."
  type        = number
  default     = 2

  validation {
    condition     = var.dispatcher_max_concurrency >= 2 && var.dispatcher_max_concurrency <= 10
    error_message = "SQS event source mapping maximum concurrency must be 2 to 10."
  }
}

variable "sweeper_schedule" {
  description = "EventBridge Scheduler expression for the sweeper (rate(15 minutes) stays inside the free tier)."
  type        = string
  default     = "rate(15 minutes)"
}

variable "permissions_boundary_arn" {
  type    = string
  default = null
}
