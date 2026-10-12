# Secret *names* and ARNs only. Terraform deliberately creates no aws_ssm_parameter: a refresh reads a SecureString
# back with decryption, so the real value would land in the state file (and be readable by every plan job). The
# parameters are created out of band, once per environment (runbook, docs/infra/ci-additions.md):
#
#   aws ssm put-parameter --name /unfold/<env>/gemini_api_key_1 --type SecureString --value ... --overwrite
#
# SecureString with the AWS-managed key `alias/aws/ssm` is free (standard tier). The API Lambda reads the whole
# path at cold start; Fargate tasks get the names listed in var.worker_secret_names injected by the agent, so a
# parameter must exist before it is listed there.

locals {
  prefix = "/unfold/${var.env}/"
}
