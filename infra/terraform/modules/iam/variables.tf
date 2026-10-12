variable "name" {
  description = "Resource name prefix, e.g. unfold-dev."
  type        = string
}

variable "region" {
  type = string
}

variable "account_id" {
  description = "12-digit AWS account id (used in ARNs so the plan needs no API call)."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be 12 digits."
  }
}

variable "bucket_arn" {
  type = string
}

variable "table_arn" {
  type = string
}

variable "queue_arn" {
  type = string
}

variable "dlq_arn" {
  type = string
}

variable "ssm_parameter_arn_pattern" {
  description = "arn:aws:ssm:<region>:<account>:parameter/unfold/<env>/*"
  type        = string
}

variable "ecr_repository_arns" {
  type = list(string)
}

variable "cluster_name" {
  description = "ECS cluster name (the ARN is built from it so this module does not depend on the worker module)."
  type        = string
}

variable "task_definition_families" {
  description = "Task definition families the dispatcher may run."
  type        = list(string)
}

variable "worker_log_group_name" {
  type = string
}

variable "worker_secret_arns" {
  description = "SSM parameters the Fargate agent injects (the execution role may read only these)."
  type        = list(string)

  validation {
    condition     = length(var.worker_secret_arns) > 0
    error_message = "List at least one worker secret (the LLM key)."
  }
}

variable "permissions_boundary_arn" {
  description = "Boundary attached to every role (bootstrap output runtime_boundary_arn); null only for tests."
  type        = string
  default     = null
}
