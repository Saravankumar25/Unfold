variable "region" {
  description = "AWS region. Latency to Vercel, the free tier and Fargate pricing decide it; both us-east-1 and ap-south-1 offer every service used."
  type        = string
  default     = "us-east-1"
}

variable "account_id" {
  description = "12-digit AWS account id (bucket name and ARNs; avoids an API call at plan time)."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be 12 digits."
  }
}

variable "permissions_boundary_arn" {
  description = "Runtime permissions boundary (bootstrap output). Empty = arn:aws:iam::<account>:policy/unfold-runtime-boundary."
  type        = string
  default     = ""
}

variable "web_origin" {
  description = "Origin of the Vercel site, e.g. https://unfold.vercel.app (CORS for uploads, PUBLIC_WEB_ORIGIN)."
  type        = string

  validation {
    condition     = can(regex("^https://", var.web_origin))
    error_message = "web_origin must be an https:// origin."
  }
}

variable "alert_emails" {
  description = "Recipients of alarms and budget alerts."
  type        = list(string)
}

# --- images (pushed by CI to the registry module's repositories before the first apply of the compute modules) ---

variable "api_image_uri" {
  description = "ECR image URI (tag or digest) of the api target."
  type        = string
}

variable "worker_image_uri" {
  description = "ECR image URI of the worker target (analysis tasks and the control Lambdas)."
  type        = string
}

variable "exporter_image_uri" {
  description = "ECR image URI of the exporter target."
  type        = string
}

variable "enable_export_task" {
  description = "Define the export task definition (phase 3 export job)."
  type        = bool
  default     = true
}

variable "worker_secret_names" {
  description = "SSM parameter names (under /unfold/<env>/) injected into analysis tasks; each must exist before the apply that lists it."
  type        = list(string)
  default     = ["gemini_api_key_1"]
}

# --- sizing ---

variable "analysis_cpu" {
  type    = number
  default = 4096
}

variable "analysis_memory" {
  type    = number
  default = 8192
}

variable "api_memory_mb" {
  type    = number
  default = 3538
}

variable "api_timeout_seconds" {
  type    = number
  default = 120
}

variable "api_reserved_concurrency" {
  description = "Caps the API fleet. -1 = unreserved (use when the account quota is below about 15)."
  type        = number
  default     = 10
}

# --- retention and cost ---

variable "project_expiry_days" {
  type    = number
  default = 4
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "monthly_budget_usd" {
  type    = string
  default = "20"
}

variable "daily_budget_usd" {
  type    = string
  default = "5"
}

variable "allow_destroy_data" {
  description = "Lets terraform destroy the bucket contents, the ECR images and the table. Off outside throwaway use."
  type        = bool
  default     = false
}

# --- application limits (decision 4.9). Conservative for a public share; raise deliberately. ---

variable "limits" {
  description = "Every quota and cap of the limits table; each maps to one application setting (see locals.limit_env)."
  type = object({
    sessions_per_ip_per_hour         = optional(number, 20)
    sessions_global_per_hour         = optional(number, 100)
    projects_per_session_per_hour    = optional(number, 10)
    projects_per_ip_per_hour         = optional(number, 30)
    projects_global_per_hour         = optional(number, 40)
    upload_urls_per_session_per_hour = optional(number, 10)
    chat_per_session_per_minute      = optional(number, 30)
    chat_per_session_per_hour        = optional(number, 200)
    chat_global_per_day              = optional(number, 2000)
    exports_per_session_per_hour     = optional(number, 5)
    active_jobs_per_session          = optional(number, 2)
    queued_jobs_global               = optional(number, 20)
    concurrent_analysis_tasks        = optional(number, 1)
    max_zip_bytes                    = optional(number, 52428800)
    max_tarball_bytes                = optional(number, 104857600)
    max_extracted_bytes              = optional(number, 314572800)
    max_file_count                   = optional(number, 20000)
    max_single_file_bytes            = optional(number, 1048576)
    max_compression_ratio            = optional(number, 100)
    chunks_per_project               = optional(number, 2500)
    job_timeout_seconds              = optional(number, 1800)
    job_attempts                     = optional(number, 3)
    llm_calls_per_job                = optional(number, 150)
    llm_tokens_per_job               = optional(number, 1500000)
  })
  default = {}
}
