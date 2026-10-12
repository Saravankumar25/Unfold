terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.68"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

resource "aws_ecr_repository" "r" {
  name                 = "x"
  image_tag_mutability = "MUTABLE"
}
