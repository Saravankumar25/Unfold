output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}

output "lock_table" {
  value = aws_dynamodb_table.lock.name
}

output "plan_role_arn" {
  description = "AWS_ROLE_ARN for pull-request plan jobs."
  value       = aws_iam_role.plan.arn
}

output "apply_role_arn" {
  description = "AWS_ROLE_ARN for the protected apply job."
  value       = aws_iam_role.apply.arn
}

output "images_role_arn" {
  description = "AWS_ROLE_ARN for the image build and push job."
  value       = aws_iam_role.images.arn
}

output "runtime_boundary_arn" {
  description = "Pass as permissions_boundary_arn to the environments (its name is fixed: unfold-runtime-boundary)."
  value       = aws_iam_policy.runtime_boundary.arn
}
