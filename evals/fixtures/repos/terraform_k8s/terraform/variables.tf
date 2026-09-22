variable "project_id" {
  type        = string
  description = "GCP project id"
}

variable "region" {
  type    = string
  default = "europe-west1"
}

variable "subnet_cidr" {
  type    = string
  default = "10.10.0.0/20"
}

variable "node_count" {
  type    = number
  default = 2
}

variable "machine_type" {
  type    = string
  default = "e2-standard-2"
}

variable "db_tier" {
  type    = string
  default = "db-custom-1-3840"
}
