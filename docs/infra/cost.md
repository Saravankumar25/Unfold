# Unfold on AWS: monthly cost, five scenarios

**These are estimates, never actuals.** Nothing was deployed. Prices are the AWS Price List figures retrieved
2026-10-09/10 and recorded in `architecture-decision.md` section 3 and 4 (us-east-1 unless stated; credits and the
12-month free tier excluded; the always-free allowances are included). The request counts below are **measured**
locally (section 2); everything else is an assumption written next to its number. Phase 5 re-checks this against
measured tokens, Fargate seconds and request counts (`six-phase-plan.md`).

## 1. Assumptions

| Item | Value | Basis |
|---|---|---|
| Analysis task | 4 vCPU / 8 GB x86, **8 min** per analysis | laptop run of a 2 424-chunk repo took 7.7 min; Fargate unmeasured, benchmark plan in `architecture-decision.md` 4.10 |
| Fargate price | $0.04048 per vCPU-hour, $0.004445 per GB-hour, per second, 1 min minimum | AWS Fargate pricing |
| Public IPv4 | $0.005 per hour while a task runs | VPC pricing |
| Chat turn | 10 s at 3 GB on the API Lambda | assumption; chat now runs in the API function. With a scripted model a local turn took 1.0 to 1.3 s of retrieval and answer assembly; the provider's first token and the Lambda figure are unmeasured (require AWS) |
| Export task | 2 vCPU / 4 GB x86, **90 s** per export (image pull, Chromium start, render, upload) | assumption; the task definition sizes are `export_cpu`/`export_memory`, the time is unmeasured on Fargate |
| Regeneration | 4 vCPU / 8 GB analysis task, **3 min** per chapter (start, research, write, verify, revise one chapter) | assumption derived from the 8 min whole-guide figure and 6 to 10 chapters; unmeasured on Fargate |
| Light request (poll, read) | 0.25 s at 2 GB, 3 DynamoDB reads, no S3 | measured shape: a status poll is 2 to 4 reads, no other service |
| DynamoDB per analysis | **258 requests, about 350 WRU and 190 RRU** plus polling (about 4 RRU per poll) | measured, section 2 |
| SQS idle polling | about 0.65 M requests per month | five long pollers, inside the 1 M free tier |
| S3 | 30 MB resident per project, 3-day expiry | measured artifact sizes: IR 18 KB, BM25 24 KB, dense 0.18 MB per 100 chunks, chunks object |
| Logs | 0.5 MB per job, 14-day retention | assumption; 5 GB free |
| ECR | api-lambda 0.31 GB + worker 0.35 GB compressed (proxy), exporter 3.28 GB uncompressed; 5 tags kept per repository | measured sizes, section 3; ECR shares identical layers between tags, so a tag costs only what changed. The $0.30 in the table assumes about 3 GB stored; five fully distinct tags of all three images would be roughly $1 to $2 per month |

## 2. Measured request profile (local, `UNFOLD_DDB_PROFILE_OUT`)

One analysis of the 33-file fixture, two attempts (one retryable failure after the plan, resumed), the API reads
that follow, and cleanup, through the real adapters on an emulator: 64 `UpdateItem`, 30 `PutItem`, 118 `GetItem`,
31 `Query`, 6 transactions, 7 batch writes, 2 deletes. Capacity units are estimated from payload size (writes 1 WRU
per KB, reads 0.5 RRU per 4 KB eventual / 1 consistent), about 350 WRU and 190 RRU. At $0.625 per million WRU and
$0.125 per million RRU that is **$0.0002 per analysis**: DynamoDB is not a cost driver at any scenario below. A
production repository with more sections and events costs a small multiple of this.

## 3. Scenarios (us-east-1, USD per month, LLM excluded)

Counts per month: **zero** 0 jobs; **occasional** 10 jobs, 100 chat turns, 3 000 light requests; **demo** 15 jobs,
200 chats, 5 000 light, 2 hours of one provisioned warm Lambda; **moderate** 300 jobs, 3 000 chats, 100 000 light;
**spike** 660 jobs, 20 000 chats, 500 000 light (300 plus one day at the 2-concurrent cap).

| Scenario | Compute (Fargate + Lambda) | Storage (S3, ECR) | DB I/O (DynamoDB) | API requests | Orchestration (SQS, EventBridge) | Networking (public IPv4) | Observability | **Total** | Always-on `t4g.large` |
|---|---|---|---|---|---|---|---|---|---|
| Zero month | 0.00 | 0.30 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | **0.30** | 55.11 |
| Occasional | 0.26 | 0.30 | 0.01 | 0.00 | 0.00 | 0.01 | 0.00 | **0.58** | 55.11 |
| Interview demo | 0.46 | 0.31 | 0.01 | 0.00 | 0.00 | 0.01 | 0.00 | **0.78** | 55.11 |
| Moderate | 7.90 | 0.39 | 0.26 | 0.00 | 0.00 | 0.20 | 0.00 | **8.75** | 55.11 |
| Spike | 24.88 | 0.52 | 0.89 | 0.00 | 0.00 | 0.44 | 0.00 | **26.73** | 55.11 |

"Zero" is the only month with no workload; its $0.30 is ECR storage. There is no idle compute, database, NAT, load
balancer, endpoint or secret-store charge. Budgets alert at 50/80/100 % of $10 per month and $3 per day (variables);
they are alerts, not caps; the caps are the quotas in `deployment.md` section 6. Mumbai (`ap-south-1`) is within 5 %
of these totals for the serverless design (`architecture-decision.md` 3.1) and cheaper for the EC2 comparison.

### Added by the phase 3 features (export and regeneration), derived

The five scenarios above were computed before exports and regeneration existed on this profile. Their Fargate cost
is `seconds x (vCPU x $0.04048/3600 + GB x $0.004445/3600 + $0.005/3600)`: one export task about **$0.0026**, one
regeneration about **$0.0101** (assumptions in section 1). The counts per scenario are assumptions.

| Scenario | Exports | Regenerations | Added USD per month |
|---|---|---|---|
| Zero month | 0 | 0 | 0.00 |
| Occasional | 3 | 2 | 0.03 |
| Interview demo | 5 | 3 | 0.04 |
| Moderate | 90 | 60 | 0.84 |
| Spike | 200 | 150 | 2.04 |

Neither changes a conclusion: Fargate seconds of full analyses remain the driver. The exporter image's ECR storage is
already inside the storage column (section 1 assumes about 3 GB of images).

## 4. External model spend (a parameter, not an estimate)

The engine's LLM spend is **not** in the table: it is a parameter, because paid Gemini prices were not retrieved
(UNVERIFIED) and the keys used for the runs are free tier. What exists now is the measured token and call profile of
the engine (`evals/baselines/pipeline-v2`, `docs/design/quality-report.md`): per guide, 19 and 13 model calls and
43 018 / 11 140 and 34 716 / 6 530 input / output tokens on the two fixture repositories (fastapi_rag, requests),
2.5x and 1.2x the tokens of the previous pipeline. A run of the same pipeline on this AWS profile is recorded in
`operations.md` section 7.

    llm_usd = jobs x (input_tokens_per_guide x P_in + output_tokens_per_guide x P_out) / 1e6

* `P_in`, `P_out`: dollars per million tokens of the configured models (`gemini_model_*`). Unknown here.
* Hard ceilings that exist in code and Terraform: **1.5 M tokens and 195 calls per job** (`ANALYSIS_LLM_*`), so
  `llm_usd <= jobs x 1.5 x (P_in + P_out) / 2` for any realistic split; chat is bounded by 200 messages per session
  per hour and 2 000 per day globally.
* On the free tier the cost is $0 but the quota binds first: 1 000 requests per day on `gemini-3.5-flash-lite`
  (about 170 to 200 analyses per day at 5 to 6 calls each, derived). The spike scenario is quota-bound before it is
  money-bound.

Example for orientation only (an assumption, not a quote): at a blended $1 per million tokens and the hard ceiling,
a job costs at most $1.50, 10 jobs $15, 300 jobs $450. The real number replaces this once the profile is measured.

## 5. What drives cost, and the levers

| Driver | Lever |
|---|---|
| Fargate seconds (about 90 % of the moderate bill) | task size from the benchmark matrix, chunk cap (5 000), arm64 once wheels are verified (-18 % in the model), Spot (documented, off by default) |
| LLM tokens | per-job token and call caps, model tiering, quota windows |
| Public IPv4 per task | none needed beyond task lifetime; NAT would cost $32.85 per month fixed |
| ECR | lifecycle keeps 5 tags; compressed size is lower than the uncompressed figure used |
| Logs | 14-day retention, JSON lines, no debug in production |
