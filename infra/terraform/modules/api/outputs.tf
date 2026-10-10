output "function_name" {
  value = aws_lambda_function.api.function_name
}

output "function_arn" {
  value = aws_lambda_function.api.arn
}

output "function_url" {
  description = "API_INTERNAL_URL for the Vercel proxy."
  value       = aws_lambda_function_url.api.function_url
}
