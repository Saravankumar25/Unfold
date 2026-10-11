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

resource "aws_ssm_parameter" "key" {
  name  = "/unfold/dev/gemini_api_key_1"
  type  = "SecureString"
  value = "placeholder"
}
