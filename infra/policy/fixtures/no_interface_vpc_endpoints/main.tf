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

resource "aws_vpc_endpoint" "e" {
  vpc_id              = "vpc-123"
  service_name        = "com.amazonaws.us-east-1.sqs"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = ["subnet-1"]
  private_dns_enabled = true
}
