# ADR 0011: SQS + dispatcher Lambda + ECS RunTask, reconciled by a sweeper; no Step Functions

Status: proposed (2026-10-10) · Phase 4 stage B · Supersedes the Step Functions assumption in `docs/adr/0002`.

## Context
The job row is the durable queue (`analysis_jobs`); the local supervisor (`worker/queue.py:57-88`) owns retries,
hard kill, exit-code policy and "gave up" recording. On AWS those duties need an owner. Requirements
(strategy §5.3): no lost jobs between commit and dispatch, idempotency, bounded retries with backoff and DLQ, bounded
concurrency, no always-on service.

## Decision
- API commits project + job (`queued`) then sends an SQS message `{type, ref, attempt}`.
- SQS standard queue (visibility 60 s, retention 14 d, redrive `maxReceiveCount` 5 to a DLQ) -> dispatcher Lambda
  (event source mapping, batch 1, maximum concurrency 2). The dispatcher counts running tasks with `ecs:ListTasks`,
  raises when at capacity (message returns), otherwise `ecs:RunTask` with `clientToken=job_id#attempt`.
  `cleanup` messages run inline (no model needed).
- Claim stays a conditional update on the job item (ADR 0012); duplicates are harmless.
- EventBridge rule on ECS Task State Change (STOPPED) -> `task_stopped` handler implementing the exit-code policy
  of `SubprocessJobQueue._supervise` (0 done, 75 retry soon, other codes retry after the heartbeat guard, then
  `fail_abandoned_job`).
- EventBridge Scheduler `rate(15 minutes)` -> sweeper: outbox reconciliation (queued > 10 min), stale running jobs,
  over-age tasks `StopTask`, expiry cleanup, counter healing. A DLQ alarm covers the rest.

## Alternatives considered
- Step Functions Standard `.sync`: does not close the commit-to-start gap, does not bound concurrency, needs
  EventBridge managed rules, and new accounts have reduced transition quotas (number not documented). Per-job cost
  ($0.0001-0.0003) is not the issue; necessity is. Not justified.
- Lambda async invoke as the queue: 6-hour event age, two retries, no long deferral. Rejected.
- DynamoDB Streams as outbox: closes the gap without a sweeper but adds a stream consumer and still needs capacity
  deferral; the sweeper is needed anyway for expiry. Possible later.

## Consequences
- Idle cost: roughly 0.65 M SQS polling requests per month (derived), inside the 1 M free tier.
- Fargate vCPU default quota is 6: one 4-vCPU task at a time until raised.
- Emulators can test the contracts; real RunTask and EventBridge behaviour requires AWS.

## Implementation notes (phase 4, stage C-E)
- A message that finds no free slot is **dropped**, not retried: waiting by redelivery burns the 5 receives that lead
  to the DLQ. The stopped-task event hands the freed slot to the oldest waiting jobs; the sweeper is the backstop.
- Idempotency: `startedBy=<job id>` prevents a second task for a job; the `RunTask` client token is the SQS message
  id (a redelivery reuses it, a new send does not). The claim remains the real exclusion.
- Two dispatcher invocations racing on the same job can still start two tasks; the second exits on the claim.
- Control handlers run from the worker image via the Lambda runtime interface client (`awslambdaric`, entry point
  override in the `control` module).
