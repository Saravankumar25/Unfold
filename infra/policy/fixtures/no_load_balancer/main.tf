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

resource "aws_lb" "l" {
  name               = "x"
  load_balancer_type = "application"
  subnets            = ["subnet-1", "subnet-2"]
}
