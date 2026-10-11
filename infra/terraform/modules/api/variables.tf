variable "function_name" {
  description = "Lambda function name, e.g. unfold-dev-api (log group /aws/lambda/<name> is created elsewhere)."
  type        = string
}

variable "role_arn" {
  type = string
}

variable "image_uri" {
  description = "ECR image URI (tag or digest) of the api target."
  type        = string
}

variable "log_group_name" {
  type = string
}

variable "architecture" {
  description = "x86_64 until arm64 wheels are verified."
  type        = string
  default     = "x86_64"

  validation {
    condition     = contains(["x86_64", "arm64"], var.architecture)
    error_message = "architecture must be x86_64 or arm64."
  }
}

variable "memory_mb" {
  description = "About 2 vCPU at 3538 MB; tune from the cold-start and chat benchmarks."
  type        = number
  default     = 3538
}

variable "timeout_seconds" {
  description = "Function timeout; also the longest streamed chat."
  type        = number
  default     = 120
}

variable "ephemeral_storage_mb" {
  type    = number
  default = 1024
}

variable "reserved_concurrency" {
  description = "Reserved concurrent executions: caps the API fleet (and so the bill). -1 leaves it unreserved."
  type        = number
  default     = 5

  validation {
    condition     = var.reserved_concurrency >= -1 && var.reserved_concurrency <= 100
    error_message = "Use -1 (unreserved) or 0 to 100."
  }
}

variable "environment" {
  description = "Non-secret settings (limits, resource names, SSM path). Secrets are read from SSM at start."
  type        = map(string)
  default     = {}
}
