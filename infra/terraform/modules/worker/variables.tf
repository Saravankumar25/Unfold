variable "cluster_name" {
  description = "ECS cluster name and task family prefix, e.g. unfold-dev."
  type        = string
}

variable "region" {
  type = string
}

variable "worker_image_uri" {
  description = "ECR image URI (tag or digest) of the worker target."
  type        = string
}

variable "exporter_image_uri" {
  description = "ECR image URI of the exporter target (used when enable_export_task)."
  type        = string
}

variable "enable_export_task" {
  description = "Define the export task (the phase 3 export job); off until that image exists."
  type        = bool
  default     = true
}

variable "task_role_arn" {
  type = string
}

variable "export_task_role_arn" {
  type = string
}

variable "task_execution_role_arn" {
  type = string
}

variable "log_group_name" {
  description = "Existing CloudWatch log group (created with retention by the observability module)."
  type        = string
}

variable "analysis_cpu" {
  description = "Fargate CPU units for analysis (4096 = 4 vCPU); size by the benchmark, not by guess."
  type        = number
  default     = 4096
}

variable "analysis_memory" {
  description = "Fargate memory MiB for analysis (valid pair with analysis_cpu)."
  type        = number
  default     = 8192
}

variable "export_cpu" {
  type    = number
  default = 2048
}

variable "export_memory" {
  type    = number
  default = 4096
}

variable "cpu_architecture" {
  description = "X86_64 until arm64 wheels (onnxruntime, tokenizers, tree-sitter pack) are verified."
  type        = string
  default     = "X86_64"

  validation {
    condition     = contains(["X86_64", "ARM64"], var.cpu_architecture)
    error_message = "cpu_architecture must be X86_64 or ARM64."
  }
}

variable "ephemeral_storage_gib" {
  type    = number
  default = 21
}

variable "environment" {
  description = "Non-secret environment variables for the container (settings, limits, resource names)."
  type        = map(string)
  default     = {}
}

variable "secrets" {
  description = "Environment variable name -> SSM parameter ARN injected by the Fargate agent."
  type        = map(string)
  default     = {}
}
