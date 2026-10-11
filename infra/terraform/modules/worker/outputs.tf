output "cluster_arn" {
  value = aws_ecs_cluster.this.arn
}

output "cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "task_definition_arns" {
  description = "Task definition ARN (with revision) by task name (analysis, export)."
  value       = { for k, t in aws_ecs_task_definition.this : k => t.arn }
}

output "task_definition_families" {
  value = [for t in aws_ecs_task_definition.this : t.family]
}
