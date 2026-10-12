variable "region" {
  description = "Region of the state bucket and lock table."
  type        = string
  default     = "us-east-1"
}

variable "account_id" {
  description = "12-digit AWS account id (state bucket name, role ARNs)."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be 12 digits."
  }
}

variable "github_owner" {
  description = "GitHub account or organisation that owns the repository."
  type        = string
  default     = "udarshmarthala"
}

variable "github_repo" {
  type    = string
  default = "unfold"
}

variable "default_branch" {
  description = "The only branch allowed to push images."
  type        = string
  default     = "main"
}

variable "apply_environments" {
  description = "GitHub Environments allowed to assume the apply role (protect them with required reviewers)."
  type        = list(string)
  default     = ["dev", "prod"]
}

variable "create_oidc_provider" {
  description = "Create the account's GitHub OIDC provider; false reuses the one that already exists (one per account)."
  type        = bool
  default     = true
}
