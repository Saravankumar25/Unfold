# Unfold AWS infrastructure strategy

Status: **authoritative** (2026-10-10). Supersedes every earlier AWS decision in `six-phase-plan.md`, `docs/HLD.md`
and the phase 1-3 ADRs where they conflict. The owner's directive is reproduced here as an engineering spec; the
architecture below is a *candidate* that phase 4 must challenge with evidence, not a mandatory list of services.

## 1. Context

Unfold is a personal engineering, experimentation and interview-demo project. Most days there are zero requests.
Occasionally the owner uploads a repository or ZIP, runs an analysis, reads the guide and diagrams, chats with the
project, exports a PDF, runs evals, or demos it in an interview. If the site is shared publicly it must degrade
gracefully, enforce quotas and scale within controlled limits. Do not design for millions of users and do not build
something that breaks when several users arrive.

Objectives, in priority order: zero idle application compute; pay per use; low API latency and good AI output; scale
from zero to a few concurrent users; resilience to failures and bursts; correct behaviour after weeks of
inactivity; security, rate limiting and cost protection; minimal complexity; reproducible Terraform; no loss of
existing functionality.

## 2. Safety rules (non-negotiable)

- Never run `terraform apply` or `destroy`, create billable resources, migrate live data or touch an existing
  deployed environment without explicit owner authorization.
- Only static work: write Terraform, `fmt`, `validate`, offline `plan` where it works, policy tests, local
  emulation, and a reviewed deployment plan. Read-only AWS calls (Describe/List/Get) are allowed when credentials
  exist and must be reported as such; if credentials are unavailable, say so and do not pretend live inspection.
- Never overwrite existing state, never remove unrelated resources, no static AWS access keys anywhere, no secrets
  in Terraform state or the repo.
- Never report a measurement, price, resource or test that was not actually obtained. Acceptance tests that need a
  real AWS account are listed as "requires AWS", not marked passed.

## 3. Method

1. **Audit first (evidence-based).** Trace every request frontend -> API -> database -> worker -> AI -> output.
   Identify every persistent store and its real purpose, every sync/async/streaming/long-running operation, every
   place that needs local filesystem or large memory, env vars and secrets handling, CI/CD, existing Terraform.
   Produce a dependency map with source files and compatibility implications. Documentation is not proof.
2. **Challenge the candidate architecture.** For each service answer: why necessary, which feature needs it, does
   compute scale to zero, what is charged while idle, what code changes, latency, security/failure behaviour, is a
   simpler alternative better. Compare against a minimal manually operated single EC2 instance. Consult current
   official AWS documentation (cite URLs).
3. **Implement only justified changes**, end to end through frontend, API, persistence, worker, retrieval and output.
   No disconnected mocks, no Terraform resources that nothing uses.
4. **Test and measure** before/after; document real results.

## 4. Candidate architecture (to be challenged)

| Layer | Candidate | Must be decided by evidence |
|---|---|---|
| Frontend | Existing Next.js on Vercel, existing first-party session-cookie proxy kept | proxy kept unless demonstrably worse |
| API | API Gateway + Lambda (FastAPI adapter) | HTTP API vs REST API; adapter (Mangum vs Lambda Web Adapter) vs response-streaming needs; timeouts; payload limits |
| Async | SQS + Step Functions Standard + ECS Fargate RunTask | is Step Functions needed vs SQS-triggered dispatcher + RunTask; lost-job prevention (outbox) |
| Data | DynamoDB on-demand, Aurora PostgreSQL Serverless v2 (0 ACU pause) only if necessary, S3 | options A (Aurora + DynamoDB), B (DynamoDB-focused + minimal relational), C (simplest safe for current code) |
| Retrieval | existing BM25, dense, Neo4j graph, reranker, context compilation; S3 Vectors vs Qdrant | S3 Vectors only if retrieval quality evals do not regress; Neo4j hosting |
| Ops | CloudWatch, IAM, SSM Parameter Store or Secrets Manager, GitHub Actions OIDC, Terraform | cheapest correct secret store |

CloudFront and AWS WAF only if their security benefit justifies recurring cost.

## 5. Requirements by area

### 5.1 API (API Gateway + Lambda where compatible)
No permanently running API containers; no provisioned concurrency by default; HTTPS; authn/authz; strict request
validation; request timeouts; structured errors; stage- and route-level throttling (best effort, not a spend cap);
safe CORS and cookies; minimal cold start; controlled Lambda concurrency. Investigate chat and progress streaming:
do not silently replace working streaming with buffered responses; verify the browser-to-backend behaviour.
Repository uploads go browser -> S3 via presigned URL (size, content type, key scoped to project/session) with
ownership enforcement and post-upload archive inspection kept.

### 5.2 Data
Evaluate DynamoDB on-demand for rate limits, quotas, idempotency, job status, durable progress, session metadata,
coordination, AI counters, using atomic conditional updates. DynamoDB TTL is not immediate: expiry is checked in
reads, and cleanup never relies on TTL alone. Decide whether Aurora is genuinely necessary by inspecting the real
queries, transactions, foreign keys and checkpoints; do not migrate relational logic to DynamoDB unless every
invariant is preserved and the benefit is clear. If Aurora is chosen: verify region and engine support for min 0
ACU, auto pause, no persistent connections, bounded max capacity, transaction/checkpoint correctness, tested resume.
Never claim zero database cost while storage, I/O and backups remain.

### 5.3 Workers
Heavy work (ingestion, extraction, Tree-sitter, IR, graph, embeddings, dense index, hybrid retrieval, reranking, LLM
calls, guide generation, PDF rendering) is not forced into Lambda. Flow: request durably recorded -> bounded queue ->
controlled dispatcher -> Step Functions (if justified) -> Fargate task -> progress and checkpoints persisted ->
results durable -> task exits. No lost jobs between DB commit and dispatch (outbox or reconciliation sweeper), idempotent,
checkpoint recovery, bounded retries with exponential backoff and a DLQ, no always-on ECS service, small images,
benchmarked CPU/memory before sizing.

### 5.4 Rate limiting and cost protection (layered)
API Gateway stage/route throttling; application quotas in a durable atomic store for session creation, repository
submissions, ZIP uploads, chat messages, analysis requests, PDF exports and expensive AI operations, keyed by
trustworthy identity (never a raw client header); maximum queued jobs, maximum concurrent analysis tasks, maximum
active jobs per user, repository size limits, execution deadlines, LLM token budget, model-call cap, retry budget,
queue backpressure, explicit overload responses. Preserve safeguards that are already correct.

### 5.5 Performance
Optimise and measure (p50/p95/p99) separately: static load, API, sessions, job submission, progress, analysis,
retrieval, chat first token, full AI response, PDF export. Investigate Lambda cold start, Aurora wake-up, Fargate
start, query/index performance, repeated model init, embedding/rerank cost, provider latency, caching (only where
isolation and correctness hold). Do not wake expensive infrastructure for a status request. Benchmark before and
after; never fabricate improvements.

### 5.6 Scaling modes and failure handling
Personal (near-zero idle, cold starts fine), interview demo (optional deliberate warm-up, predictable, clear failure
reporting), growth (bursts, durable queue, bounded parallelism, no duplicate processing, no uncontrolled spend).
Limits are configuration, not rewrites. Handle Lambda throttling, DB wake-up, worker termination, SQS duplicate
delivery, LLM rate limits, network failures, partial workflow failures, expired sessions, outages, bursts and
connection exhaustion with circuit breakers, bounded retries, idempotency and graceful degradation. Interactive chat
capacity is separate from background analysis capacity.

### 5.7 Long-term reliability
Correct recovery after 24 h / 7 d / 30 d idle: DB resume, cold worker, expired credentials, old images,
interrupted analysis, partial cleanup. Automated checks without always-on heartbeat services; credential lifecycle,
migrations, backups, restore procedure, dependency management. Expired project data is removed from every store
without relying only on asynchronous TTL.

### 5.8 Terraform and security
Pinned providers; reusable modules; separate environments; GitHub Actions OIDC; least-privilege roles, separate for
API, workers, orchestration and deployment; private encrypted object storage; secure secrets; no NAT Gateway or ALB
unless justified with calculated recurring cost (interface endpoints are not a free NAT replacement); no public
database; no permanent ECS services; budgets and notifications; cost-allocation tags; short configurable log
retention; image lifecycle policies; safe backup settings; state protection; destructive-change safeguards.

### 5.9 Testing
Terraform fmt/validate; IAM and infrastructure policy checks; API routing; auth and sessions; rate limits;
presigned uploads; queue dispatch; duplicate delivery; worker start/stop; retries and recovery; durable progress;
database compatibility; graph and vector retrieval; chat; PDF; expired-session cleanup; concurrency controls. Load
tests: rising concurrency, short spikes, sustained load, overload rejection. Accepted jobs complete or are durably
recoverable. LLM provider failures are never papered over with fabricated output. Retrieval and answer quality
measured before and after. The system returns to zero running tasks after work completes.

## 6. Deliverables (docs under `docs/infra/`)

Verified existing architecture and dependency map; decision record with alternatives (ADRs 0010+); final
architecture diagram; request and data flows; every AWS resource and its purpose; Terraform module layout;
deployment procedure; env vars and secrets; rate limits and concurrency settings; performance notes with measured
numbers and their source; reliability and recovery procedures; cost-saving mechanisms; cost estimates for zero-traffic
month, occasional personal use, interview demo, moderate activity and spikes, split into compute, storage, database
I/O, API requests, orchestration, networking, observability and external AI, using documented regional pricing and
stated assumptions (estimates, never "actual"); scaling behaviour; known limitations; tests done and remaining
validation; safe rollback.

## 7. Acceptance

Infrastructure matches real requirements; Terraform organised, validated, reproducible; no unnecessary always-on
compute; existing functionality, AI pipelines and retrieval quality preserved; background processing durable and
recoverable; rate limits and concurrency enforced; cold starts and trade-offs documented; scaling bounded by
configurable quotas; long inactivity does not permanently break the app; recurring costs and risks explicit; real
test results reported; nothing deployed without authorization.

## 8. What this overrides in earlier plans

| Earlier statement | Now |
|---|---|
| Phase 4 spike "RDS Data API vs VPC Lambda", "Aurora 0 ACU", "S3 Vectors vs Qdrant" as isolated spikes | One evidence-based architecture selection covering all candidates incl. DynamoDB-focused option and EC2 comparison |
| Aurora Serverless v2 assumed | Aurora only if the audit shows relational invariants need it (options A/B/C) |
| `DynamoRateLimiter` + `SqsJobQueue` as the only AWS adapters | Plus quotas, idempotency, outbox/dispatch reliability, job and concurrency caps, per-user limits |
| API Lambda via Mangum, assumed HTTP API | Adapter and API type chosen after verifying streaming/timeout needs |
| Cost model in phase 5 only | Cost comparison for five usage scenarios is a phase 4 deliverable and is re-checked in phase 5 |
| Phase 6 deploys after phase 5 | Unchanged, and still requires explicit owner authorization before any `apply` |
