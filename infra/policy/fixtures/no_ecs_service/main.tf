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

resource "aws_ecs_cluster" "c" {
  name = "x"
}

resource "aws_ecs_task_definition" "t" {
  family                   = "x"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  container_definitions    = jsonencode([{ name = "a", image = "busybox", essential = true }])
}

resource "aws_ecs_service" "s" {
  name            = "x"
  cluster         = aws_ecs_cluster.c.id
  task_definition = aws_ecs_task_definition.t.arn
  desired_count   = 1
  launch_type     = "FARGATE"
}
