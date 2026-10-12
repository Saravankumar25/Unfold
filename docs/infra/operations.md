# Unfold on AWS: reliability, performance, scaling, limitations, verification

Status (2026-10-11, after merging phase 3 into this branch): everything below was run **locally** (in-process AWS
emulator `moto`, the compose stores, Docker, offline Terraform). Nothing was deployed, no AWS API was called, and no number here comes from AWS. Items that need a real
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
| Expired / deleted project | `expires_at` and the `deleting_at` marker are compared on every read (hidden at once); deletion is S3 prefix + progress + usage counters + the project partition (jobs, chats, messages, exports), idempotent and resumable; a project whose task has a fresh heartbeat is deferred; TTL and the S3 lifecycle are only backstops | e2e test (cleanup twice, counters gone), `test_a_deleting_project_is_cleaned_by_the_sweeper...`, `test_delete_marker_moves_a_project_between_the_sweeper_lists` |
| Export task crashes, is OOM-killed or never starts | `task_stopped` re-sends the export (5 s for exit 75, 15 s otherwise) until the artifact is 15 minutes old, then marks it `failed` with a message; the client's poll ends | `test_a_crashed_export_is_retried_then_failed_when_the_window_closes` |
| Export message lost or dropped for capacity | The sweeper re-sends pending exports older than 5 minutes that have no running task; one export task at a time | `test_sweeper_resends_stale_exports_and_expires_old_ones`, `test_export_tasks_do_not_take_analysis_slots...` |
| Export requested where no exporter task exists | The dispatcher fails the artifact with "not available on this deployment" instead of retrying | `test_export_without_an_exporter_task_is_failed_not_retried` |
| Two regenerations of one project | `add_regeneration` returns the running job (one per project, a marker item released by the finishing transaction); caps count it like an analysis | contract `test_regeneration_is_exclusive_under_concurrency` (both adapters) |
| Chat turn writes concurrently | seq comes from the chat record's version-checked `message_count` (DynamoDB) or the row lock (Postgres): contiguous, no duplicates | contract `test_chat_turns_get_distinct_contiguous_seq_under_concurrency` |

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
| `import unfold.api.aws` in the `api-lambda` dependency set (phase 5 re-measure) | 7.3 to 8.0 s on a loaded Windows laptop, 1 049 modules, process start to `/health` 12.4 to 13.6 s; the same machine took 5.4 to 6.6 s on the development venv, so this is an upper bound dominated by file system and load (`fastapi` alone 2.1 s). Lambda on Linux must be measured on AWS | `performance.md` section 6.3 |
| (earlier) `import unfold.api.main` | 1.10 to 1.37 s cumulative over three runs, 835 modules (bm25s and numpy are loaded; no store client, no onnxruntime, no networkx at import) | interpreter + imports, no store client, no ML runtime. The phase 4 figure before the phase 3 merge was 1.61 s and 677 modules; chat retrieval (bm25s, numpy) is now imported by the API process. Before the import hygiene work the imports alone were 5.6 to 14 s |
| API request, emulated stores | see table below | handler + adapter overhead on moto; **not** a Lambda or network figure |
| `api-lambda` image | see section 3 | |
| 33-file fixture (102 chunks) through the whole 21-stage pipeline, AWS profile on moto, 4 pinned CPUs, scripted model | 71 to 115 s (79 s and 71 s with `MODEL_THREADS=2` / `4`), peak RSS 1.1 to 1.4 GB; research (retrieval) is 60 % of it and the cross-encoder rerank is 85 to 95 % of each retrieval (p50 5 s tuned, 8 to 9 s at the default 6 threads) | measured in phase 5, `performance.md` sections 2 to 5. A real-model run adds 2 to 4 min of model latency. The earlier "12 s" was the pre-investigation pipeline |
| 2 464-chunk input (this repository's `backend/src`) | 1 599 s on a loaded laptop (embedding 1 196 s), peak RSS 2.7 GB | `performance.md` section 2; the 1 800 s job timeout is marginal at this size |
| Chat first token, scripted / real Gemini, API pinned to 2 CPUs | 4.0 s p50 (p95 5.4) / 6.6 s (n = 3), retrieval 3.2 to 3.9 s | `performance.md` section 6.2; the first chat after a cold start adds about 10 s of model loads |

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
| `api-lambda` | 991 MB | 312 MB | Debian + Python 154 MB, site-packages 350 MB (onnxruntime 68, numpy 70, botocore 31, grpc 19), models 152 MB. Measured before the phase 3 merge; networkx (pure Python, a few MB) moved into the `ai` group because chat retrieval builds the in-memory graph in this function, so the figure is a slight under-count |
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

Estimated from payload sizes: about 350 write units and 190 read units (`cost.md` section 2). This table is the phase 4 pipeline with its API reads and cleanup. **Phase 5 re-count of the current 21-stage pipeline** (analysis only, client-side count, `performance.md` section 7): 262 to 268 requests, about 600 WRU and 265 RRU for the 33-file fixture; 403 requests, 665 WRU and 313 RRU for 2 464 chunks; one status poll is 5 requests and 7 RRU, one chat turn 16.5 requests, 15 WRU and 13 RRU. The number of requests
does not grow with repository size except through the number of guide sections, diagrams and progress events
(chunks are one S3 object).

## 5. Scaling behaviour

| Mode | Behaviour |
|---|---|
| Personal | No idle compute. First request after idle pays a Lambda cold start (image pull + imports; imports measured locally at 5 to 8 s on a loaded Windows laptop, the Lambda figure **requires AWS**). A job pays Fargate start + image pull (requires AWS), then about 70 to 115 s of local work plus model latency for a 100-chunk repository (`performance.md`). |
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

Run locally on this branch after the phase 3 merge (2026-10-11); exit codes and counts are in the report that
accompanies the merge, summarised here:

* backend unit, security, architecture and AWS-emulator tests (444 passed), `ruff` check and format, `mypy`
  (strict), `alembic check` on a migrated scratch database;
* the integration suite on the compose stores (136 tests; 135 passed in the full run on isolated scratch stores, and the wall-clock ratio test `test_retrieval_rerank_keeps_scores_and_is_not_slower` failed once under load from other processes (the same inputs took 3.3 to 14 s) and passed on two isolated re-runs), including the contract suites that run the same
  assertions against the DynamoDB and the Postgres `ControlStore` (sessions, projects, jobs, caps, checkpoints, guide
  content, **chats, messages, exports, the delete marker, typed jobs and single regeneration**);
* the AWS profile end to end over moto (upload ticket -> SQS -> dispatcher -> worker run -> API reads -> retry and
  resume -> duplicate delivery -> cleanup) **and**, new, chat (JSON and NDJSON), Markdown export through the export
  task path with a presigned redirect, single-chapter regeneration as an `analysis` task, and cleanup of chats,
  messages, exports and usage counters (`test_chat_export_and_regeneration_run_on_the_aws_profile`);
* an architecture test that nothing under `unfold.api` can reach the pipeline or its stages, and a dependency test
  that every package the API process can import is in the image groups (it found networkx);
* the progress stream of all 21 stages through the DynamoDB `ProgressStore`;
* `terraform fmt -check`, `validate` of `bootstrap`, `envs/dev`, `envs/prod`, and the 26 policy tests with negative
  fixtures;
* frontend `lint`, `typecheck`, `test` (55 tests, including the HMAC signature vectors shared with the backend), `build`.

Quality gate on the embedded backends. The offline eval gate (`python -m unfold.evaluation.offline --gate`) is
store-independent by construction (in-memory chunks and blobs, no dense, no graph): exit 0, every metric inside `evals/baseline.json` (claim grade accuracy 1.0, hallucination verified rate 0.0, retrieval hit rate at 8 1.0 and MRR 0.78, graph relation recall 1.0, injection scanner recall 1.0 with a false-positive rate of 0.17 under its ceiling). The rubric on a real run
of the pipeline on this profile (moto DynamoDB and S3, embedded dense artifact, in-memory graph, counter-store LLM
coordination, real Gemini through the real gateway), `fastapi_rag`, scored by `unfold.evaluation.rubric` against
`evals/baselines/pipeline-v2`: two runs (`gemini-3.5-flash-lite`, 19 and 20 model calls, 41.1k / 11.8k and 42.6k / 12.3k tokens in / out, one attempt each with two provider rate-limit waits, 287 s and 400 s of wall clock on a loaded laptop, which is not comparable to the 118 s of the local-stack run). Scores: coverage 4.0 / 4.0, groundedness 2.92 / 2.42, citation accuracy 4.0 / 4.0 (resolve rate 1.0), depth proxy 2.67 / 2.83, specificity 1.33 / 1.46, redundancy 3.88 / 3.93, uncertainty honesty 4.0 / 4.0; unsupported, uncited and unverified-fact counts 0. Against the v1 baseline of the gate no dimension is more than 0.3 lower (redundancy -0.12 / -0.07). Against the v2 primary run the worst gap is specificity (-0.27 / -0.14); against the v2 replicate specificity is -0.52 / -0.39 and depth proxy -0.33 / -0.17, while the two v2 runs themselves differ by 0.25 on specificity. Two runs cannot separate a backend effect from model variance: read it as no regression shown, not none. Artifacts and method: `evals/baselines/pipeline-v2-aws/` (`scripts/aws_profile_guide_run.py`); human review sheets are not filled. The retrieval swaps themselves are proven by the differential tests against Qdrant and Neo4j (`test_retrieval_backend_parity`).

**Measured locally in phase 5 (2026-10-12, laptop, `performance.md`):** worker wall time per stage, peak RSS (1.1 to 1.4 GB for 100 chunks, 2.7 GB for 2 464), CPU seconds, model calls, tokens and seconds per role and chapter (real Gemini on `cli_tool`: 13 calls, 17.3k / 9.3k tokens), retrieval p50/p95 (5 s tuned, 8 to 9 s at default threads; rerank is 85 to 95 %), flow trace (under 5 ms), API non-LLM endpoints p50/p95 (n = 100), chat first token (4.0 s scripted, 6.6 s real Gemini), the API cold-start proxy and the DynamoDB request profile of the current pipeline. Findings: `MODEL_THREADS=4` on the worker (1.5x faster, default 6 oversubscribes 4 vCPU), `MODEL_THREADS=2` on the API, the 1 800 s job timeout is marginal for about 2 500 chunks (cap the chunks or raise the timeout), 4 vCPU / 8 GB stays. These replace the earlier statements that the engine profile and chat latency were not measured.

**Requires a real AWS account (not run, not claimed):** Function URL + Web Adapter response streaming end to end
through Vercel (chat first token, full answer); Lambda cold start p50/p95/p99 of the API image (it now also imports
the retrieval stack); Fargate image pull and task start latency for the `analysis` and the 3.28 GB `export` image,
arm64 viability, the actual `RunTask` quota (6 vCPU?), Fargate memory and wall clock of the longer 21-stage pipeline
(the 4 vCPU / 8 GB size is confirmed locally by the phase 5 profile, `performance.md` section 3; only the Fargate speed, `memory.peak` and `os.cpu_count()` need AWS); Lambda reserved concurrency feasibility in a new
account; `clientToken` / `startedBy` behaviour (including the `export-` prefix) and the STOPPED event payload for OOM
and spot; `GetParametersByPath` IAM resource evaluation; SSM loader and ECS `secrets` injection; the browser POST to
S3 with real CORS and CSP and the browser following the 302 to a presigned download; IAM least-privilege completeness
(any missing action fails at run time, not at plan time); EventBridge and Scheduler delivery; budget alarm delivery;
DynamoDB TTL behaviour (never relied on); 24 h / 7 d recovery; Cost Explorer vs model.

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
  its next heartbeat can leave a few items under the deleted partition (not found by the sweeper). Every item of a
  project partition and its `J#`/`H#`/`A#` pointers carry the project's `ttl`, so DynamoDB removes such leftovers
  with the project record even when the sweeper was down past the expiry (TTL deletion is itself delayed, typically
  by hours to two days, and reads never rely on it).
* Chat sessions, messages, export artifacts, the delete marker and typed jobs are behind `ControlStore` on both
  adapters. DynamoDB's export idempotency is weaker than Postgres: two simultaneous identical export requests (a
  double click) can both create a pending export, because the reuse check and the insert are not one atomic step
  (both valid; the per-project hourly limiter and the per-session export limit bound the cost).
  `stale_pending_artifacts` reads the not-yet-expired part of the `ARTIFACTS` index (bounded by the 24 h lifetime and
  the limits) and filters it; a pending-only index would be needed for a very large export backlog.
* The exporter image is the heaviest artifact (3.28 GB uncompressed, Chromium with its system packages plus the
  worker's analysis packages). It is pulled only by the `export` task, but each cold task pays that pull. Shrinking it
  (a headless-shell-only Chromium, a venv without the analysis group, since the exporter reads guides and does not
  parse repositories) is the first reduction and is not done; Fargate pull time is unmeasured (requires AWS).
* Rate-limit windows are part of a counter's key, so DynamoDB cannot delete "by prefix". Project and session cleanup
  delete the owner's counters by exact key for the current and the previous window (60 s and 3600 s windows, the
  ones the routes use); a counter of an older window is not touched and expires by its own `ttl` (at most
  two windows plus an hour).
* The per-job LLM usage cap on DynamoDB (`CounterUsageStore.admit`) is a read of the counters followed by a
  conditional add, not one atomic step: only the hard call cap is exact. Concurrent calls can all pass the soft
  ceiling and token checks together and overshoot the soft ceiling, by at most the number of concurrent callers and
  never past the hard cap. When several limits are reached at once the reason differs from the Redis script:
  `tokens` is reported before `hard` (Lua reported `hard` first).
* The investigation engine runs on these ports unchanged. Its memory, wall clock, calls and tokens are measured locally
  (section 7, `performance.md`); only the Fargate speed and `memory.peak` are not, so the task size stays 4 vCPU / 8 GB
  and `JOB_TIMEOUT_SECONDS` 1 800 until a Fargate run (phase 6), with the chunk-cap caveat in `performance.md` section 2.
* Chat runs in the API function with the in-memory graph and the dense artifact loaded per instance from S3: first
  turn after a cold start pays the artifact loads and the model loads (about 10 s locally, `performance.md` section 6.2; the Lambda figure requires AWS). `stream=true` through the Function URL and Vercel is an
  acceptance test that requires AWS; `stream=false` always works.
* One region per environment; no cross-region failover. Single AZ failure is absorbed by the second public subnet.
* The tree-sitter 0.26 memory corruption is avoided by the pin `<0.26`; Linux behaviour of 0.26 was not measured.
