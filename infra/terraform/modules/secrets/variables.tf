variable "env" {
  description = "Environment name (dev, prod); part of the parameter path /unfold/<env>/."
  type        = string
}

variable "account_id" {
  type = string
}

variable "region" {
  type = string
}

variable "worker_secret_names" {
  description = "Parameter names (lower case, = application setting names) injected into Fargate tasks. Each must exist."
  type        = list(string)
  default     = ["gemini_api_key_1"]
}
