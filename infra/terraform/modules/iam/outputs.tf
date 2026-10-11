output "api_role_arn" {
  value = aws_iam_role.api.arn
}

output "worker_task_role_arn" {
  value = aws_iam_role.worker_task.arn
}

output "task_execution_role_arn" {
  value = aws_iam_role.task_execution.arn
}

output "dispatcher_role_arn" {
  value = aws_iam_role.dispatcher.arn
}
