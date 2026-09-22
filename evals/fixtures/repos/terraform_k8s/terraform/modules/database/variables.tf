variable "name" {
  type = string
}

variable "region" {
  type = string
}

variable "network_id" {
  type = string
}

variable "tier" {
  type = string
}

variable "db_password" {
  type      = string
  sensitive = true
  default   = null
}
