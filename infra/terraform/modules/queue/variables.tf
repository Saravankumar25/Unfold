variable "name" {
  description = "Resource name prefix, e.g. unfold-dev."
  type        = string
}

variable "visibility_timeout_seconds" {
  description = "Must be at least the dispatcher Lambda timeout (Lambda event source mappings require it)."
  type        = number
  default     = 60
}

variable "retention_seconds" {
  description = "Message retention for the queue and its DLQ (14 days = 1209600)."
  type        = number
  default     = 1209600
}

variable "max_receive_count" {
  description = "Receives before a message moves to the DLQ."
  type        = number
  default     = 5

  validation {
    condition     = var.max_receive_count >= 1 && var.max_receive_count <= 10
    error_message = "Keep the redrive bounded: 1 to 10 receives."
  }
}
