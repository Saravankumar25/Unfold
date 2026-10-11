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

resource "aws_vpc" "v" {
  cidr_block = "10.0.0.0/16"
}

resource "aws_subnet" "s" {
  vpc_id     = aws_vpc.v.id
  cidr_block = "10.0.0.0/24"
}

resource "aws_eip" "e" {
  domain = "vpc"
}

resource "aws_nat_gateway" "n" {
  allocation_id = aws_eip.e.id
  subnet_id     = aws_subnet.s.id
}
