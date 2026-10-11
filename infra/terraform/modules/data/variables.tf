variable "table_name" {
  description = "DynamoDB table name, e.g. unfold-dev."
  type        = string
}

variable "deletion_protection" {
  description = "Refuse table deletion until switched off."
  type        = bool
  default     = true
}
