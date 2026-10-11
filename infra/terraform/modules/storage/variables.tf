variable "bucket_name" {
  description = "Globally unique bucket name, e.g. unfold-dev-<account id>."
  type        = string
}

variable "cors_allowed_origins" {
  description = "Web origins allowed to POST uploads (the Vercel site). Never \"*\"."
  type        = list(string)

  validation {
    condition     = length(var.cors_allowed_origins) > 0 && !contains(var.cors_allowed_origins, "*")
    error_message = "List the exact web origins; a wildcard origin is not allowed."
  }
}

variable "project_expiry_days" {
  description = "Backstop expiry for everything under projects/ (the application deletes earlier)."
  type        = number
  default     = 4
}

variable "force_destroy" {
  description = "Allow destroying a non-empty bucket (dev only)."
  type        = bool
  default     = false
}
