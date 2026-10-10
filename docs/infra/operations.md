# Unfold on AWS: reliability, performance, scaling, limitations, verification

Status (2026-10-10): everything below was run **locally** (in-process AWS emulator `moto`, MinIO, Docker, offline
Terraform). Nothing was deployed, no AWS API was called, and no number here comes from AWS. Items that need a real
account are listed in section 7 and are **not** marked passed.

## 1. Reliability and recovery

| Failure | What happens | Where it is tested |
|---|---|---|
| Duplicate SQS delivery | The dispatcher starts no second task for a job that has one (`startedBy`); the claim is a conditional update, so a rare duplicate task exits 0 without work; a finished job is skipped | `test_aws_dispatch` (duplicate, skipped), `test_aws_load` (redelivery storm), contract `test_claim_is_exclusive_under_concurrency` |
| Commit without enqueue (lost job) | Job stays `queued`; the sweeper (15 min) re-sends jobs queued > 10 min and jobs `running` without a heartbeat for 5 min | `test_sweeper_resends_a_job_whose_enqueue_was_lost`, `..._recovers_stale_running_jobs...` |
| Worker crash / OOM / failed to start | `task_stopped` re-sends after the 2 min heartbeat guard; after `MAX_JOB_ATTEMPTS` the job is failed with `ANALYSIS_STAGE_FAILED` and the client sees a `failed` event | `test_crash_is_retried_until_attempts_are_exhausted...` |
| Retryable failure (exit 75: provider capacity, timeout) | Job returns to `queued`, re-sent with backoff `min(30, 5*attempt)` s, resumes at the stage checkpoints (the plan is not paid for twice) | `test_zip_to_guide_duplicate_delivery_resume_and_cleanup` |
| No free slot | The message is dropped without an error; the next stopped task (or the sweeper) re-sends the oldest waiting jobs. Backpressure never burns DLQ receives | `test_capacity_defers_without_error_and_a_stopped_task_resends` |
| Message dead-lettered (5 failed receives) | The sweeper fails the job with a typed error; alarm on DLQ depth | `test_sweeper_fails_jobs_whose_message_was_dead_lettered` |
| Hung task | The worker watchdog exits 75 at `job_timeout + 30 s`; the sweeper `StopTask`s anything older than `job_timeout + 5 min` | `test_sweeper_stops_tasks_past_the_job_deadline` |
| Store outage (DynamoDB, S3, SQS) | Typed `STORE_UNAVAILABLE` / `QUEUE_UNAVAILABLE` (503, retryable); rate limiting fails closed; a failed enqueue leaves the job `queued` for the sweeper | `test_*_outage_*`, `test_sqs_failure_is_a_typed_retryable_error` |
| Capacity failure of `RunTask` (quota, subnet) | Retryable error, message returns to the queue (bounded redrive) | `test_fargate_runner_surfaces_capacity_failures_as_retryable` |
| Lost counter update / drift | Caps live in the same transaction as the job; a cap that looks exceeded is re-counted from the job records before rejecting, and the sweeper heals the global counter | `test_active_job_cap_per_session_and_release`, `test_sweeper_heals_the_cap_counters` |
| Abandoned upload | Project has a 30 min expiry and no job; the sweeper deletes it and its prefix | `test_expired_projects_are_listed_for_the_sweeper`, `test_sweeper_deletes_expired_data_from_every_store` |
| Expired / deleted project | `expires_at` is compared on every read; `DELETE` sets it to now (hidden at once); deletion is S3 prefix + progress + project partition, idempotent and resumable; TTL and the S3 lifecycle are only backstops | e2e test (cleanup twice), sweeper expiry test |

Long idle (24 h / 7 d / 30 d): there is no database to wake and nothing running. Cold starts re-create Lambda
environments; sessions and projects past their lifetime were already deleted by the sweeper (or are rejected on read);
ECR keeps the last 5 tagged images, so the deployed digest never disappears. A rotated or revoked LLM key surfaces as
`PROVIDER_NOT_CONFIGURED` / a disabled-key metric and alarm, not as fabricated output. A `scripts/smoke_aws.py` style
check after a long idle is part of the deploy checklist (requires AWS, section 7).

Recovery procedures: drain the DLQ (the sweeper does it; `aws sqs purge-queue` only after reading the messages);
re-run a stuck job by re-sending its message (idempotent); restore from a bad deploy with the previous image tags
(`deployment.md` section 8). There is no backup to restore: data lives at most three days, PITR is off by design.

## 2. Performance (measured, locally)

Sources: `tests/integration/test_aws_load.py` (latency, `UNFOLD_BENCH_OUT`), `test_aws_profile_e2e.py`
(`UNFOLD_DDB_PROFILE_OUT`), image builds (`docker image ls`), `python -X importtime`. Laptop under heavy memory
pressure (Windows, 16 GB, about 0.5 to 1 GB free, other agents running), so absolute times are pessimistic.

| What | Result | Meaning |
|---|---|---|
| `import unfold.api.main` | 1.61 s cumulative, 677 modules (fastapi 0.56 s) | interpreter + imports, no store client, no ML. Before the import hygiene work the imports alone were 5.6 to 14 s |
| API request, emulated stores | see table below | handler + adapter overhead on moto; **not** a Lambda or network figure |
| `api-lambda` image | see section 3 | |
| 33-file fixture through the whole pipeline on moto + local models | about 12 s of pipeline time, of which about 10 s embedding | CPU-bound locally; Fargate numbers require AWS |

API layer over moto, n = 100 per endpoint (one session, one project):

| Endpoint | p50 ms | p95 ms | p99 ms |
|---|---|---|---|
| `GET /sessions/current` | 108 | 403 | 763 |
| `GET /projects/{id}` | 90 | 354 | 390 |
| `GET /projects/{id}/progress` | 100 | 455 | 868 |
| `POST /sessions/current/touch` | 91 | 457 | 628 |

Moto costs tens of milliseconds per DynamoDB call in this setup, so these are an upper-bound sanity check, useful as
a regression guard (p99 < 2 s asserted) and to show that a status poll touches only DynamoDB: no queue, no task, no S3.
DynamoDB request profile per analysis: section 4.

## 3. Image sizes (measured with `docker image ls`, uncompressed)

| Target | Uncompressed (`docker image ls`) | `docker save` gzip -1 (proxy for ECR compressed size) | Contents that dominate |
|---|---|---|---|
| `api-lambda` | 991 MB | 312 MB | Debian + Python 154 MB, site-packages 350 MB (onnxruntime 68, numpy 70, botocore 31, grpc 19), models 152 MB |
| `worker` | 1.12 GB | 346 MB | adds tree-sitter grammars and the analysis packages |
| `exporter` | 3.28 GB | not measured | worker + superset venv (552 MB) + Chromium with system packages (1 GB layer); the heaviest image, used only by the PDF export task. A headless-shell-only install would be the first reduction |

Measured once on a Windows laptop with Docker Desktop; layer content is the same on Linux. The compressed number
is a `docker save | gzip -1` size, not an ECR figure. Both images were started: `api-lambda` served `/health` in
4.6 s from `docker run` (container create + uvicorn + app start, 76 MiB RSS, none of the local-stack, tree-sitter
or fastembed modules imported at start); the worker image imported all three control handlers under
`awslambdaric` with no local-stack module loaded. Lambda cold start (image pull + extension + imports) and Fargate
image pull time are **unmeasured** (require AWS). Pinned base digests: Python `3.12-slim-bookworm`, uv 0.12.10, Lambda
Web Adapter 1.1.0.

## 4. DynamoDB request profile (measured counts, estimated units)

One analysis of the 33-file fixture (102 chunks), including a failed first attempt that resumed from checkpoints,
the API reads of the finished guide and the cleanup, through the real adapters:

| Operation | Requests |
|---|---|
| `GetItem` | 118 |
| `UpdateItem` | 64 |
| `Query` | 31 |
| `PutItem` | 30 |
| `BatchWriteItem` | 7 |
| `TransactWriteItems` | 6 |
| `DeleteItem` | 2 |
| **Total** | **258** |

Estimated from payload sizes: about 350 write units and 190 read units (`cost.md` section 2). The number of requests
does not grow with repository size except through the number of guide sections, diagrams and progress events
(chunks are one S3 object).

## 5. Scaling behaviour

| Mode | Behaviour |
|---|---|
| Personal | No idle compute. First request after idle pays a Lambda cold start (image pull + imports; **unmeasured on AWS**). A job pays Fargate start + image pull (unmeasured). |
| Interview demo | Optional deliberate warm-up: one request to `/health` before the demo wakes the API Lambda; the worker image is pulled on the first task. Status is explicit (`queued`, stage labels), failures are typed. |
| Growth / public share | Bounded everywhere: 2 active jobs per session, 20 queued, 1 concurrent task (2 after the quota raise), API reserved concurrency 5, per-IP and global creation windows. Overload returns 429 with a retry hint, never silent drops. Raising a limit is a Terraform variable. |

Chat capacity is separate from analysis capacity (different compute, different counters). One 4 vCPU task is the
current ceiling because the default Fargate On-Demand quota is 6 vCPU (the number is documented, the account's actual
quota is not known).

## 6. Smoke checklist after a first deploy (requires AWS)

1. `GET /health`, `GET /ready` through the Vercel proxy (all checks `ok`).
2. Create session, submit a small public repo, watch `queued` -> stages -> `succeeded`; read guide and a diagram.
3. Upload a ZIP from the browser (CORS and CSP of the S3 POST), complete, analyse.
4. Idle check: no running tasks, no NAT, no load balancer, Lambda concurrency not provisioned.
5. Kill a task (`aws ecs stop-task`): job retries and completes. Send a bad message: it reaches the DLQ and the job fails.
6. Delete a project and expire a session: S3 prefix, items and events are gone.
7. Cost Explorer after 24 to 48 h against `cost.md`.

## 7. Verification status

Run locally and passing (2026-10-10, `main` gate pieces on this branch):

* backend unit, security and AWS-emulator tests, `ruff`, `mypy` (strict), architecture tests (only adapters import
  `boto3`/`botocore`; no local-store package is imported by the AWS profile or the API image);
* integration suite on the compose stores (Postgres, Redis, Neo4j, Qdrant, MinIO) including the parity tests and the
  contract suites that run the same assertions against the local and the AWS adapter;
* AWS-profile end to end (upload ticket, SQS, dispatcher, worker run, API reads, retry/resume, duplicate delivery,
  cleanup) with the real pipeline and retrieval; presigned POST policy against a real S3-compatible server (MinIO);
* `terraform fmt`, `validate` of all roots and modules, offline `plan` of bootstrap/dev/prod, 24 policy tests with
  negative fixtures;
* frontend: signature vectors shared with the backend signer, CSP test.

**Requires a real AWS account (not run, not claimed):** Function URL + Web Adapter response streaming end to end through
Vercel (chat first token, full answer); Lambda cold start p50/p95/p99 of the API image; Fargate image pull and task
start latency, arm64 viability, actual `RunTask` quota (6 vCPU?); Lambda reserved concurrency feasibility in a new
account; `clientToken` / `startedBy` behaviour and the STOPPED event payload for OOM and spot; `GetParametersByPath`
IAM resource evaluation; SSM loader and ECS `secrets` injection; browser POST to S3 with real CORS and CSP; IAM
least-privilege completeness (any missing action fails at run time, not at plan time); EventBridge and Scheduler
delivery; budget alarm delivery; DynamoDB TTL behaviour (never relied on); 24 h / 7 d recovery; Cost Explorer vs model.
The retrieval-backend swap is proven by differential tests against Qdrant and Neo4j; the rubric gate against the v2
baseline needs the phase 3 engine.

## 8. Known limitations

* No NAT and no egress filtering: tasks can reach any host on 443 (they never execute repository code; Network
  Firewall was rejected on cost). Public IPv4 per running task.
* Function URL has no edge protection (no WAF, no usage plans): reserved concurrency, application quotas and the
  HMAC identity are the controls. A caller that bypasses Vercel shares one `unverified` rate-limit bucket.
* Provider quota windows are fixed (approximate), not sliding; concurrency slots, cooldowns and the circuit breaker
  are per process, so two chat instances can each use their own slots.
* The response cache and embedding cache of the Redis stack are not part of the AWS profile.
* DynamoDB item limit: records are guarded at 300 KB; chunks live in one S3 object per project (5 000 chunks cap).
* Two dispatcher invocations racing on the same job can start two tasks; the claim makes the second exit at once.
  The concurrency cap is read (`ListTasks`) and then used (`RunTask`) without a lock, so two invocations racing on
  different jobs can exceed it by at most `dispatcher_max_concurrency - 1`; the Fargate vCPU quota is the hard stop.
* Deleting a project whose task is still running removes everything at that moment; a write the task makes before
  its next heartbeat can leave a few items under the deleted partition (no `ttl`, not found by the sweeper).
* Chat sessions, messages and export artifacts (phase 3) are not yet behind `ControlStore`; they need item types in
  the same table when phase 3 merges. The investigation engine (phase 3.x) runs on these ports unchanged but its
  worker size, timeout and token budget must be set from its measured profile.
* One region per environment; no cross-region failover. Single AZ failure is absorbed by the second public subnet.
* The tree-sitter 0.26 memory corruption is avoided by the pin `<0.26`; Linux behaviour of 0.26 was not measured.
