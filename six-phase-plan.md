# Unfold: Six-Phase Plan (build, test, ship, deploy to AWS)

Source inputs: `PROJECT_AUDIT.md` (repo state) and `docs/infra/strategy.md` (authoritative AWS strategy, supersedes the earlier scale-to-zero plan; Appendix A is only the original candidate).

Guide-quality strategy: `docs/design/investigation-engine.md` (ADR 0017). It is owned by phase 3, run on AWS in phase 4,
regressed and hardened in phase 5 and verified live in phase 6. It adds no phase, store or always-on service.

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
| 2 | Core pipeline end to end (local) | [x] | [x] | [x] |
| 3 | Chat, exports, cleanup, evals, frontend contract, investigation engine | [x] | [ ] | [ ] |
| 4 | AWS-native adapters + Terraform | [ ] | [ ] | [ ] |
| 5 | Hardening and full verification | [ ] | [ ] | [ ] |
| 6 | Deploy to AWS and ship | n/a | n/a | n/a |

Phases 1-2 are complete and unchanged by the quality strategy. Phase 3 (3A features, 3B engine) is built,
reviewed, gated and merged to `main`; the v2 rubric gate holds (`docs/design/quality-report.md`); human review sheets are
unfilled. The phase 4 branch holds stages A-F, a review pass and (2026-10-11) the merge of phase 3: chat, exports,
regeneration and the investigation engine run on both persistence profiles behind the same ports. It is built,
reviewed and gated, and stays unmerged until the owner merges it; nothing is deployed.

---

## Phase 1: Foundation (branch `phase-1-foundation`)

Goal: a trustworthy base. Gates green, CI running, audit security findings fixed, and every Redis/arq/MinIO/SSE coupling hidden behind a port so phase 4 swaps adapters instead of rewriting.

Tasks
1. Fix the quality gate: ruff (5), mypy (4), eslint (3); add a vitest setup with one real test; make `scripts/check.py` green on a clean checkout.
2. Add `.github/workflows/ci.yml`: backend (ruff, mypy, pytest), frontend (eslint, tsc, vitest, build), `pip-audit`, `npm audit`.
3. Security fixes from the audit: S-1 (per-IP and global limits on session and project creation), S-2 (trusted proxy IPs only; the Next proxy forwards a client address only from the Vercel platform header, or from X-Forwarded-For when `TRUST_FORWARDED_FOR=1` is set behind a proxy that appends the peer), S-3 (production startup guard: secure cookie, no default creds), S-4 (atomic rate-limit), S-5 (stream cap, becomes moot once polling lands but keep until then), S-6 (CSP, HSTS, Permissions-Policy), S-7 (drop `X-Unfold-Session`).
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

## Phase 3: Chat, exports, cleanup, evals, frontend contract, investigation engine (branch `phase-3-features`)

Goal: every feature the frontend and HLD claim exists and is proven, and guides explain how the repository works
(investigate, plan, research, write, verify) instead of summarising retrieved chunks. Design and contracts:
`docs/design/investigation-engine.md`.

### 3A. Product features (built on the branch, review fixes applied)
1. Chat backend: `POST /chats`, `POST /chats/{id}/messages`, retrieval-grounded answers with evidence refs, first-token
   latency measured separately. Prompt-injection fencing exercised for real.
2. Exports: Markdown and Chromium PDF as a worker job type, stored through `BlobStore`.
3. `cleanup_project` and stale-job sweeper: one idempotent function across DB, blobs, vectors, graph, rate-limit keys.
4. `evaluation/`: offline evals (groundedness, retrieval hit rate, graph usefulness, injection fixtures), baseline file.
5. Frontend aligned to the real API; polling hook; error, not-found, loading states; vitest component tests.
6. `docs/HLD.md` and LLD to reality.

### 3B. Investigation engine (new; tasks in dependency order)
| # | Task | Reuses | New | Depends on |
|---|---|---|---|---|
| 3.1 | **Baseline.** Run the current pipeline on 2 repos (small: `evals/fixtures/repos/fastapi_rag`; moderate: a pinned public repo). Preserve guides, tokens, seconds, cost and per-dimension scores in `evals/baselines/pipeline-v1/` | `run_analysis`, gateway usage log | scorer harness stub | 3A.4 |
| 3.2 | **RepoMap and proof labels.** Symbol lookup, neighbour expansion, edge `proof` (static/heuristic/inferred), entry points | `ProjectIR`, `graph/builder.py`, `graph/queries.py` | `understand/repomap.py`, `proof` on graph edges | none |
| 3.3 | **Flow tracing and architecture model.** Deterministic `trace_flow`, components, persistence boundaries, uncertainty list; diagrams only from non-inferred edges | `graph/communities.py`, `diagrams/` | `understand/flows.py`, `understand/architecture.py` | 3.2 |
| 3.4 | **Adaptive planner v2.** Deterministic skeleton per project kind, chapter count from complexity, LLM refines; validation with typed fallback | `plan_guide`, `normalise_plan` | `guide/plan.py`, `ChapterPlan` | 3.3 |
| 3.5 | **Investigator.** Per-chapter bounded loop: symbol lookup, hybrid retrieve, flow trace, tests; `ResearchBrief` with coverage and budget caps | `HybridRetriever`, sufficiency, `compile_context` | `guide/research.py`, stage `RESEARCHING_GUIDE` | 3.2, 3.4 |
| 3.6 | **Writer from brief.** Same `SectionDraft` blocks, plus failure-mode and change-guidance blocks when the brief supports them | `write_section` | `guide/write.py` | 3.5 |
| 3.7 | **Verifier and reviser.** Deterministic path/line/symbol/excerpt/edge checks, existing `grade_claims`, one semantic critique, <= 2 targeted revision rounds, unsupported statements qualified or removed | `grade_claims`, `_verify` | `guide/verify.py`, `guide/revise.py`, stage `REVISING_GUIDE` | 3.6 |
| 3.8 | **Reading experience and regeneration.** Compact commit-pinned citations, evidence appendix, exports read canonical content, `POST /projects/{id}/sections/{key}/regenerate`, per-chapter progress | `GuideSectionRow.version`, exports, guide page | additive columns `research`, `verification` (`input_hash` column reserved, reuse not built: one analysis job per project) | 3.7 |
| 3.9 | **Rubric and A/B.** Automated scorers, human review sheet, run v2 vs baseline, record in `evals/baselines/pipeline-v2/` and `docs/design/quality-report.md` | 3A.4 harness | `evaluation/rubric.py`, `poe eval-guide` | 3.1, 3.8 |

Rules: ports only (no SDK imports); no new store; additive migrations only; chat reuses RepoMap lookups where it
helps and is measured by the existing chat evals; Deep Agents stay cut unless 3.9 shows a measurable win.

Tests: contract tests for every model in section 4 of the design; fixture repos with known edges and flows (RepoMap,
`trace_flow`, planner validation); investigator budget-exhaustion and early-stop tests; verifier rejects a fabricated
path, out-of-range line, altered excerpt and non-existent edge; revision loop terminates and touches only failing
blocks; resume mid-chapter; single-chapter regeneration leaves others untouched; export golden file; cleanup
completeness; vitest for polling, chat and citation rendering.

Exit gate: all frontend pages work against the real backend locally; eval baseline recorded; v2 meets the rubric gate
(no dimension > 0.3 below baseline, citation accuracy >= 0.98, no unsupported statement presented as fact) on both
repos, with a human spot-check recorded; `ponytail:ponytail-debt` shows no unused modules.

## Phase 4: AWS infrastructure strategy and implementation (branch `phase-4-aws`)

**Authoritative spec: `docs/infra/strategy.md` (supersedes earlier AWS decisions).** Goal: the smallest sensible, secure,
resilient, cost-efficient AWS infrastructure that runs Unfold end to end, proven locally. Study first, challenge the
candidate architecture, implement only what is justified, document measured results. **Nothing is applied to AWS**;
`terraform apply`/`destroy`, billable resources and live-data changes need explicit owner authorization.

Stages (each ends in a committed artifact; implementation does not start before stage B is written)
- **A. Audit.** Evidence-based dependency map (`docs/infra/existing-architecture.md`): every request path, store and
  its real purpose, sync/async/streaming/long-running operations, filesystem and memory needs, env/secrets, CI/CD.
- **B. Architecture selection** (`docs/infra/architecture-decision.md` + ADRs 0010+). Per service: why, which feature,
  scale-to-zero, idle charges, code changes, latency, failure modes, simpler alternative. Compare serverless vs one
  manually operated EC2. Decide with cited official docs: API Gateway HTTP vs REST and Lambda adapter vs streaming
  needs; Step Functions vs SQS-triggered dispatcher; DynamoDB-only vs Aurora (0 ACU) vs simplest compatible
  (options A/B/C); S3 Vectors vs Qdrant (switch only if evals do not regress); Neo4j hosting; secret store;
  CloudFront/WAF only if justified; NAT/endpoints with calculated cost.
- **C. Implement** the selected design behind the existing ports: object store (presigned upload, scoped keys,
  post-upload inspection), job queue + dispatcher with lost-job prevention (outbox or reconciliation), durable
  quotas/idempotency/rate limits with atomic conditional updates (TTL never trusted for correctness), job and
  concurrency caps (queued, concurrent, per user), API entrypoint for Lambda, lean API image, pinned worker image.
- **D. Terraform** (`infra/terraform/`): only modules the decision needs; pinned providers; envs dev/prod; OIDC deploy
  role; separate IAM roles for API, workers, orchestration, deploy; private encrypted storage; budgets, tags, log
  retention, image lifecycle, backup and state protection; cost-guard policy tests (no always-on ECS service, NAT,
  load balancer, provisioned DB or Lambda concurrency unless an ADR grants an exception) with negative tests.
- **E. Verify.** Terraform fmt/validate/offline plan; adapter contract tests against an emulator; duplicate delivery,
  retry/recovery, cleanup completeness; load and overload tests locally; benchmarks (p50/p95/p99) and image sizes
  measured, never invented; list which acceptance tests require a real AWS account.
- **F. Document** (`docs/infra/`): diagram, flows, resource inventory, module layout, deployment and rollback,
  env/secrets, limits, performance, reliability and recovery, cost for five scenarios (zero month, occasional use,
  interview demo, moderate, spike) split by cost type with stated assumptions, scaling, known limitations.

Status (2026-10-11, branch `phase-4-aws`): stages A to F are implemented and verified locally; nothing was applied.
Phase 3 is merged in: chat messages, export artifacts, the delete marker, typed jobs and chapter regeneration are behind
`ControlStore` on the Postgres and the DynamoDB adapters (one contract suite), the dispatcher starts `export` tasks, the
sweeper is one function for both profiles, and an architecture test keeps the investigator, verifier and reviser out of
the API. Evidence and open items: `docs/infra/operations.md` sections 7 and 8, deployment `docs/infra/deployment.md`,
cost `docs/infra/cost.md`. Remaining before phase 6: the "requires AWS" acceptance list (including Fargate memory and
wall clock of the 21-stage pipeline and the exporter image pull), the exporter image size (3.28 GB, a known limitation),
and the owner's authorization to apply anything.

Pipeline support (no new services; the engine runs on the ports and the phase 4 backends):
- Retrieval-backend swaps (embedded dense, in-memory graph, S3 Vectors, Neo4j removal) are gated by the phase 3 rubric
  and retrieval evals; a swap that drops any dimension > 0.3 below the v2 baseline is rejected.
- Worker size, task timeout and per-job token budget come from the measured engine profile (research rounds, revision
  rounds, peak memory) on the fixture repos, not from guesses; limits stay configuration.
- Chapter regeneration and revision run as worker work through the same `JobQueue`; the API Lambda never runs the
  investigator. Interactive chat keeps its separate capacity.
- Cost scenarios include the engine's measured LLM spend per guide next to compute.

Tests: as stage E, plus the rubric gate on the in-memory and embedded backends against the v2 baseline.
Exit gate: gate green on `main`; `terraform validate` and policy tests pass; ADRs and docs recorded; both images build
in CI; unmeasured items are labelled "requires AWS"; no resource was deployed.

## Phase 5: Hardening and full verification (branch `phase-5-hardening`)

Goal: confidence before money is spent. Everything testable without real AWS is tested.

Tasks
1. Playwright journeys in CI: create session, submit GitHub URL, watch progress, read guide, open diagram, chat with evidence, export PDF, session expiry, ZIP upload, error states.
2. Security: `claude-security:claude-security` full scan; manual prompt-injection red-team of the chat and guide paths; verify uploaded code is never executed; re-check archive limits; secrets scan; dependency audits clean or triaged.
3. Performance: first-token chat latency budget; frontend Lighthouse and `agent-skills:webperf`; worker memory and time profile to set final Fargate size; API Lambda cold-start measurement and package size.
4. Reliability: failure injection (LLM 429/timeouts, worker crash, duplicate SQS delivery, DB paused) and verify checkpoint resume, DLQ and "waking up" UX.
5. Cost model: re-check the phase 4 cost scenarios (zero month, occasional use, interview demo, moderate, spike) against measured tokens, Fargate seconds and database I/O; derive budget alarm thresholds. Document in `docs/infra/cost.md`.
6. UI polish with `impeccable:impeccable`: mobile layout, keyboard-accessible diagram, reduced motion, empty and error states, OG/icons.
7. Docs: README quickstart, runbook (deploy, rollback, drain DLQ, rotate keys), `docs/HLD.md` final.
8. Run `ponytail:ponytail-audit` and `ponytail:ponytail-debt`; delete what is unused.
9. **Guide-quality regression suite.** Deterministic scorers run in CI on stored guides and snapshots (citation
   resolve rate, verified-claim ratio, coverage of IR components and entry points, redundancy, injection fixtures); live
   LLM evals run on demand (`-m llm`) and their recorded scores are compared to `evals/baselines/pipeline-v2/`.
10. **Depth and coverage checks** on three fixture repos (small CLI, web service, multi-module) so the planner is
    proven to emit different outlines; negative fixtures (unsupported language, empty repo, huge file, dynamic dispatch)
    must produce stated uncertainty, not invented flows.
11. **Performance and cost**: per-stage and per-chapter latency, tokens and cost for investigator, writer, reviser;
    retrieval and trace p50/p95; peak worker memory; set budget alarms from these numbers.
12. **Failure recovery**: LLM 429/timeout mid-research, worker kill between research and write, kill during revision,
    duplicate SQS delivery of a chapter task, regenerate-during-run; each must resume or fail typed with no duplicate
    rows and no half-written chapter.

Exit gate: CI green including e2e; security scan has no open High/Critical; evals and the guide-quality suite at or above
the phase 3 baseline; failure-recovery cases pass; runbook and cost doc exist; `main` pushed and clean.

## Phase 6: Deploy to AWS and ship (no feature branch; infra applied from `main` via OIDC)

Goal: live system, verified, with rollback. Starts only after phases 1-5 are merged and pushed.

Steps (use `engineering:deploy-checklist`; confirm each destructive or billable step with the owner before running)
1. Preflight (owner authorization required before any `terraform apply`, see `docs/infra/strategy.md` §2): AWS account and region chosen, billing budget and alerts created first, IAM OIDC role, state bucket. Secrets (Gemini/Groq keys, Neo4j creds) in SSM Parameter Store or Secrets Manager, never in Terraform state or the repo.
2. `terraform apply` dev, run the smoke suite, then prod. Push images to ECR from CI.
3. Frontend: Vercel project from the repo; env points at the API Gateway URL; keep the first-party session-cookie proxy; verify cookies are `Secure`/`HttpOnly`/`SameSite` and the proxy overwrites XFF.
4. Production verification (checklist, record results in `docs/deploy-log.md`):
   - Idle check: no running ECS tasks, Aurora paused at 0 ACU, no NAT/ALB in the account.
   - Cold start: Lambda first request, Aurora resume, Fargate start to first progress event.
   - Real analysis of a small and a medium public repo; ZIP upload; chat first-token latency; PDF export.
   - **Guide quality in production**: run the rubric on both repos from the deployed system; every score within 0.3 of
     the phase 5 record; 100% of sampled citations resolve to the pinned commit and line range; no unsupported
     statement presented as fact; regenerate one chapter and confirm the others are unchanged; record guide tokens,
     seconds and cost against the model. Tracked in `docs/deploy-log.md`.
   - Failure drill: kill a task, force a DLQ message, confirm alarms fire.
   - Cleanup: expire a session and confirm S3, DB, vector, graph and DynamoDB entries are gone.
   - Cost: check Cost Explorer after 24-48 h against the phase 5 model.
5. Rollback ready: previous image tags and Terraform state versions kept; `terraform destroy` for dev documented; Vercel instant rollback.
6. Ship: tag `v1.0.0`, GitHub release notes, final `graphify update .`, update the status table, delete any remaining worktrees/branches.

Exit gate: all production verification items pass, including live guide quality; idle cost matches the model; release tagged and pushed. Product is done.

---

## Appendix A: Original candidate architecture (superseded by `docs/infra/strategy.md`; kept for reference)

Browser → Next.js (Vercel) → API Gateway HTTP API → Lambda (FastAPI + Mangum) → Aurora PostgreSQL Serverless v2 (0 ACU, auto-pause).
Upload → presigned S3 → job row → SQS → Step Functions Standard → ECS Fargate RunTask (analysis, graph, index, LLM, guide, PDF) → Postgres / S3 / indexes → frontend via short polling.

Replacements: Redis → SQS + DynamoDB + Postgres; MinIO → S3; arq → SQS/Step Functions (a refactor, not a drop-in); SSE → durable progress rows + polling; Qdrant → S3 Vectors only if evals pass; Neo4j → managed free tier first, no equivalence claim without evals.
Workers: start only for a real job, one bounded job each, checkpoint stages, persist before success, exit when done, bounded by concurrency, memory, time and LLM budget.
Security: never execute uploaded code; archive size/count/extraction/path-traversal limits; per-project and per-session isolation; session expiry with cleanup across every store; bounded retries with DLQ.
