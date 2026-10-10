# Unfold: Six-Phase Plan (build, test, ship, deploy to AWS)

Source inputs: `PROJECT_AUDIT.md` (repo state) and the AWS scale-to-zero plan (target state, reproduced in Appendix A).

**Rule of the plan:** phases 1-5 each end merged to `main` and pushed. Phase 6 deploys to AWS only after phase 5 is pushed. If all six exit gates pass, the product is built, tested, shipped and live.

## Operating rules (every phase)

**Branch lifecycle**
1. Start: `git worktree list` and `git status` on `main`. Create one worktree per phase under `.claude/worktrees/phase-N-<name>` on branch `phase-N-<name>` (skill `superpowers:using-git-worktrees`). Max 2 worktrees alive.
2. Build inside the worktree only.
3. Test gate: run `scripts/check.py` plus the phase's tests (listed per phase). Review with `ecc:code-review` and `ecc:security-review`; fix findings.
4. Merge to `main` (fast-forward or squash, skill `superpowers:finishing-a-development-branch`). Re-run the gate on `main`.
5. `git push origin main`.
6. Clean up: `git worktree remove .claude/worktrees/phase-N-<name>`, `git branch -d phase-N-<name>`, delete the remote branch if it was pushed, `git worktree prune`. Check `git branch --merged main` and `git log origin/main..branch` first; never force-remove.
7. Run `graphify update .`. Tick the phase in the status table below.

**Commits and pushes:** author `udarshmarthala`, no Claude co-author trailer, remote `udarshmarthala/unfold` (stored preference, overrides the default trailer). Never commit `.env`, build output or caches.

**Tooling per task**
- **Ponytail** (`ponytail:ponytail` stays on at `full`): smallest complete change, reuse the existing libs listed in the audit, one test per non-trivial branch. Run `ponytail:ponytail-review` on each diff before merge and `ponytail:ponytail-debt` at the end of phases 3 and 5.
- **Planning/spec:** `superpowers:writing-plans` and `agent-skills:spec` at the start of each phase to turn the phase into a task list; track tasks with claude-mem `work_state_write`.
- **Build:** `superpowers:test-driven-development`, `agent-skills:incremental-implementation`, `superpowers:subagent-driven-development` for independent tasks, `caveman:cavecrew-builder` for 1-2 file edits.
- **Debug:** `superpowers:systematic-debugging`, `ecc:build-fix`.
- **Review:** `ecc:python-review`, `ecc:fastapi-review`, `ecc:react-review`, `ecc:rag-pipeline-reviewer`, `ecc:database-reviewer`, `claude-security:claude-security` (phase 5).
- **Verify:** `superpowers:verification-before-completion` before any "done" claim; `ecc:quality-gate`; `ecc:e2e-testing` and `agent-skills:browser-testing-with-devtools` for UI.
- **Infra:** `ecc:deployment-patterns`, `ecc:docker-patterns`, `agent-skills:ci-cd-and-automation`, `engineering:deploy-checklist`.
- **UI polish:** `impeccable:impeccable`, `ecc:frontend-a11y`.
- **Context hygiene:** `graphify query` before grepping, `/clear` between phases, claude-mem for cross-session recall.

**LLM defaults:** Gemini 3.5-flash-lite / 3.5-flash / 3.6-flash (2.5 models are retired for new keys; Pro has no free quota). Every LLM call goes through the existing gateway with a per-job token budget.

## Status

| Phase | Name | Merged | Pushed | Worktree/branch deleted |
|---|---|---|---|---|
| 1 | Foundation: green gates, CI, security, ports | [x] | [x] | [x] |
| 2 | Core pipeline end to end (local) | [ ] | [ ] | [ ] |
| 3 | Chat, exports, cleanup, evals, frontend contract | [ ] | [ ] | [ ] |
| 4 | AWS-native adapters + Terraform | [ ] | [ ] | [ ] |
| 5 | Hardening and full verification | [ ] | [ ] | [ ] |
| 6 | Deploy to AWS and ship | n/a | n/a | n/a |

---

## Phase 1: Foundation (branch `phase-1-foundation`)

Goal: a trustworthy base. Gates green, CI running, audit security findings fixed, and every Redis/arq/MinIO/SSE coupling hidden behind a port so phase 4 swaps adapters instead of rewriting.

Tasks
1. Fix the quality gate: ruff (5), mypy (4), eslint (3); add a vitest setup with one real test; make `scripts/check.py` green on a clean checkout.
2. Add `.github/workflows/ci.yml`: backend (ruff, mypy, pytest), frontend (eslint, tsc, vitest, build), `pip-audit`, `npm audit`.
3. Security fixes from the audit: S-1 (per-IP and global limits on session and project creation), S-2 (trusted proxy IPs only, proxy overwrites XFF), S-3 (production startup guard: secure cookie, no default creds), S-4 (atomic rate-limit), S-5 (stream cap, becomes moot once polling lands but keep until then), S-6 (CSP, HSTS, Permissions-Policy), S-7 (drop `X-Unfold-Session`).
4. Introduce ports with local adapters, behavior unchanged: `BlobStore` (MinIO now, S3 later), `JobQueue` (arq now, SQS later), `RateLimiter` (Redis now, DynamoDB later), `ProgressStore` (durable table in Postgres; replaces Redis stream replay). Callers depend on the port only.
5. Remove dead dependencies decision: keep `langgraph` / `langgraph-checkpoint-postgres` only if phase 2 uses them; drop `deepagents` unless phase 3 chat uses it. Record the call in an ADR (`agent-skills:documentation-and-adrs`).
6. Rewrite `CLAUDE.md` per audit section 10 (worktree rules, graphify rules, no-mocks rule kept).

Tests: existing 129 backend tests stay green; new unit tests per security fix (forged XFF ignored, 51st session blocked, prod guard fails startup, rate-limit fault injection); port contract tests run against both adapters where possible.

Exit gate: CI green on `main`; every S-1..S-7 item has a passing test; no code outside adapters imports `redis`, `arq` or the MinIO client.

## Phase 2: Core pipeline end to end, local (branch `phase-2-pipeline`)

Goal: submit a GitHub URL or ZIP locally and get a real guide with diagrams. The one-shot worker design here is what Fargate runs later.

Tasks
1. Worker entrypoint `python -m unfold.worker.run --job-id <id>`: loads the job, runs the pipeline, writes results, exits 0/non-zero. Local dev drives it from the `JobQueue` local adapter. No long-lived worker loop.
2. `run_analysis` pipeline: `acquire (ingestion/service) → repo_intel → project_ir → graph build → index (BM25 + dense) → plan sections → retrieve + write each section via gateway → verify claims → diagrams → persist`. Each stage writes a checkpoint row; rerun skips completed stages; results persist before success is reported (idempotent, safe to resume).
3. Composition root: build the LLM gateway (`llm/factory.py`), retriever, graph and index clients once per job. Fix `Runtime.llm` always being `None`.
4. Guide planner, writer, claim verifier in `guide/` with evidence refs; write `GuideSectionRow`, `DiagramRow`, `ArtifactRow`.
5. Progress via `ProgressStore`; API gets `GET /projects/{id}/progress?after=<seq>` short-poll endpoint. SSE route removed (not deprecated: the target has no persistent streams).
6. Read routes: guide, sections, diagrams, architecture, artifacts.
7. Hard limits: job wall-clock, per-job token budget, max files and size, stage retry cap with dead-letter state.
8. Decide LangGraph: use it for the section-writing graph only if its checkpoints replace the stage table cleanly; otherwise delete the dependency. Do not carry it as decoration.

Tests: pipeline integration test on a small fixture repo against docker-compose services (no mocks, per `backend/CLAUDE.md`); kill-mid-stage resume test; poll endpoint ordering test; archive-bomb and path-traversal fixtures through the real path. Manual: `/run` a real public repo end to end.

Exit gate: fixture repo and one real public repo yield a persisted guide with diagrams; killing the worker mid-run and rerunning completes without duplicate rows; no stage touches an uploaded file's code for execution.

## Phase 3: Chat, exports, cleanup, evals, frontend contract (branch `phase-3-features`)

Goal: every feature the frontend and HLD claim exists and is proven.

Tasks
1. Chat backend: `POST /chats`, `POST /chats/{id}/messages`, retrieval-grounded answers with evidence refs, streamed or chunked per request (first-token latency measured separately). Prompt-injection fencing (`security/fence.py`, `security/injection.py`) exercised for real.
2. Exports: Markdown and Chromium PDF routes, run as a worker job type (heavy), stored through `BlobStore`, returned by presigned or proxied download.
3. `cleanup_project` and stale-job sweeper: session expiry removes DB rows, blobs, vector entries, graph nodes, rate-limit keys. One idempotent function, callable by a scheduler.
4. `evaluation/`: offline evals for groundedness, retrieval hit rate, graph usefulness, injection fixtures. Wire the `poe eval-*` tasks. Record a baseline score file; later phases must not regress it.
5. Align frontend client to the real API; replace SSE UI with polling hook (backoff, "waking up" state for slow first request); add error, not-found and loading states; vitest component tests.
6. Update `docs/HLD.md` to match reality.
7. Optional agent use (Deep Agents) only if it measurably beats the plain retrieval-then-write path in evals; otherwise cut it.

Tests: chat grounding and injection evals above threshold; export golden-file test (PDF renders, Mermaid sanitized); cleanup test proves nothing is left in any store; vitest for polling and chat UI.

Exit gate: all frontend pages work against the real backend locally; eval baseline recorded; `ponytail:ponytail-debt` shows no unused modules.

## Phase 4: AWS-native adapters + Terraform (branch `phase-4-aws`)

Goal: the same code, scale-to-zero runtime. Nothing is applied to AWS yet; this phase proves it with local emulation, `terraform validate` and `plan`.

Spikes first (each ends in a written decision in `docs/adr/`)
- **Lambda to Aurora path:** RDS Data API from a non-VPC Lambda (no NAT, simplest, needs a SQLAlchemy Data API dialect) vs Lambda inside the VPC (needs endpoints for SQS/Step Functions/S3, which can add hourly cost). Pick the cheapest that keeps SQLAlchemy transactions working.
- **Vector search:** Amazon S3 Vectors vs keeping Qdrant. Compare filtering, latency, upsert behavior and retrieval hit rate with the phase 3 evals. Switch only if evals do not regress.
- **Neo4j:** managed free-tier instance first. Record its limits; do not claim equivalence with any S3-backed graph without running the evals.
- **Aurora 0 ACU:** confirm auto-pause config, measure resume time, design the client "waking up" retry.

Adapters
1. `S3BlobStore`, presigned ZIP upload (size limit, content-type, key scoped to project/session), post-upload archive inspection retained.
2. `SqsJobQueue` plus a small dispatcher: SQS to Step Functions Standard to Fargate `RunTask` (`.sync`), one bounded job per task, task exits on completion, DLQ with bounded retries.
3. `DynamoRateLimiter` (TTL items) and any temporary coordination; progress stays in Postgres.
4. API Lambda via Mangum: lean image (no torch, no Tree-sitter, no Chromium), provisioned concurrency 0, per-request DB connection, no persistent connections.
5. Worker image: pinned, models (embedding, reranker) baked in, Chromium for PDF; benchmark at 2 vCPU / 4 GB then right-size from measured memory and time.

Terraform (`infra/terraform/`, modules mirror the plan)
`network_iam`, `api_lambda`, `storage_s3`, `queues_sqs`, `workflow_sfn`, `worker_ecs`, `aurora`, `dynamodb`, `vectors_graph`, `observability_budgets`; separate `envs/dev` and `envs/prod`; remote state in S3 with locking; GitHub Actions OIDC deploy role with least privilege.
Cost guards enforced in code: policy tests (OPA/conftest or `terraform plan` JSON checks) fail on any `aws_ecs_service`, `aws_nat_gateway`, load balancer, provisioned Aurora instance or provisioned Lambda concurrency. Fargate tasks run in public subnets with a public IP and locked-down security groups; only S3/DynamoDB gateway endpoints are allowed (interface endpoints need an explicit exception).

Tests: adapter contract tests against LocalStack or equivalent; one-shot worker run against real SQS/S3 emulation; `terraform fmt`, `validate`, `plan` clean for dev; policy tests; image build in CI; eval baseline unchanged after adapter swap.

Exit gate: full pipeline passes locally through the AWS adapters; `terraform plan` clean and passes cost-guard policies; ADRs recorded; CI builds both images.

## Phase 5: Hardening and full verification (branch `phase-5-hardening`)

Goal: confidence before money is spent. Everything testable without real AWS is tested.

Tasks
1. Playwright journeys in CI: create session, submit GitHub URL, watch progress, read guide, open diagram, chat with evidence, export PDF, session expiry, ZIP upload, error states.
2. Security: `claude-security:claude-security` full scan; manual prompt-injection red-team of the chat and guide paths; verify uploaded code is never executed; re-check archive limits; secrets scan; dependency audits clean or triaged.
3. Performance: first-token chat latency budget; frontend Lighthouse and `agent-skills:webperf`; worker memory and time profile to set final Fargate size; API Lambda cold-start measurement and package size.
4. Reliability: failure injection (LLM 429/timeouts, worker crash, duplicate SQS delivery, DB paused) and verify checkpoint resume, DLQ and "waking up" UX.
5. Cost model: expected cost per analysis (tokens + Fargate seconds + Aurora I/O) and monthly idle cost; budget alarm thresholds derived from it. Document in `docs/cost.md`.
6. UI polish with `impeccable:impeccable`: mobile layout, keyboard-accessible diagram, reduced motion, empty and error states, OG/icons.
7. Docs: README quickstart, runbook (deploy, rollback, drain DLQ, rotate keys), `docs/HLD.md` final.
8. Run `ponytail:ponytail-audit` and `ponytail:ponytail-debt`; delete what is unused.

Exit gate: CI green including e2e; security scan has no open High/Critical; evals at or above baseline; runbook and cost doc exist; `main` pushed and clean.

## Phase 6: Deploy to AWS and ship (no feature branch; infra applied from `main` via OIDC)

Goal: live system, verified, with rollback. Starts only after phases 1-5 are merged and pushed.

Steps (use `engineering:deploy-checklist`; confirm each destructive or billable step with the owner before running)
1. Preflight: AWS account and region chosen, billing budget and alerts created first, IAM OIDC role, state bucket. Secrets (Gemini/Groq keys, Neo4j creds) in SSM Parameter Store or Secrets Manager, never in Terraform state or the repo.
2. `terraform apply` dev, run the smoke suite, then prod. Push images to ECR from CI.
3. Frontend: Vercel project from the repo; env points at the API Gateway URL; keep the first-party session-cookie proxy; verify cookies are `Secure`/`HttpOnly`/`SameSite` and the proxy overwrites XFF.
4. Production verification (checklist, record results in `docs/deploy-log.md`):
   - Idle check: no running ECS tasks, Aurora paused at 0 ACU, no NAT/ALB in the account.
   - Cold start: Lambda first request, Aurora resume, Fargate start to first progress event.
   - Real analysis of a small and a medium public repo; ZIP upload; chat first-token latency; PDF export.
   - Failure drill: kill a task, force a DLQ message, confirm alarms fire.
   - Cleanup: expire a session and confirm S3, DB, vector, graph and DynamoDB entries are gone.
   - Cost: check Cost Explorer after 24-48 h against the phase 5 model.
5. Rollback ready: previous image tags and Terraform state versions kept; `terraform destroy` for dev documented; Vercel instant rollback.
6. Ship: tag `v1.0.0`, GitHub release notes, final `graphify update .`, update the status table, delete any remaining worktrees/branches.

Exit gate: all production verification items pass; idle cost matches the model; release tagged and pushed. Product is done.

---

## Appendix A: Target architecture (from the AWS plan)

Browser → Next.js (Vercel) → API Gateway HTTP API → Lambda (FastAPI + Mangum) → Aurora PostgreSQL Serverless v2 (0 ACU, auto-pause).
Upload → presigned S3 → job row → SQS → Step Functions Standard → ECS Fargate RunTask (analysis, graph, index, LLM, guide, PDF) → Postgres / S3 / indexes → frontend via short polling.

Replacements: Redis → SQS + DynamoDB + Postgres; MinIO → S3; arq → SQS/Step Functions (a refactor, not a drop-in); SSE → durable progress rows + polling; Qdrant → S3 Vectors only if evals pass; Neo4j → managed free tier first, no equivalence claim without evals.
Workers: start only for a real job, one bounded job each, checkpoint stages, persist before success, exit when done, bounded by concurrency, memory, time and LLM budget.
Security: never execute uploaded code; archive size/count/extraction/path-traversal limits; per-project and per-session isolation; session expiry with cleanup across every store; bounded retries with DLQ.
