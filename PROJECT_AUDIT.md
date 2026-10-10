# PROJECT_AUDIT.md — Unfold, Phase 0

Date: 2026-10-10 · Repo: `C:\dev\unfold` (single commit `3a195f2`, branch `main`, clean, synced with `origin/main`) · Method: static reading by three read-only explorers + safe local checks (lint, types, unit tests, build). No code, config, plugin, branch, or cache was changed. **Evidence labels:** *Verified* = read in code or reproduced by a command; *Suspected* = inferred, not confirmed.

---

## 1. Project overview and actual architecture

Unfold is meant to take a GitHub URL or ZIP and produce a guide, architecture diagrams, and grounded chat about the repo (RAG + graph + LLM agents).

**What actually exists**

| Layer | State |
|---|---|
| `backend/src/unfold` FastAPI app | Runs. `create_app` in `api/main.py` builds a `Runtime` (Postgres, Redis, Neo4j, Qdrant, MinIO clients). |
| Library layers | Substantial real code: `repo_intel` (~8.8k lines, AST + tree-sitter), `project_ir`, `graph` (Neo4j), `indexing` (chunker, bm25s, fastembed, reranker), `retrieval` (planner, RRF, sufficiency), `context`, `diagrams`, `exports`, `llm` (Gemini/Groq gateway, key pool, budget, circuit breaker). |
| Orchestration (the glue) | **Absent.** Only `orchestration/progress.py` (130 lines). |
| `frontend` Next.js 16 / React 19 / Tailwind 4 | Builds. Pages for landing, progress, guide, architecture, chat; streaming API proxy. |
| Infra | `docker-compose.yml` (5 datastores) and `docker/backend.Dockerfile`. No CI, no Terraform, nothing deployed. |

**Net:** a well-built parts bin with a thin API on top. No path turns an uploaded repo into a result.

**Docs vs reality.** `docs/HLD.md` says it "describes the system as built". It does not: LangGraph checkpointing, Deep Agents, claim verification, the worker, chat, exports and evaluation do not exist. There is no top-level README. Root `CLAUDE.md` and `AGENTS.md` are identical 781-byte graphify stubs; the real operating manual is `backend/CLAUDE.md`.

---

## 2. Feature matrix

| Feature | Status | Evidence |
|---|---|---|
| Anonymous cookie sessions (hashed token) | **Complete** | `api/routes/sessions.py` |
| GitHub URL / ZIP intake, project + job rows | **Complete** | `api/routes/projects.py`, `ingestion/` |
| SSE progress (Redis stream replay) | **Complete** (no producer) | `GET /projects/{id}/events`; nothing publishes events because no worker runs |
| `ingestion/service.py` (`acquire_github/zip`) | **Unused** | no caller |
| Worker (`arq`) | **Broken** | Dockerfile `CMD arq unfold.worker.settings.WorkerSettings`; `worker/settings.py` does not exist; tasks `run_analysis`, `run_export`, `cleanup_project` are only enqueued as strings |
| LangGraph pipeline / checkpoints | **Missing** | `langgraph`, `langgraph-checkpoint-postgres`, `deepagents` in `pyproject.toml`, imported nowhere |
| Repo analysis, IR, graph, index, retrieval, context, diagrams, exports libs | **Partial** (implemented + unit-tested, never invoked by the app) | no composition-root caller |
| LLM gateway | **Partial** | `Runtime.llm` is always `None`; `build_gateway` never called by `api/main.py` |
| Guide generation | **Missing** | `guide/` holds models only (no planner/writer/verifier) |
| Chat | **Missing backend**, frontend exists | `chat/` empty, no `/chats` routes, yet `chat-page.tsx` and `guide-page.tsx` call for them |
| Exports (PDF/MD) | **Missing routes** | renderer exists, no routes; `ArtifactRow/GuideSectionRow/DiagramRow` never written |
| Evaluation | **Missing** | `evaluation/` empty; poe `eval-*` tasks and `scripts/check.py` reference it |
| Stale-job sweeper / cleanup | **Missing** | `cleanup/` empty |
| MCP | **Missing** | no code (not in the HLD's critical path either) |
| Mocks | None found in production paths | consistent with the no-mocks rule in `backend/CLAUDE.md` |

---

## 3. Functional and API audit

Live routes (`/api/v1`): `POST /sessions`, `GET /sessions/current`, `POST /projects/github`, `POST /projects/zip`, `GET /projects/{id}`, `DELETE /projects/{id}`, `GET /projects/{id}/events`; ops `/health`, `/ready`.

End-to-end trace (frontend → API → backend → DB → agents → RAG → output):
`landing page → /api/[...path] proxy → POST /projects/* → Postgres project/job rows + arq enqueue → [nothing consumes the queue]`.
A user can create a project and watch an SSE stream that never emits. The guide, architecture and chat pages have no backing endpoints.

**F-1 (Critical, verified) — The pipeline is not wired.** Missing: worker settings and tasks, orchestration graph, guide writer, chat, export routes. Fix: build the minimum vertical slice first (see §12). Verify: submit a small public repo and receive a guide section through the UI.

**F-2 (Medium, verified) — Frontend calls endpoints that do not exist.** Chat/guide/architecture pages target routes absent from the API. Fix: define the OpenAPI contract first, then generate or hand-write the client. Verify: contract test hitting every client path.

**F-3 (Low, verified) — Config mismatch:** `.env.example` `PUBLIC_WEB_ORIGIN` defaults to `:3000`; the web app runs on `:3010`. Local CORS breaks outside production.

---

## 4. Security findings

Strengths (verified): hashed session tokens (`secrets.token_urlsafe(32)` → SHA-256), 404 on foreign project ids, GitHub SSRF allow-list enforced on every request including redirects (`ingestion/github.py`), hardened archive extraction with tests (`ingestion/archive.py`, `tests/security/test_archive.py`), `SecretStr` for keys, secret redaction in `llm/gateway.py`, DOMPurify before `dangerouslySetInnerHTML`, docs/OpenAPI off in production, generic 500 handler.

| ID | Sev | Status | Finding | Files | Fix | Validation |
|---|---|---|---|---|---|---|
| S-1 | High | Verified | `POST /sessions` is unauthenticated and unlimited; the 10/hour project limit is per session, so new sessions bypass it. No per-IP limit. | `api/routes/sessions.py`, rate-limit code in `projects.py` | Per-IP + global limits on session creation and project creation; CAPTCHA/PoW if public | Script 50 sessions × 10 projects → 429 |
| S-2 | High | Verified | `uvicorn --forwarded-allow-ips "*"` plus proxy forwards client `x-forwarded-for` → spoofable client IP (defeats any IP limit). | `docker/backend.Dockerfile`, `api/main.py` `run`, `frontend/src/app/api/[...path]/route.ts` | Set trusted proxy IPs only; proxy strips/overwrites XFF | Request with forged XFF is not honoured |
| S-3 | Medium | Verified | `cookie_secure=False` default; dev credentials hard-coded as settings defaults with no production guard. | `core/config.py`, `docker-compose.yml` | Fail startup in `APP_ENV=production` if secure=False or default creds | Startup test |
| S-4 | Medium | Verified | Rate limit `INCR`+`EXPIRE` non-atomic; a crash leaves a key that never expires. | rate-limit helper | Lua script or `SET NX EX` + `INCR` | Unit test with fault injection |
| S-5 | Medium | Verified | SSE streams live up to 1 h with no per-session concurrency cap. | `projects.py` events route | Cap open streams per session/IP | Open N+1 streams → rejected |
| S-6 | Medium | Verified | No CSP, HSTS, or Permissions-Policy (only nosniff, Referrer-Policy, X-Frame-Options). | `frontend/next.config.ts` | Add CSP (nonce-based; Mermaid/Shiki output is sanitised), HSTS | securityheaders scan |
| S-7 | Low | Verified | `X-Unfold-Session` header accepted as cookie fallback widens token exposure (logs, scripts). | `api/deps.py` `session_token_from` | Remove unless a client needs it | Test removal |
| S-8 | Low | Verified | Archive ratio check starts above 10 MB; many small bombs stay under it (bounded by 300 MB extracted cap). | `ingestion/archive.py` | Acceptable; note only | — |
| S-9 | Info | Suspected | Prompt-injection defences (scanner labels only; fence tags) are untested end-to-end because no consumer exists. Re-audit once the writer/chat exist. | `security/injection.py`, `security/fence.py` | Add injection fixtures to evals | Eval pass rate |

AI-specific: no MCP surface, no agent write actions, no tool permissions to abuse yet (those risks are *deferred, not absent*). RAG poisoning relies on fencing + redaction; verify with adversarial repos (e.g. a README saying "ignore previous instructions") once chat exists. Human-approval gates are N/A for a read-only analysis product.

Dependencies: no audit was run (`pip-audit`/`npm audit` not executed). Recommend adding them to CI.

---

## 5. AI, agents and RAG assessment

- **Models:** Gemini + Groq via a custom gateway (key pool, per-model budget, circuit breaker, cache, schema coercion). Good engineering; unused. Gemini availability notes are in memory (`gemini-models.md`): 2.5 retired for new users, Pro has no free quota.
- **Retrieval:** hybrid lexical (bm25s) + dense (fastembed/Qdrant) + graph (Neo4j) with RRF and a sufficiency check. Tested in isolation; never run on a real corpus in-app.
- **Missing:** context propagation (nothing passes IR → retrieval → writer), memory/checkpoints, tracing (OTEL deps present, no spans wired), evals (`evaluation/` empty), fallbacks beyond provider failover, token-cost reporting.
- **Skeptical take:** `langgraph`, `langgraph-checkpoint-postgres`, `deepagents` add weight and failure modes. A guide is a deterministic pipeline (ingest → analyse → index → plan sections → write each with retrieved evidence → verify → persist) that fits a plain arq task with per-step Postgres progress rows. **Recommendation: build the pipeline as plain async functions in arq; adopt LangGraph only if resumability proves necessary; drop `deepagents` and MCP.** This removes ~3 dependencies and the largest unknown.
- **Cost control:** `ANALYSIS_LLM_CALL_BUDGET=150` exists; enforce it in the gateway on a per-job basis and log tokens per section.

---

## 6. Infrastructure and cloud-cost assessment

**Verified deployed:** nothing. Vercel account has no project matching "unfold". Render, Cloudflare, GCP and AWS were **not** checked (no access performed) — treat as unknown, confirm manually.

**Defined in code only**
- `docker-compose.yml`: postgres 16, redis 7, neo4j 5, qdrant 1.19, minio; memory limits total ~3.2 GB; default passwords; host ports published (local only). **No api, worker, or web service**, contradicting `.env.example` comments.
- `docker/backend.Dockerfile`: multi-stage `api`/`worker`, non-root uid 10001, Chromium + prefetched models in worker; worker `CMD` is broken (§2).
- `frontend/vercel.json`: framework/build only; `output: "standalone"` set but no frontend Dockerfile.
- No Terraform, no `.github/workflows`; HLD §129 says deferred.

**Cost view.** Today: $0. Realistic future: Neo4j + Qdrant need ~3 GB RAM → one small VM (≈$25–40/mo) beats managed services at this stage. Cheapest sane topology: Vercel (web) + one VM running compose (api, worker, 5 stores) with Caddy TLS; LLM spend capped by free-tier keys + the call budget. Risks: Vercel function duration/bandwidth for 50 MiB uploads and long SSE → point uploads/SSE at the API origin directly or use a streaming-friendly host. Don't buy managed Neo4j/Qdrant until traffic exists.

---

## 7. Frontend and UX assessment

**Verified:** `npm run build` succeeds (routes `/`, `/projects/[projectId]`, `.../architecture`, `.../chat`, `.../guide`, `/api/[...path]`); `tsc --noEmit` clean.

| ID | Sev | Status | Finding |
|---|---|---|---|
| U-1 | Medium | Verified | `npm run lint` fails: 3 × `react-hooks/set-state-in-effect` (`chat-page.tsx:38`, `guide-page.tsx:48`, +1) with `--max-warnings=0`. |
| U-2 | Medium | Verified | `frontend/tests/` is empty; `npm test` exits 1 ("No test files found"). |
| U-3 | Medium | Verified | No `error.tsx` / `not-found.tsx` at app level (build lists `/_not-found` default only), nothing in `public/`, no OG/icon assets. |
| U-4 | Medium | Suspected | Mobile: 264 px sidebar + 340 px rail with no verified small-screen layout; React Flow canvas likely lacks a keyboard alternative; no verified skip link or `prefers-reduced-motion` handling beyond `globals.css`. Needs a real browser pass. |
| U-5 | Low | Verified | Tokens in `globals.css` (ink/paper/signal etc., light/dark, AA contrast fixes) are a solid base; fonts Fraunces / Space Grotesk / IBM Plex Mono are distinctive. Primitives under `components/ui` (Radix) are consistent. |

**Versus OpenAI/Claude/Linear/Vercel:** the token foundation is good; polish gaps are empty/error/loading states, density, keyboard navigation, and motion restraint. Because the guide content does not exist yet, the real UX can't be judged — do UI polish after the vertical slice works.

**Design tooling:** `frontend-design` and `impeccable` skills are already installed (see session context) — enough. Don't add more UI plugins.

---

## 8. Testing and production readiness

**Run results (this audit)**

| Check | Result |
|---|---|
| `pytest` (backend, default = unit/non-docker) | **129 passed** (a Python traceback prints at interpreter exit; cosmetic, investigate) |
| `ruff check src` | **5 errors** (SIM114, SIM110, SIM102 ×2, RUF034) in `repo_intel/analyzers/{python,treesitter}.py` |
| `mypy` (strict) | **4 errors** in `python.py:915`, `treesitter.py:2359,2361,3981` |
| `npm run lint` | **fail** (3 errors) |
| `npm run typecheck` | pass |
| `npm test` | **fail** (no tests) |
| `npm run build` | pass |

Therefore `scripts/check.py` (the only gate) is **red** on a fresh checkout. There is no CI, so nothing catches this.

Coverage: 15 backend test files (~141 tests): 10 unit, 1 security, 4 integration (docker-gated and deselected by default). No e2e despite `e2e` markers and LLD §293. No Playwright. No AI evals. No tests for chat/guide/exports/worker because they do not exist. `test_architecture.py` enforcing import rules is a good practice.

Readiness gaps: no CI, no deploy config, no structured monitoring wiring, no backup/recovery story for Postgres/Neo4j/Qdrant, no job-timeout/stale-job sweeper, no load or abuse tests.

**Recommended E2E journeys (Playwright):** (1) paste small public repo → progress → guide → diagram; (2) ZIP upload; (3) chat question with cited evidence; (4) expired session; (5) hostile ZIP / bad URL error states; (6) PDF export.

---

## 9. Claude Code plugins and configuration

**Current state (verified):** `.claude/` holds only `settings.json` + `settings.local.json`. No skills, subagents, commands, or `.mcp.json`. `settings.json` has two PreToolUse hooks running `graphify hook-guard` on Bash|Grep and Read|Glob, which inject a "run graphify query first" reminder on every such call. `graphify-out/` is 27 MB (3 files tracked: `graph.json`, `GRAPH_REPORT.md`, etc.; caches ignored). Installed in the user environment (from session memory/context): caveman, ponytail, graphify, claude-security, claude-mem, frontend-design, impeccable, superpowers, ECC, agent-skills, code-review.

**Recommendations (nothing installed in this audit)**

| Tool | Verdict | Why / caveat |
|---|---|---|
| Graphify | Keep, loosen | Useful for Q&A about structure. Make the hook advisory for Read/Glob (it is noise on ops tasks); keep `graphify update .` after code changes. Not yet verified against upstream docs in this audit. |
| Caveman / Ponytail | Keep if wanted | Output/coding style modifiers. They stack with the Concise output style — pick one voice to avoid conflicting instructions. |
| claude-security | Use at P0 | Run `/claude-security` scan after the pipeline lands (the surface is small today). Token-heavy. |
| Code review (`/code-review`, ECC reviewers) | Keep | Enough; don't add a second reviewer plugin. |
| Playwright | Add as **dev dependency + project config** (`@playwright/test`), not necessarily an MCP | Journeys in §8 need repeatable tests, not an interactive MCP. `chrome-devtools` MCP is already present for ad-hoc checks. |
| Frontend design | `frontend-design` + `impeccable` already enough | Avoid adding more. |
| Others (ECC has ~300 skills) | Prune | Skill-listing overhead is itself context cost; see §10. |

Conflicts to watch: three overlapping output-style plugins (caveman, ponytail, Concise); overlapping review skills (ECC, agent-skills, code-review); graphify hook vs. ad-hoc tool use. *I did not web-verify upstream plugin versions or compatibility in this audit; do that before installing anything.*

---

## 10. Context-rot prevention strategy

1. **CLAUDE.md = permanent rules only (<80 lines).** Replace the graphify stub with: stack, run/test commands (`uv run poe …`, `npm run …`), architecture one-paragraph + link, conventions, "what exists vs planned" (point to this audit's §2), cleanup rules (§11). Make `AGENTS.md` a one-line pointer to `CLAUDE.md`, not a duplicate. Keep `backend/CLAUDE.md` as the backend manual.
2. **Progressive disclosure.** `docs/HLD.md` / `LLD.md` stay out of context by default; add a `docs/STATUS.md` (short: built / not built / next) that CLAUDE.md links to. Fix HLD's "as built" claim so docs stop contradicting code.
3. **Targeted reads.** Search/graphify first, then read ranges; never open `treesitter.py` (~4k lines) or `gateway.py` (723 lines) whole.
4. **Subagents for isolation.** Explore agents for searches, one reviewer per diff, security auditor per milestone; require ≤300-word, file:line-evidenced reports.
5. **Checkpoints.** One task per branch/PR; at each milestone write a 10-line handoff in `docs/STATUS.md`; `/clear` between tasks. Record decisions as short ADRs in `docs/adr/` (e.g. "plain arq pipeline, no Deep Agents").
6. **Trim plugin surface.** Disable unused ECC/agent-skills skills per project via settings so the skill list doesn't burn tokens every turn.
7. **Validation gates.** `scripts/check.py` green + one Playwright journey before merging each milestone.

---

## 11. Git worktree, RAM, disk and workspace plan

**Inventory (verified, read-only)**
- Worktrees: **one** — main checkout `C:/dev/unfold`, branch `main` @ `3a195f2`. `.claude/worktrees` does not exist.
- Branches: only `main` and `origin/main` (up to date). Stashes: none. Working tree clean. Remote: `https://github.com/udarshmarthala/unfold.git`. Unique unpushed commits: none.
- **Conclusion: no stale/abandoned worktrees or branches; nothing to remove.** No `git worktree remove/prune` is needed.

**Disk**

| Path | Size | Safe to delete? |
|---|---|---|
| `frontend/node_modules` | 718 MB | Regenerable (`npm ci`); keep while developing |
| `backend/.venv` | 482 MB | Regenerable (`uv sync`); keep |
| `frontend/.next` | 362 MB | Safe (build cache); delete after builds if space is needed |
| `graphify-out/` | 27 MB (cache ~5.5 MB, dated dir ~9.9 MB gitignored) | Cache subfolders safe; keep committed `graph.json`/report |
| `.ruff_cache`, `.codex`, `__pycache__` | tiny | Safe |

Proposed cleanup (**needs your approval; not run**):
```
rm -rf frontend/.next                 # ~362 MB, rebuilt by npm run build
rm -rf graphify-out/cache graphify-out/20??-*   # gitignored caches
find . -name __pycache__ -prune -exec rm -rf {} +
git status --short                    # confirm still clean
```

**RAM vs disk.** Live processes: `claude` ×7 ≈ 1.9 GB, `node` ×14 ≈ 813 MB; no python/docker running. Deleting files frees disk only; RAM is freed by closing the sessions/dev servers holding it. Review the 7 `claude` processes (likely several sessions + plugin helpers incl. claude-mem) and close unused ones; I did not inspect per-process details.

**Permanent CLAUDE.md rules (proposed)**
- Work in the main checkout; create a worktree only when asked, max 2 at once, under `.claude/worktrees/`.
- Never remove a worktree/branch without checking `git status`, `git branch --merged main`, and `git log origin/main..branch`; never force-remove or hand-edit `.git/worktrees`.
- Don't commit build output or caches; keep dev servers to one frontend + one API, stopped at session end.
- Run `git worktree list` and `git status` at session start and end.

---

## 12. Prioritized implementation roadmap

**P0 — make the product exist (vertical slice, smallest working version)**
1. Fix the gate: ruff (5), mypy (4), eslint (3), add a minimal vitest setup/test, so `scripts/check.py` is green. Add GitHub Actions running it. *Verify: CI green on a clean checkout.*
2. Worker: create `worker/settings.py` with `WorkerSettings`; implement `run_analysis` as a plain async pipeline: `acquire → repo_intel → project_ir → graph → index → plan sections → write (retrieval + gateway) → persist artifacts`, emitting progress events to the existing SSE stream; build the gateway in the worker (`llm/factory.py`). *Verify: small public repo yields guide rows + SSE events end to end.*
3. Read routes for guide/diagrams/artifacts; align frontend client to the real contract. *Verify: guide and architecture pages render real data.*
4. Security fixes S-1…S-3, S-5, S-6 (session/IP limits, trusted proxy, prod guards, SSE cap, CSP/HSTS).

**P1 — complete features and prove them**
5. Chat backend (`/chats`, retrieval-grounded answers with evidence refs), `cleanup_project` + stale-job sweeper, export routes.
6. `evaluation/` with offline evals (groundedness, retrieval hit rate, injection fixtures); wire OTEL spans and per-job token accounting.
7. Playwright journeys from §8 in CI; frontend error/not-found/loading states.
8. Update `docs/HLD.md` to reality; rewrite CLAUDE.md per §10; drop `deepagents` (and `langgraph*` unless used).

**P2 — ship and polish**
9. Containerise `api`/`worker`/`web`; single-VM compose + TLS; Vercel for web; backups for Postgres/Neo4j/Qdrant; `pip-audit` / `npm audit` in CI. Terraform only if you outgrow one VM.
10. UI polish against Linear/Vercel standard: mobile layout, keyboard-accessible diagram, reduced motion, empty/error states, OG/icons.
11. Run `/claude-security` full scan + manual prompt-injection red-team of the chat path.

---

### Not verified in this audit
Other cloud accounts (Render, Cloudflare, GCP, AWS); dependency vulnerability scans; backend integration tests (need docker); real-browser accessibility/responsiveness; `repo_intel`/`llm` internals; the actual `.env` contents (deliberately not read); upstream plugin documentation and versions.

**Stopping here for your approval before any implementation or cleanup.**
