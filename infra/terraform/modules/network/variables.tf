variable "name" {
  description = "Resource name prefix, e.g. unfold-dev."
  type        = string
}

variable "region" {
  description = "AWS region (used to build gateway endpoint service names and default zones)."
  type        = string
}

variable "vpc_cidr" {
  description = "VPC CIDR; one /24 public subnet is carved out per zone."
  type        = string
  default     = "10.20.0.0/16"
}

variable "availability_zones" {
  description = "Zones for the public subnets. Empty = <region>a and <region>b."
  type        = list(string)
  default     = []
}
