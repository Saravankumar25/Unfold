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

resource "aws_s3_bucket" "b" {
  bucket = "unfold-policy-fixture-clean"
}

resource "aws_s3_bucket_public_access_block" "b" {
  bucket                  = aws_s3_bucket.b.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "b" {
  bucket = aws_s3_bucket.b.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

data "aws_iam_policy_document" "read" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.b.arn}/*"]
  }
}

resource "aws_cloudwatch_log_group" "g" {
  name              = "/aws/lambda/x"
  retention_in_days = 14
}

resource "aws_dynamodb_table" "t" {
  name                        = "x"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "PK"
  deletion_protection_enabled = true

  attribute {
    name = "PK"
    type = "S"
  }
}

resource "aws_ecr_repository" "r" {
  name                 = "x"
  image_tag_mutability = "IMMUTABLE"
}

resource "aws_ecr_lifecycle_policy" "r" {
  repository = aws_ecr_repository.r.name
  policy     = jsonencode({ rules = [{ rulePriority = 1, description = "x", selection = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 5 }, action = { type = "expire" } }] })
}

resource "aws_sqs_queue" "dlq" {
  name = "x-dlq"
}

resource "aws_sqs_queue" "q" {
  name = "x"
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 5
  })
}
