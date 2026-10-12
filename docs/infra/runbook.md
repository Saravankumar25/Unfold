# Runbook: deploy, roll back, drain the queue, rotate keys

Short procedures for an operator. Reasons and numbers are in `deployment.md`, `operations.md` and `ci-additions.md`.
Nothing is deployed yet: every `terraform apply` below needs the owner's explicit authorization.

## Deploy

1. CI builds and pushes `api-lambda`, `worker` and `exporter` images tagged with the git SHA (immutable tags).
2. `terraform apply` `infra/terraform/envs/dev` with the three image URIs. Run the smoke checklist (`operations.md` section 6).
3. Repeat for `envs/prod` from the protected GitHub Environment.
4. Vercel deploys the frontend from `main`; its environment holds the API URL and `proxy_hmac_secret`.

## Roll back

- Bad image: re-apply with the previous SHA tags (ECR keeps the last 5).
- Bad Terraform change: `git revert`, then apply; or restore the previous version of the state file from the versioned state bucket.
- Bad frontend: Vercel instant rollback.
- Data-plane changes are additive, so an older image reads newer items.
- Destroying an environment: set `allow_destroy_data=true` and apply first (it lifts deletion protection and empties the bucket), then `terraform destroy`.

## Drain the dead-letter queue

An alarm fires when the DLQ holds any message (a job failed 5 deliveries).

1. Read the messages without deleting them: `aws sqs receive-message --queue-url <dlq-url> --max-number-of-messages 10 --visibility-timeout 0`.
2. The sweeper already marks the matching job `failed` with a typed error. Confirm in the job record that it did.
3. Fix the cause (worker log by job id), then ask the user to resubmit, or re-send the message to the jobs queue; re-sending is safe, the job claim is conditional.
4. Only after reading: `aws sqs purge-queue --queue-url <dlq-url>`.

## Stuck job

A job `running` with a stale heartbeat is picked up again by the sweeper. To force it: re-send its message to the jobs queue. A job that is `failed` or `succeeded` ignores duplicate deliveries on purpose.

## Rotate keys

Secrets are SSM SecureString parameters under `/unfold/<env>/`: `gemini_api_key_1..4`, `groq_api_key_1..4`, `github_token`, `proxy_hmac_secret`. Terraform does not manage them.

1. `aws ssm put-parameter --name /unfold/<env>/gemini_api_key_1 --type SecureString --overwrite --value <new>`.
2. The API Lambda reads them at cold start; force it with a no-op config change (`aws lambda update-function-configuration --function-name <api> --description rotated-<date>`). Fargate tasks read them at start, so the next job uses the new value.
3. A revoked key shows as `PROVIDER_NOT_CONFIGURED` and a disabled-key metric, never as made-up output. Rotate one key at a time; the pool serves from the rest.
4. `proxy_hmac_secret` must change in SSM and in the Vercel environment together, then redeploy the frontend. Sessions signed with the old value stop validating, which is intended.

## Idle check

No running ECS tasks, no NAT gateway, no load balancer, no provisioned Lambda concurrency. Cost Explorer after 24 to 48 hours against `cost.md`.
