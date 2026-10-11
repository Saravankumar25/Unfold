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

resource "aws_lambda_provisioned_concurrency_config" "p" {
  function_name                     = "x"
  qualifier                         = "1"
  provisioned_concurrent_executions = 2
}
