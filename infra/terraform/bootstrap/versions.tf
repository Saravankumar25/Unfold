terraform {
  required_version = ">= 1.9.8, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.68"
    }
  }

  # Local state on purpose: this root creates the remote state bucket. After the first apply, either keep the
  # local state file somewhere safe (it holds no secrets) or migrate it with `terraform init -migrate-state`
  # to a backend block pointing at the bucket created here.
}
