variable "name" {
  description = "Repository name prefix, e.g. unfold-dev."
  type        = string
}

variable "repositories" {
  description = "Image targets of backend/Dockerfile (docker/backend.Dockerfile)."
  type        = list(string)
  default     = ["api", "worker", "exporter"]
}

variable "keep_tagged_images" {
  description = "Tagged images kept per repository."
  type        = number
  default     = 5
}

variable "force_delete" {
  description = "Delete repositories that still hold images (dev only)."
  type        = bool
  default     = false
}
