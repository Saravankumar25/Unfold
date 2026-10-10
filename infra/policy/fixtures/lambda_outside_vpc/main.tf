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

resource "aws_cloudwatch_log_group" "g" {
  name              = "/aws/lambda/x"
  retention_in_days = 14
}

resource "aws_lambda_function" "f" {
  function_name = "x"
  role          = "arn:aws:iam::123456789012:role/x"
  package_type  = "Image"
  image_uri     = "123456789012.dkr.ecr.us-east-1.amazonaws.com/x:1"

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.g.name
  }

  vpc_config {
    subnet_ids         = ["subnet-1"]
    security_group_ids = ["sg-1"]
  }
}
