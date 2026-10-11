output "path_prefix" {
  description = "Parameter path the API loader reads and the IAM policies are scoped to."
  value       = local.prefix
}

output "parameter_arn_pattern" {
  description = "IAM resource pattern covering every parameter of this environment."
  value       = "arn:aws:ssm:${var.region}:${var.account_id}:parameter${local.prefix}*"
}

output "worker_parameter_arns" {
  description = "Environment variable name -> parameter ARN for the Fargate task definition."
  value       = { for n in var.worker_secret_names : upper(n) => "arn:aws:ssm:${var.region}:${var.account_id}:parameter${local.prefix}${n}" }
}
