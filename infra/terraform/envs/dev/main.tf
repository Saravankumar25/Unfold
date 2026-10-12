# Unfold, environment "dev". envs/prod is the same composition with its own state and defaults.
# Nothing here runs while idle: no ECS service, no NAT, no load balancer, no provisioned concurrency.

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project    = "unfold"
      env        = local.env
      managed_by = "terraform"
    }
  }
}

locals {
  env  = "dev"
  name = "unfold-${local.env}"

  worker_log_group = "/unfold/${local.env}/worker"
  boundary_arn     = var.permissions_boundary_arn != "" ? var.permissions_boundary_arn : "arn:aws:iam::${var.account_id}:policy/unfold-runtime-boundary"
  function_names   = ["${local.name}-api", "${local.name}-dispatcher", "${local.name}-task_stopped", "${local.name}-sweeper"]
  log_groups       = concat([for f in local.function_names : "/aws/lambda/${f}"], [local.worker_log_group])
  task_families    = ["${local.name}-analysis", "${local.name}-export"]

  # Application settings shared by the API, the dispatcher handlers and the Fargate tasks. Names are the
  # application's setting names (backend/src/unfold/core/config.py); the limits come from var.limits.
  app_env = merge(
    {
      APP_ENV             = "production"
      LOG_FORMAT          = "json"
      COOKIE_SECURE       = "true"
      ALLOW_DIRECT_UPLOAD = "false" # uploads go browser -> S3 (presigned POST); the API never carries the file
      PUBLIC_WEB_ORIGIN   = var.web_origin
      DENSE_BACKEND       = "artifact"
      GRAPH_BACKEND       = "memory"
      PERSISTENCE_BACKEND = "dynamodb"
      OBJECT_STORE_BUCKET = module.storage.bucket_name
      DYNAMODB_TABLE      = module.data.table_name
      JOBS_QUEUE_URL      = module.queue.queue_url
      ECS_CLUSTER_ARN     = module.worker.cluster_arn
      SSM_PATH_PREFIX     = module.secrets.path_prefix
      AWS_REGION_NAME     = var.region
      MODEL_THREADS       = "4" # measured: 4 threads cut a 102-chunk analysis from ~110 s to ~71 s (docs/infra/performance.md)
    },
    local.limit_env,
  )

  limit_env = {
    MAX_SESSIONS_PER_IP_PER_HOUR           = tostring(var.limits.sessions_per_ip_per_hour)
    MAX_SESSIONS_PER_HOUR                  = tostring(var.limits.sessions_global_per_hour)
    MAX_PROJECTS_PER_HOUR                  = tostring(var.limits.projects_per_session_per_hour)
    MAX_PROJECTS_PER_IP_PER_HOUR           = tostring(var.limits.projects_per_ip_per_hour)
    MAX_PROJECTS_PER_HOUR_GLOBAL           = tostring(var.limits.projects_global_per_hour)
    MAX_UPLOAD_URLS_PER_SESSION_PER_HOUR   = tostring(var.limits.upload_urls_per_session_per_hour)
    MAX_CHAT_MESSAGES_PER_MINUTE           = tostring(var.limits.chat_per_session_per_minute)
    MAX_CHAT_MESSAGES_PER_SESSION_PER_HOUR = tostring(var.limits.chat_per_session_per_hour)
    MAX_CHAT_MESSAGES_GLOBAL_PER_DAY       = tostring(var.limits.chat_global_per_day)
    MAX_EXPORTS_PER_SESSION_PER_HOUR       = tostring(var.limits.exports_per_session_per_hour)
    MAX_ACTIVE_JOBS_PER_SESSION            = tostring(var.limits.active_jobs_per_session)
    MAX_QUEUED_JOBS_GLOBAL                 = tostring(var.limits.queued_jobs_global)
    MAX_CONCURRENT_JOBS                    = tostring(var.limits.concurrent_analysis_tasks)
    MAX_ZIP_BYTES                          = tostring(var.limits.max_zip_bytes)
    MAX_REPO_ARCHIVE_BYTES                 = tostring(var.limits.max_tarball_bytes)
    MAX_EXTRACTED_BYTES                    = tostring(var.limits.max_extracted_bytes)
    MAX_FILE_COUNT                         = tostring(var.limits.max_file_count)
    MAX_SINGLE_FILE_BYTES                  = tostring(var.limits.max_single_file_bytes)
    MAX_COMPRESSION_RATIO                  = tostring(var.limits.max_compression_ratio)
    MAX_CHUNKS_PER_PROJECT                 = tostring(var.limits.chunks_per_project)
    JOB_TIMEOUT_SECONDS                    = tostring(var.limits.job_timeout_seconds)
    MAX_JOB_ATTEMPTS                       = tostring(var.limits.job_attempts)
    ANALYSIS_LLM_CALL_BUDGET               = tostring(var.limits.llm_calls_per_job)
    ANALYSIS_LLM_TOKEN_BUDGET              = tostring(var.limits.llm_tokens_per_job)
  }

}

module "network" {
  source = "../../modules/network"

  name   = local.name
  region = var.region
}

module "storage" {
  source = "../../modules/storage"

  bucket_name          = "${local.name}-${var.account_id}"
  cors_allowed_origins = [var.web_origin]
  project_expiry_days  = var.project_expiry_days
  force_destroy        = var.allow_destroy_data
}

module "data" {
  source = "../../modules/data"

  table_name          = local.name
  deletion_protection = !var.allow_destroy_data
}

module "queue" {
  source = "../../modules/queue"

  name = local.name
}

module "registry" {
  source = "../../modules/registry"

  name         = local.name
  force_delete = var.allow_destroy_data
}

module "secrets" {
  source = "../../modules/secrets"

  env                 = local.env
  account_id          = var.account_id
  region              = var.region
  worker_secret_names = var.worker_secret_names
}

module "iam" {
  source = "../../modules/iam"

  name                      = local.name
  region                    = var.region
  account_id                = var.account_id
  bucket_arn                = module.storage.bucket_arn
  table_arn                 = module.data.table_arn
  queue_arn                 = module.queue.queue_arn
  dlq_arn                   = module.queue.dlq_arn
  ssm_parameter_arn_pattern = module.secrets.parameter_arn_pattern
  ecr_repository_arns       = values(module.registry.repository_arns)
  cluster_name              = local.name
  task_definition_families  = local.task_families
  worker_log_group_name     = local.worker_log_group
  worker_secret_arns        = values(module.secrets.worker_parameter_arns)
  permissions_boundary_arn  = local.boundary_arn
}

module "observability" {
  source = "../../modules/observability"

  name                   = local.name
  log_group_names        = local.log_groups
  log_retention_days     = var.log_retention_days
  worker_log_group_name  = local.worker_log_group
  dlq_name               = "${local.name}-jobs-dlq"
  alarmed_function_names = ["${local.name}-dispatcher", "${local.name}-sweeper"]
  alert_emails           = var.alert_emails
  monthly_budget_usd     = var.monthly_budget_usd
  daily_budget_usd       = var.daily_budget_usd
}

module "worker" {
  source = "../../modules/worker"

  cluster_name            = local.name
  region                  = var.region
  worker_image_uri        = var.worker_image_uri
  exporter_image_uri      = var.exporter_image_uri
  enable_export_task      = var.enable_export_task
  task_role_arn           = module.iam.worker_task_role_arn
  export_task_role_arn    = module.iam.export_task_role_arn
  task_execution_role_arn = module.iam.task_execution_role_arn
  log_group_name          = local.worker_log_group
  analysis_cpu            = var.analysis_cpu
  analysis_memory         = var.analysis_memory
  environment             = merge(local.app_env, { AWS_REGION = var.region })
  secrets                 = module.secrets.worker_parameter_arns

  depends_on = [module.observability]
}

module "api" {
  source = "../../modules/api"

  function_name        = "${local.name}-api"
  role_arn             = module.iam.api_role_arn
  image_uri            = var.api_image_uri
  log_group_name       = "/aws/lambda/${local.name}-api"
  memory_mb            = var.api_memory_mb
  timeout_seconds      = var.api_timeout_seconds
  reserved_concurrency = var.api_reserved_concurrency
  environment          = local.app_env

  depends_on = [module.observability]
}

module "control" {
  source = "../../modules/control"

  name        = local.name
  account_id  = var.account_id
  role_arn    = module.iam.dispatcher_role_arn
  image_uri   = var.worker_image_uri
  queue_arn   = module.queue.queue_arn
  cluster_arn = module.worker.cluster_arn
  environment = merge(local.app_env, {
    TASK_DEFINITION_ANALYSIS = module.worker.task_definition_arns["analysis"]
    TASK_DEFINITION_EXPORT   = lookup(module.worker.task_definition_arns, "export", "")
    SUBNET_IDS               = join(",", module.network.public_subnet_ids)
    SECURITY_GROUP_IDS       = module.network.worker_security_group_id
  })

  depends_on = [module.observability]
}
