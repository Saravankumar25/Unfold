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

resource "aws_secretsmanager_secret" "s" {
  name = "x"
}

resource "aws_kms_key" "k" {
  description = "customer managed key bills monthly"
}
