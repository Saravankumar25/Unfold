# ECS cluster and task definitions only. There is deliberately NO aws_ecs_service: nothing runs while idle, the
# dispatcher Lambda calls RunTask per job and the task exits when the job ends (decision 4.3). The container
# command is set per run (`analysis <job_id>`); the default below only documents the entrypoint.

resource "aws_ecs_cluster" "this" {
  name = var.cluster_name

  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

locals {
  tasks = merge(
    {
      analysis = {
        image  = var.worker_image_uri
        cpu    = var.analysis_cpu
        memory = var.analysis_memory
      }
    },
    var.enable_export_task ? {
      export = {
        image  = var.exporter_image_uri
        cpu    = var.export_cpu
        memory = var.export_memory
      }
    } : {},
  )
}

resource "aws_ecs_task_definition" "this" {
  for_each = local.tasks

  family                   = "${var.cluster_name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  execution_role_arn       = var.task_execution_role_arn
  task_role_arn            = var.task_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  ephemeral_storage {
    size_in_gib = var.ephemeral_storage_gib
  }

  container_definitions = jsonencode([
    {
      name      = "worker"
      image     = each.value.image
      essential = true
      command   = [each.key]

      environment = [for k, v in var.environment : { name = k, value = v }]
      secrets     = [for k, arn in var.secrets : { name = k, valueFrom = arn }]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = var.log_group_name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = each.key
        }
      }
    }
  ])
}
