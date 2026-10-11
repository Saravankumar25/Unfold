# The API: one container-image Lambda behind a Function URL with response streaming (decision 4.1). It is not in a
# VPC (streaming Function URLs and cold start both want that). Edge protection is application quota plus reserved
# concurrency; the Vercel proxy authenticates itself with an HMAC header, never a static AWS key.

resource "aws_lambda_function" "api" {
  function_name = var.function_name
  role          = var.role_arn
  package_type  = "Image"
  image_uri     = var.image_uri
  architectures = [var.architecture]
  memory_size   = var.memory_mb
  timeout       = var.timeout_seconds

  # -1 = unreserved. New accounts may not be able to reserve anything until the quota is raised.
  reserved_concurrent_executions = var.reserved_concurrency

  ephemeral_storage {
    size = var.ephemeral_storage_mb
  }

  logging_config {
    log_format = "Text"
    log_group  = var.log_group_name
  }

  environment {
    variables = merge(
      {
        AWS_LWA_INVOKE_MODE          = "response_stream"
        AWS_LWA_READINESS_CHECK_PATH = "/health"
        AWS_LWA_PORT                 = "8000"
      },
      var.environment,
    )
  }
}

resource "aws_lambda_function_url" "api" {
  function_name      = aws_lambda_function.api.function_name
  authorization_type = "NONE"
  invoke_mode        = "RESPONSE_STREAM"
}

# A NONE-auth Function URL needs these resource-policy statements to be callable at all.
resource "aws_lambda_permission" "url" {
  statement_id           = "AllowPublicFunctionUrl"
  function_name          = aws_lambda_function.api.function_name
  action                 = "lambda:InvokeFunctionUrl"
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "url_invoke" {
  statement_id             = "AllowInvokeViaFunctionUrl"
  function_name            = aws_lambda_function.api.function_name
  action                   = "lambda:InvokeFunction"
  principal                = "*"
  invoked_via_function_url = true
}
