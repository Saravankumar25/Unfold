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

resource "aws_iam_user" "u" {
  name = "ci"
}

resource "aws_iam_access_key" "k" {
  user = aws_iam_user.u.name
}
