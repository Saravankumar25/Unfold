# Unfold on AWS: monthly cost, five scenarios

**These are estimates, never actuals.** Nothing was deployed. Prices are the AWS Price List figures retrieved
2026-10-09/10 and recorded in `architecture-decision.md` section 3 and 4 (us-east-1 unless stated; credits and the
12-month free tier excluded; the always-free allowances are included). Phase 5 re-checked the model against measured
token counts, request profiles, chat latency and worker seconds (`performance.md`, measured on a laptop, 2026-10-12).
What a laptop cannot give (Fargate speed, Lambda cold start) is an explicit assumption below and is re-measured in
phase 6 from Cost Explorer.

## 1. Assumptions

| Item | Value | Basis |
|---|---|---|
| Analysis task | 4 vCPU / 8 GB x86 (confirmed by the profile, `performance.md` section 3), **9 min blended** per analysis (540 s) | measured on the laptop with `MODEL_THREADS=4`: a 100-chunk repository takes 70 to 80 s of local work plus 2 to 4 min of model latency, a 2 500-chunk one about 15 min (embedding 1 200 s on a loaded machine, 400 s quiet). Fargate is assumed **1.5x slower than the laptop thread** (UNVERIFIED), so 80 % small at 6 min plus 20 % large at 22 min is 9.2 min. The earlier 8 min figure came from one unloaded laptop run; this one is derived from several inputs |
| Fargate price | $0.04048 per vCPU-hour, $0.004445 per GB-hour, per second, 1 min minimum | AWS Fargate pricing; one task-hour is $0.2025 with the public IPv4 |
| Public IPv4 | $0.005 per hour while a task runs | VPC pricing |
| Chat turn | 10.2 s at 3 538 MB (35.2 GB-s) on the API Lambda | measured shape: retrieval 3.2 to 3.9 s (rerank on 2 CPUs), first token 6.6 s, full answer 10.2 s with real Gemini; Lambda speed unmeasured |
| Export task | 2 vCPU / 4 GB x86, **90 s** per export | assumption; unmeasured on Fargate (needs AWS) |
| Regeneration | 4 vCPU / 8 GB analysis task, **3 min** per chapter | the measured chapter (research, write, verify, revise) is 35 to 60 s of local time plus task and model load; with the 1.5x factor and the 1-minute minimum 3 min stands |
| Light request (poll, read) | 0.25 s at 2 GB | measured: status poll 150 ms, project read 60 ms, guide read 450 ms on emulated stores |
| DynamoDB per analysis | **about 600 WRU and 1 945 RRU** (265 RRU for the pipeline plus 240 polls of 7 RRU) | measured counts (`performance.md` section 7), units estimated from payload size |
| DynamoDB per chat turn | 15 WRU and 13 RRU | measured, 16.5 requests per turn |
| DynamoDB per light request | 0.3 WRU and 10 RRU | measured mix of reads (a guide read is 27 RRU, a section 11, a status read 3) |
| SQS idle polling | about 0.65 M requests per month | five long pollers, inside the 1 M free tier |
| S3 | 30 MB resident per project, 3-day expiry | measured artifact sizes: IR 18 KB, BM25 24 KB, dense 0.18 MB per 100 chunks, chunks object |
| Logs | 0.5 MB per job, 14-day retention | assumption; 5 GB free |
| ECR | api-lambda 0.31 GB + worker 0.35 GB compressed (proxy), exporter 3.28 GB uncompressed; 5 tags kept per repository | measured sizes (`operations.md` section 3); the $0.30 in the table assumes about 3 GB stored |

## 2. Measured request profile

One analysis of the 33-file fixture through the real adapters on an emulator: 262 to 268 requests (about 90 `UpdateItem`,
125 `GetItem`, 31 `PutItem`, 12 `Query`, 3 transactions, 3 batch writes), about 600 WRU and 265 RRU; the 2 464-chunk
input needed 403 requests, 665 WRU and 313 RRU. At $0.625 per million WRU and $0.125 per million RRU one analysis is
**$0.0004 for the pipeline and $0.0002 for its polling**. DynamoDB stays under 4 % of every scenario.

## 3. Scenarios (us-east-1, USD per month, LLM excluded)

Counts per month: **zero** 0 jobs; **occasional** 10 jobs, 100 chat turns, 3 000 light requests; **demo** 15 jobs,
200 chats, 5 000 light, 2 hours of one provisioned warm Lambda; **moderate** 300 jobs, 3 000 chats, 100 000 light;
**spike** 660 jobs, 20 000 chats, 500 000 light. Exports and regenerations are now inside compute: 3 and 2, 5 and 3,
90 and 60, 200 and 150. One 9-minute analysis is **$0.0296 of Fargate plus $0.00075 of IPv4**.

| Scenario | Compute (Fargate + Lambda) | Storage (S3, ECR) | DB I/O (DynamoDB) | Networking (public IPv4) | Orchestration, API requests, observability | **Total** | Previous total | Always-on `t4g.large` |
|---|---|---|---|---|---|---|---|---|
| Zero month | 0.00 | 0.30 | 0.00 | 0.00 | 0.00 | **0.30** | 0.30 | 55.11 |
| Occasional | 0.32 | 0.30 | 0.01 | 0.01 | 0.00 | **0.64** | 0.61 | 55.11 |
| Interview demo | 0.55 | 0.31 | 0.02 | 0.01 | 0.00 | **0.89** | 0.82 | 55.11 |
| Moderate | 9.70 | 0.39 | 0.37 | 0.25 | 0.00 | **10.71** | 9.59 | 55.11 |
| Spike | 30.77 | 0.52 | 1.35 | 0.56 | 0.00 | **33.20** | 28.77 | 55.11 |

"Previous total" is the phase 4 table plus its separate export and regeneration addition (0.03, 0.04, 0.84, 2.04).
The new totals are 5 to 15 % higher, almost entirely the longer analysis (9 min instead of 8) and the measured,
heavier chat Lambda. There is no idle compute, database, NAT, load balancer, endpoint or secret-store charge; the zero
month is ECR storage. The spike's Lambda line ($9.25) is chat: 20 000 turns of 35 GB-s past the free 400 000 GB-s.
Mumbai (`ap-south-1`) stays within about 5 % of these totals for the serverless design.

### Budget alarms (variables `monthly_budget_usd`, `daily_budget_usd`)

The owner chose **$20 per month and $5 per day** (the Terraform defaults; this document previously said $10 and $3).
The model supports them:

* Monthly $20 is 1.9x the moderate month ($10.71) and 30x the occasional one. Alerts at 50 / 80 / 100 % are $10 / $16 /
  $20: the 50 % alert fires in a moderate month (about 280 analyses, a useful early signal), the 100 % alert fires
  only between moderate and spike (the spike is $33.20), and the forecast alert fires well before. A quiet, demo or
  occasional month never alerts.
* Daily $5 is 14x an ordinary moderate day ($0.36). With `concurrent_analysis_tasks = 1` the most Fargate can bill in a
  day is 24 task-hours, **$4.86**, so the alert fires only if saturation is combined with chat or Lambda spend (1 000
  chat turns add $0.59) or after the concurrency is raised to 2 (maximum $9.72). Read it as a saturation and abuse
  tripwire, not a spend ceiling.
* If the concurrency limit is raised to 2, raise `daily_budget_usd` to 10 so that normal bursts do not alert.
* Budgets cover AWS only. **Gemini spend never appears in them** (section 4), and they alert after the cost, they do
  not cap it: the caps are the application quotas in `deployment.md` section 6.

## 4. External model spend (a parameter, not an estimate)

LLM spend is **not** in the table: paid Gemini prices were not retrieved (UNVERIFIED) and the keys used for the runs
are free tier. The token profile of the engine is now measured (`performance.md` section 4):

| Guide | Model calls | Input / output tokens |
|---|---|---|
| small (15 chunks, real run) | 13 | 17k / 9k |
| typical (about 100 chunks, two real runs) | 19 to 20 | 42k / 12k |
| large (about 2 500 chunks, derived from prompt size) | about 25 | about 55k / 18k |

    llm_usd = jobs x (input_tokens_per_guide x P_in + output_tokens_per_guide x P_out) / 1e6

* `P_in`, `P_out`: dollars per million tokens of the configured models. Unknown here. For orientation only, at
  $0.30 in / $2.50 out per million (an assumed mid-tier flash price, not the configured models, not a quote) a typical
  guide costs $0.04, so 300 jobs a month is about $13: **the same size as the whole AWS bill**. Verify prices before
  launch.
* Hard ceilings in code and Terraform: 150 calls and 1.5 M tokens per job. Measured guides use 13 to 25 calls and 26k to
  73k tokens, so the ceilings are 6x and 20x above the largest figure; they stop a runaway, they do not predict a
  bill. A tighter ceiling (60 calls, 300k tokens) would still clear every measured guide. Chat is bounded by 200
  messages per session per hour and 2 000 per day globally; a chat turn is one call.
* On the free tier the cost is $0 but the quota binds first: 1 000 requests per day on `gemini-3.5-flash-lite` divided
  by 13 to 20 calls per analysis is **about 50 to 75 analyses per day** (derived; the writer and reviser calls use
  `gemini-3.5-flash`, whose daily quota is not recorded here). The earlier "170 to 200 per day" assumed 5 to 6 calls.
  The spike scenario is quota-bound before it is money-bound.


## 5. What drives cost, and the levers

| Driver | Lever |
|---|---|
| Fargate seconds (about 90 % of the moderate bill) | task size (4 vCPU / 8 GB held by the profile), `MODEL_THREADS=4`, chunk cap (5 000, 2 500 suggested), arm64 once wheels are verified (-18 % in the model), Spot (documented, off by default) |
| LLM tokens | per-job token and call caps, model tiering, quota windows |
| Public IPv4 per task | none needed beyond task lifetime; NAT would cost $32.85 per month fixed |
| ECR | lifecycle keeps 5 tags; compressed size is lower than the uncompressed figure used |
| Logs | 14-day retention, JSON lines, no debug in production |
