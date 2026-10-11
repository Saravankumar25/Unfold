output "api_url" {
  description = "Function URL: set as API_INTERNAL_URL in the Vercel project."
  value       = module.api.function_url
}

output "bucket_name" {
  value = module.storage.bucket_name
}

output "table_name" {
  value = module.data.table_name
}

output "queue_url" {
  value = module.queue.queue_url
}

output "ecr_repository_urls" {
  description = "Push targets for CI (api, worker, exporter)."
  value       = module.registry.repository_urls
}

output "ssm_path_prefix" {
  description = "Put the real secret values under this path (see the runbook)."
  value       = module.secrets.path_prefix
}

output "alarm_topic_arn" {
  value = module.observability.alarm_topic_arn
}
