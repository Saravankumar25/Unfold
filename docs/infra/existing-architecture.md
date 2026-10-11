# Unfold: verified existing architecture and dependency map

Status: phase 4 stage A, written 2026-10-10 against `main` at `accfc01` (worktree `phase-4-aws`).
Method: code read with `path:line` evidence, plus real measurements on this machine. Documentation (HLD, LLD,
PROJECT_AUDIT) was treated as a claim to check, not as proof; section 9 lists where it is wrong.

Conventions
- `file:line` paths are relative to the repository root. Backend paths drop the prefix `backend/src/unfold/`.
- **[planned]** marks things the sibling phase 3 worktree is building (chat with a `stream=false|true` switch,
  the `export` worker job type with `JobQueue.enqueue_export(export_id)`, a durable cleanup sweeper and a `sweep`
  task, evals, frontend polling). They do **not** exist on `main` and nothing here is verified for them.
- "Measured" means obtained on this machine on 2026-10-10 (Windows 11, Intel Core Ultra 5 125H, 14 cores / 18
  threads, 15.4 GB RAM, local docker stores). Numbers will differ on Fargate and Lambda.

## 1. Component inventory (what actually runs)

| Component | Where | Notes |
|---|---|---|
| Web | `frontend/` Next.js 16 / React 19, `output: "standalone"` (`frontend/next.config.ts:37`) | Planned host: Vercel. Same-origin proxy route handler `frontend/src/app/api/[...path]/route.ts` |
| API | `api/main.py` FastAPI factory `create_app` | Routes: sessions, projects, guide (`api/routes/*.py`). No chat, no export routes |
| Worker | `worker/run.py` one-shot: `python -m unfold.worker.run analysis <job_id>` or `cleanup <project_id>` (`worker/run.py:84`) | One job per process, exit 0 done, 75 retry (`orchestration/pipeline.py:71`) |
| Local "queue" | `worker/queue.py` `SubprocessJobQueue` | Spawns a worker subprocess per job from inside the API process; bounded by `max_concurrent_jobs=2` (`core/config.py:118`) |
| Postgres 16 | `persistence/postgres/*`, SQLAlchemy 2 async + asyncpg | System of record for everything relational |
| Redis 7 | `persistence/redis_store.py`, `llm/*`, `indexing/dense.py` | Limiter, LLM coordination, caches. Not a queue (arq is gone) |
| Neo4j 5 | `persistence/neo4j_store.py`, `graph/*` | Code graph, optional (`_optional("graph")`, `orchestration/pipeline.py:308`) |
| Qdrant 1.19 | `persistence/qdrant_store.py`, `indexing/dense.py` | Dense vectors, optional (`orchestration/pipeline.py:328`) |
| MinIO | `persistence/object_store.py` | Uploads, IR snapshot, BM25 artifact |
| Models (local) | fastembed ONNX: `BAAI/bge-small-en-v1.5` (embedder), `Xenova/ms-marco-MiniLM-L-6-v2` (reranker) (`core/config.py:100-101`) | CPU inference, loaded lazily per process |
| LLM providers | Gemini (3 tiers), Groq fallback, via `llm/gateway.py` | Only two call sites exist: `guide/generate.py:216` (plan) and `:325` (section) |

## 2. Verified request flows

### 2.1 Session creation (`POST /api/v1/sessions`)
1. `api/routes/sessions.py:32` `create_session` calls `enforce_ip_limits` (`api/deps.py:111`): two Redis fixed-window
   hits, per IP (`max_sessions_per_ip_per_hour=20`) and global (`max_sessions_per_hour=500`), window 3600 s
   (`core/config.py:120-121`).
2. It mints a random token (`core/ids.new_session_token`), stores only `token_hash` in `sessions`
   (`api/routes/sessions.py:39-47`), and sets an HttpOnly, `SameSite=Lax`, `Secure` (config) cookie
   `unfold_session` (`api/routes/sessions.py:19-28,48`).
3. `expires_at = now + session_ttl_seconds (86 400 s)`; hard cap `session_max_lifetime_seconds (259 200 s = 3
   days)` applied on touch (`api/routes/sessions.py:88-98`).

### 2.2 Authenticated request
`api/deps.py:59` `require_session`: cookie only (a header fallback was removed, audit S-7); one indexed `SELECT` on
`sessions.token_hash`; rejects non-`active` or expired rows by comparing `expires_at` to `now()` in application
code (so no background expiry job is needed for correctness). `owned_project` (`api/deps.py:84`) returns 404 for
foreign ids. Sliding touch is explicit (`POST /sessions/current/touch`) and takes a row lock
(`api/routes/sessions.py:93`).

### 2.3 Project creation, GitHub (`POST /api/v1/projects/github`)
`api/routes/projects.py:148`: `parse_github_url` is syntax only (no network in the API). `_enforce_creation_limits`
(`:83`): per-session limit (`max_projects_per_hour=10`) plus per-IP (30) and global (200) per hour. `_create`
(`:91`) in one transaction inserts `projects` and an `analysis_jobs` row (`status=queued`), then
`rt.progress.publish(...RECEIVED)` (`:137`), then `queue.enqueue_analysis(job_id, 1)` (`:140`). If enqueue raises, the
error is logged and the job stays `queued` for the recovery sweep (`:141-143`). The GitHub fetch itself happens
in the worker (`ingestion/github.py`: REST API plus codeload tarball over `httpx`, hosts allow-listed to
`api.github.com` and `codeload.github.com`, no git binary).

### 2.4 Project creation, ZIP (`POST /api/v1/projects/zip`)
`api/routes/projects.py:169`: the **API process receives the multipart body**, streams it to a temp file in 1 MiB
chunks, enforces `max_zip_bytes` = 50 MiB (`:178-192`, `core/config.py:107`), checks the ZIP magic, hashes it, then
`rt.objects.put_file("projects/{id}/upload/source.zip")` (`:107-108`) and runs the same `_create`. Archive
inspection (zip bomb, traversal, symlinks, file count, ratio) is in the worker (`ingestion/archive.py:180`
`safe_extract`, limits `core/config.py:107-113`). The browser sends the file through the Next proxy, which streams
request bodies with `duplex: "half"` (`frontend/src/app/api/[...path]/route.ts:5,62-70`).

### 2.5 Job dispatch and recovery (local)
- `SubprocessJobQueue._spawn` is idempotent per key (`worker/queue.py:50-52`). `_supervise` (`:57`) runs the worker
  process, hard-kills it after `job_timeout_seconds + 60` (`:62-73`), retries exit 75 after `min(30, 5*attempt)` s
  and any other exit after `CRASH_RETRY_SECONDS = ALIVE_WITHIN + 10 = 130 s` (`:24`), up to `max_job_attempts=3`, then
  `fail_abandoned_job` (`worker/recovery.py:43`) records a terminal failure so polling ends.
- The durable queue is the `analysis_jobs` row. `worker/recovery.py:23` `requeue_incomplete_jobs` re-enqueues
  every `queued` job and every `running` job whose heartbeat is older than 5 minutes. It runs **only at API
  start** (`api/main.py:41`); nothing schedules it. **[planned]** phase 3 adds a scheduled sweeper.
- Claim is a row lock: `orchestration/progress.py:57-81` `start()` does `SELECT ... FOR UPDATE`
  (`progress.py:53`), refuses a `succeeded` job or a `running` job whose heartbeat is younger than `ALIVE_WITHIN`
  (2 min, `pipeline.py:73`), increments `attempt`, stamps `heartbeat_at`. Two workers started together cannot both
  own a job.

### 2.6 Worker pipeline (`orchestration/pipeline.py`)
`run_analysis` (`:91`) claims the job, starts a 30 s heartbeat task (`:118`, `HEARTBEAT_SECONDS` `:72`), wraps the
body in `asyncio.timeout(job_timeout_seconds=1800)` (`:119`), and records success only after everything is
persisted. A process-level watchdog `os._exit(75)` fires at `job_timeout + 30 s` because in-thread parsing cannot
be cancelled (`worker/run.py:50-53,61`). Nine checkpointed stage groups (`pipeline.py:181-191`; the checkpoint
write is `:208-211`; a rerun skips groups present in `job_checkpoints`, `:194`):

| Group | What it does | Reads | Writes | Heavy resource |
|---|---|---|---|---|
| analyze (`:239`) | ingest, security scan, inventory, tree-sitter/Python analysis, IR build, chunking | GitHub or blob `upload/source.zip` | blob `ir/<ver>.json.gz`; Postgres `chunks`, `projects` (row lock `:284`) | Temp dir (`tempfile`, `work_dir`), CPU in threads, untrusted bytes parsed (never executed) |
| graph (`:299`) | write IR to Neo4j | blob IR | Neo4j nodes/rels | network; **optional**: failure records `degraded=graph` |
| lexical (`:310`) | BM25 build | Postgres `chunks` | blob `lexical/<ver>.bin`; `projects.lexical_object_key` | CPU |
| dense (`:320`) | fastembed embedding of every chunk, Qdrant upsert, Redis embedding cache | Postgres `chunks` | Qdrant points | **CPU and memory dominant**; **optional** |
| subsystems (`:330`) | community detection (networkx), summaries, set subsystem ids | IR | `summaries`, `chunks.subsystem_id`, Neo4j, Qdrant payload | CPU |
| plan (`:402`) | 1 LLM call | IR, summaries | `guide_plans`, `guide_sections` (pending rows) | LLM |
| sections (`:444`) | per section: hybrid retrieval, LLM write; `section_concurrency=2` | BM25 blob, Qdrant, Neo4j, Postgres chunks | `guide_sections` (row lock) | LLM, local reranker |
| verify (`:527`) | deterministic claim grading, no model call | `guide_sections`, `chunks` | `guide_sections` | none |
| visuals (`:558`) | deterministic diagrams from IR, attach to sections | IR, summaries | `diagrams`, `guide_sections` | CPU |

Retrieval inside the worker (`retrieval/hybrid.py`): BM25 (blob) + dense (Qdrant) + graph (Neo4j) + structural (IR),
weighted RRF, cross-encoder rerank top 30, up to two sufficiency follow-up rounds, graph expansion.

### 2.7 Progress polling
`GET /projects/{id}/progress?after=<seq>` (`api/routes/projects.py:245`): `owned_project` + `read_after`
(`persistence/postgres/progress.py:75-85`, `id > after ORDER BY id LIMIT 200`) + `latest_job`. No Redis, no SSE, no
worker contact. This is a pure database read, so it can never wake a worker.

### 2.8 Guide and diagram reads
`api/routes/guide.py`: `GET /{id}/guide` (`:71`), `/guide/sections/{key}` (`:101`), `/evidence/{chunk_id}` (`:140`),
`/diagrams/{id}` (`:164`), `/architecture` (`:174`). All read what the worker persisted; "nothing here calls a model"
(`api/routes/guide.py:3-4`). They need Postgres and the `ChunkRepository`, and `load_ir` for architecture (blob).

### 2.9 Delete and cleanup
`DELETE /projects/{id}` (`api/routes/projects.py:235`) only enqueues `cleanup:<project_id>`. The worker's `cleanup`
task runs `cleanup/service.py:21`: blob prefix delete (`:28`), Qdrant delete by filter, Neo4j delete, progress delete,
then `DELETE FROM projects` which cascades to every child table (`:33-35`). It is idempotent and ordered so a
failure leaves the project row to retry. **Not done today:** nothing selects expired sessions or projects, so
expiry does not remove data (section 9). Redis keys are never deleted by cleanup (`emb:*`, `llm:cache:*`,
`usage:*` die by TTL only).

### 2.10 Planned (phase 3 worktree, not on `main`) **[planned]**
- Chat: `POST /chats/{id}/messages` with a `stream=false|true` switch; retrieval then a streamed or buffered LLM
  call. `max_chat_messages_per_minute=30` exists in config (`core/config.py:115`) but nothing uses it;
  `LLMGateway.stream` (`llm/gateway.py:561`) has no caller; the frontend already calls `/chats`
  (`frontend/src/lib/api/endpoints.ts:69-77`).
- Export: `export` job type, `JobQueue.enqueue_export(export_id)`, Chromium PDF in a worker. The renderer exists
  and is unwired (`exports/pdf.py`, `PdfRenderer`; `worker/run.py:84` accepts only `analysis|cleanup`).
- Sweeper: scheduled `sweep` task for expired sessions/projects and stale jobs.

## 3. Persistent stores and their real purpose

### 3.1 Postgres (16 tables, 10 have a writer) - `persistence/postgres/models.py`

| Table | Purpose | Written by | Read by | Replaceable by |
|---|---|---|---|---|
| `sessions` (`:44`) | anonymous session, token hash, expiry | `api/routes/sessions.py:39-47,93` | `api/deps.py:65`, sessions route | key-value (DynamoDB) |
| `projects` (`:54`) | one analysed input, ownership, S3 keys, stats | `api/routes/projects.py:112-125`, `pipeline.py:283-297,315-318` | every route, worker | key-value |
| `analysis_jobs` (`:78`) | job state machine, heartbeat, attempt, error, stage JSONB | `orchestration/progress.py`, `projects.py:127-134` | polling, recovery | key-value with conditional update |
| `job_checkpoints` (`:275`) | completed stage groups per job | `pipeline.py:208-211` (merge) | `pipeline.py:176-180` | key-value |
| `job_events` (`:262`) | durable progress log, BIGSERIAL cursor | `persistence/postgres/progress.py:23-33` | `projects.py:245` | key-value + atomic counter |
| `chunks` (`:110`) | evidence text and metadata, 1 row per chunk | `indexing/store.py:77-80,146-156` | retrieval hydrate, guide evidence, BM25 build | object store (whole-project blob) |
| `summaries` (`:130`) | project/subsystem facts | `pipeline.py:361-363` | `pipeline.py:393` | key-value |
| `guide_plans` (`:160`), `guide_sections` (`:170`) | guide structure and content | `pipeline.py:417-435,483-505,549-554,589-604` | guide routes | key-value |
| `diagrams` (`:189`) | diagram spec + Mermaid | `pipeline.py:565-580` | guide routes | key-value |
| `source_files`, `findings`, `chat_sessions`, `chat_messages`, `artifacts`, `eval_runs` | **no writer on `main`** (chat/export/eval tables prepared for phase 3) | - | `chat_sessions` only read in `api/deps.py:94-97` | n/a |

Measured sizes for the 33-file fixture `fastapi_rag` (102 chunks, 5 sections): `chunks` 75 KB total (max row
~1 KB), `guide_sections` 20.8 KB total (max row 4.9 KB), `diagrams` 15.6 KB (max 4.5 KB), `summaries` 3.5 KB,
`job_events` 17 rows / 5.1 KB. Every row is far below DynamoDB's 400 KB item limit; a project this size is about
0.12 MB of relational data plus 59 KB of blobs. The 175-file medium input produced 2 424 chunks (about 1.8 MB of
chunk text by extrapolation from 32.7 KB per 102 chunks: UNMEASURED for that run).

### 3.2 Redis (ephemeral; full inventory)

| Key | Type / TTL | Purpose | Atomic op | Critical? |
|---|---|---|---|---|
| `rl:{bucket}:{session}`, `rl:{bucket}:ip:{ip}`, `rl:{bucket}:global` | STRING counter / window s | fixed-window rate limits | Lua INCR+EXPIRE (`persistence/redis_store.py:15-20`), fails closed 503 (`:58`) | **Security** |
| `llm:budget:{model}[:k{i}]:rpm\|tpm` | ZSET / 120 s | sliding request and token windows against the provider quota | Lua `_RESERVE`/`_COMMIT` (`llm/budget.py:38-105`) | quota protection |
| `...:rpd:{utc_day}` | STRING / 2 d | requests per day | `_RESERVE` INCR, `_RELEASE` DECR (`budget.py:108-117`) | quota protection |
| `...:conc`, `...:cooldown` | ZSET lease, STRING | concurrency slots, 429 cooldown | `_RESERVE`, `_COOLDOWN` (`budget.py:120-129`) | resilience |
| `llm:circuit:{provider}:{model}` | HASH / 24 h | circuit breaker (5 failures, 60 s open, single probe) | Lua (`llm/circuit.py:23-70`) | optimisation |
| `llm:key:{provider}:{idx}` | HASH / 7 d | key pool health, LRU, disabled-until | Lua `_ACQUIRE` (`llm/key_pool.py:19-41`) | convenience |
| `usage:{job_id}` | HASH / 7 d | **per-job LLM call and token budget** (150 calls, x1.3 hard, 1.5 M tokens) and accounting | Lua `_ADMIT` (`llm/gateway.py:70-79`) | **Cost cap** |
| `llm:cache:{sha256}` | STRING / 24 h | response cache; only `plan_guide` is cacheable (`guide/generate.py:212`) | GET/SET | cache |
| `emb:{sha1}` | STRING 1.5 KB / 7 d | embedding cache | MGET + pipeline SET (`indexing/dense.py:123-135`) | cache |

Redis is not used for progress, queueing, locking or pub/sub (grep: no `SET NX`, streams, lists). The API process
uses it only for the limiter and `/ready` (`api/main.py:69`). The gateway is built in the worker only
(`worker/run.py:72`); `Runtime.llm` is `None` in the API. Redis commands used: INCR, EXPIRE/PEXPIRE, TTL, TIME,
GET, SET EX/PX, DECR, MGET, ZADD, ZREM, ZREMRANGEBYSCORE, ZCARD, ZRANGE WITHSCORES, HGET, HSET, HMGET, HINCRBY,
HGETALL, SCAN+DEL, plus 7 Lua scripts. Redis `TIME` is the clock authority for budget/circuit/key-pool logic.

### 3.3 Neo4j
Written by `graph/builder.py` (`write` `:235`, `set_subsystem` `:414`, `delete_project` `:427`), read by
`graph/queries.py` (`GraphQueries`: `get_node`, `find_entities` with a fulltext index `entity_name_ft`
(`graph/builder.py:32,229`), `neighbors` with variable-length paths, `callers/callees`, `path_between`
(`shortestPath`), `subsystem_members`, `endpoint_flows`, `overview`, `module_dependencies`). Used by retrieval only
(graph seeds, graph expansion, graph paths). **Diagrams and subsystem detection do not read Neo4j**: they use the IR
and networkx (`graph/communities.py:16`, `diagrams/builders.py`). The graph is deterministic from the IR. Measured:
169 nodes and 335 relationships for the 33-file fixture, 2 175 nodes for the 175-file medium input. Graph failure is
already a supported degradation (`orchestration/pipeline.py:308,381`; `HybridRetriever` accepts `graph=None`,
`retrieval/hybrid.py:96,110`).

### 3.4 Qdrant
One shared collection per embedding model, projects separated by a `project_id` payload filter
(`persistence/qdrant_store.py:12-16`); payload indexes `project_id, content_type, language, subsystem_id`
(`indexing/dense.py:26`); `search` takes optional `content_types` and `subsystem_ids` (`:156-175`). Vectors are
384-d float32 (bge-small). 2 424 vectors = 3.7 MB.

### 3.5 Object store (MinIO now, S3 later)
Keys: `projects/{id}/upload/source.zip`, `projects/{id}/ir/<ANALYSIS_VERSION>.json.gz`,
`projects/{id}/lexical/<ver>.bin` (`ports.project_prefix`, `indexing/lexical.py:36`, `project_ir/snapshot.py:45`).
Measured for the fixture: IR 17.6 KB, BM25 24.4 KB, upload 17.1 KB. **[planned]** exports will add PDF/Markdown
objects. The adapter wraps the synchronous MinIO SDK in threads (`persistence/object_store.py:237-245`).

## 4. Long-running, CPU, memory and filesystem-heavy operations

| Operation | Where | Nature | Constraint for Lambda/Fargate |
|---|---|---|---|
| Archive download and extraction (up to 100 MB tarball, 300 MB extracted, 20 000 files) | worker `ingestion/*` | disk I/O in a temp dir, CPU in a thread | needs writable scratch: 300 MB extracted + archive; Lambda `/tmp` 512 MB default (10 GB max), Fargate 20 GiB included |
| Inventory, tree-sitter analysis, IR | worker threads | CPU, native parser (see finding F-2) | grammar libraries are downloaded to a cache dir at first use (`repo_intel/analyzers/treesitter.py:339-341`); the container prefetches 15 (`docker/backend.Dockerfile:53`) |
| Embedding | worker `indexing/embeddings.py` | ONNX CPU inference, batch 64 | **dominant time and memory**, see measurements |
| Reranking | worker today, chat tomorrow | ONNX CPU, 16 pairs per batch | needs the 80 MB model in the process that serves chat |
| LLM calls | worker (and chat **[planned]**) | network, 3-6 s each measured | `llm_request_timeout_seconds=120` (`core/config.py:97`) |
| Whole job | worker | up to `job_timeout_seconds=1800` | far beyond API Gateway 29/30 s and near Lambda's 15 min |
| ZIP upload through the API | API | 50 MiB stream to temp file | Lambda payload 6 MB, Vercel function body 4.5 MB (see decision doc) |
| PDF render **[planned wiring]** | `exports/pdf.py` | Chromium launch, ~270-430 MB browser | not in an API function; Fargate or a dedicated image |

### 4.1 Measured worker runs (real stack, real Gemini calls)
Harness: a script (kept in the session scratchpad, not in the repo) seeds a project exactly like
`POST /projects/zip` creates it, runs `python -m unfold.worker.run analysis <job_id>` as a subprocess, samples the
process tree's OS-tracked peak working set and peak commit, and reads the job row. Data stores: the local docker
compose stack (Postgres, Redis, Neo4j, Qdrant, MinIO).

| Run | Input | Result | Wall | Peak working set | Peak commit (private) |
|---|---|---|---|---|---|
| 1 (cold: model download) | fixture `fastapi_rag`, 33 files, 17 KB zip | succeeded, 102 chunks, 4 sections, 6 diagrams | 92.1 s | not captured (the venv launcher process was sampled, bug in my harness) | not captured |
| 2 (warm) | same | succeeded, 5 LLM calls | 37.1 s | 929 MB | 1 582 MB |
| 3 (warm) | same | succeeded, 5 sections | 32.5 s | 1 053 MB | 2 045 MB |
| 4 (warm) | medium input: this repo's `backend/src`, `backend/tests`, `docs`, `docker` (175 files, 1.47 MB zip) | succeeded, **2 424 chunks**, 5 sections, 6 diagrams, 2 175 graph nodes, 6 LLM calls | **461.2 s** | **3 515 MB** | **4 902 MB** |

Stage timing from the log for run 2 (relative to `job.started`): analyze+IR 0.9 s, graph 0.7 s, lexical 0.4 s,
dense 0.6 s, subsystems 0.5 s, plan 3.1 s, sections 21 s (4 retrievals 4.7-6.3 s each, 4 LLM calls 2.4-6.0 s each),
verify+visuals <0.3 s; total body 27.5 s, so about 9.6 s of the 37.1 s wall is interpreter start, imports and
runtime construction on this machine (an `import unfold.worker.run` alone took 8.2 s here, measured). Stage timing
for run 4: analyze+IR 10.7 s, graph 14.3 s, lexical 1.2 s, **dense 395 s (about 6.1 chunks/s)**, plan+sections
31 s. Embedding 2 424 chunks took 395 s here, which is 86 % of the job and the reason peak memory reached 3.5 GB.
These numbers are from a 14-core laptop with local stores and are **not** a Fargate prediction; the Fargate benchmark
plan is in `architecture-decision.md` section 11.

LLM behaviour observed: the first Gemini key list entry index 3 returned `UNAUTHENTICATED` and was disabled by the
key pool (`llm.key_disabled` log), the run continued on the remaining keys. All calls used `gemini-3.5-flash-lite`.

## 5. Configuration, environment and secrets

- One `Settings` object (`core/config.py:54`), `.env` and `../.env` plus process environment, `lru_cache`d
  (`:188`). Secrets are `SecretStr`: `GEMINI_API_KEY_1..4`, `GROQ_API_KEY_1..4`, `GITHUB_TOKEN`, `NEO4J_PASSWORD`,
  `OBJECT_STORE_SECRET_KEY`, and the password inside `DATABASE_URL` (`core/config.py:41-51,80-87`).
- Production guard (`core/config.py:142-161`): refuses `COOKIE_SECURE=false`, dev Neo4j/MinIO/Postgres credentials, or
  `TRUSTED_PROXY_IPS` containing `*` or a /0.
- Non-secret knobs relevant to infrastructure: `DATABASE_URL`, `REDIS_URL`, `NEO4J_URI`, `QDRANT_URL`,
  `OBJECT_STORE_*`, `MODEL_CACHE_DIR`, `WORK_DIR`, `OTEL_EXPORTER_OTLP_ENDPOINT`, all limits in section 6 of the
  decision doc.
- `TRUSTED_PROXY_IPS` makes uvicorn's `ProxyHeadersMiddleware` honour `X-Forwarded-For` only from listed peers
  (`api/main.py:100-102`); the Next proxy sets `X-Forwarded-For` to the Vercel platform header
  (`frontend/src/lib/client-address.ts`). **A Vercel to AWS hop has no fixed peer address**, so this mechanism cannot
  be reused unchanged (decision doc, topic 9).
- No secret is read from a file other than `.env`; there is no secret store integration, no `boto3`.

## 6. Relational features actually used (input to the Aurora decision)

| Feature | Evidence | Needed? |
|---|---|---|
| Transactions spanning several statements | create project+job (`projects.py:109-134`), replace chunks (`indexing/store.py:77-80`), plan+sections (`pipeline.py:417-435`), diagrams+section attach (`pipeline.py:565-604`) | yes, but each is "replace one project's set of rows" |
| Row locks `SELECT ... FOR UPDATE` | job claim and every job mutation (`orchestration/progress.py:53`), session touch (`sessions.py:93`), project and section updates (`pipeline.py:284,316,488,499,550,594`) | yes: job claim is the one true mutual exclusion |
| Foreign keys with `ON DELETE CASCADE` | sessions -> projects -> 8 child tables, chat_messages -> chat_sessions (`models.py:38-41,58,98,133,...`) | cleanup relies on it (`cleanup/service.py:33-35`) |
| Unique constraints | `sessions.token_hash`, `(project_id, section_key)` (`models.py:47,172`), `(chat_session_id, seq)` (`:218`) | yes (lookup + idempotent upsert) |
| Sequence / BIGSERIAL | `job_events.id` as the replay cursor (`models.py:267`) | yes: monotonic cursor per project |
| JSONB | every JSON column, read and written whole; no JSONB operators in any query | storage only |
| Upsert | ORM `merge` (SELECT then INSERT/UPDATE), no `ON CONFLICT`, no `RETURNING` | no |
| Joins / aggregation / FTS / LIKE | exactly one join (`api/routes/sessions.py:56-62`); no aggregation, no LIKE, no FTS, no extensions | no |
| Checkpoints | table `job_checkpoints`, not LangGraph (no `langgraph*`, no psycopg anywhere in `src`, `migrations`, `pyproject.toml`) | yes (rerun skip) |
| Isolation | default READ COMMITTED, `expire_on_commit=False` (`persistence/postgres/db.py:20`) | no stronger isolation relied upon |
| Connections | async engine, pool 10 (worker 4, `worker/run.py:65`), `pool_pre_ping`, `pool_recycle=1800` | per-process pools |

Conclusion for the data decision: the schema is a document store with parent-keyed partitions. The only two
guarantees that need more than a key lookup are the job claim (single-item conditional update) and the cascade
delete (delete by partition). No query needs cross-partition SQL.

## 7. Dependency map and Lambda compatibility

Direct runtime dependencies from `backend/pyproject.toml:10-41`, resolved in `backend/uv.lock` (99 packages in the
closure, 144.6 MB of manylinux x86_64 cp312 wheels). Installed size measured from `backend/.venv` (Windows wheels,
so Linux `.so` sizes differ) and from `dist-info` RECORD sums.

| Dependency | Imported by | Native? | Used by | Lambda / compatibility implication |
|---|---|---|---|---|
| fastapi 0.141.1, pydantic 2.13, pydantic-settings, starlette | `api/*`, `core/config.py` | pydantic-core (Rust) | API | fine; needs an ASGI adapter |
| uvicorn[standard] 0.53 | `api/main.py:12,106` | uvloop, httptools | API | Lambda Web Adapter runs it; Mangum would not |
| sqlalchemy 2.0.54, asyncpg 0.31, alembic 1.20 | `persistence/postgres/*`, `migrations/` | asyncpg (C) | API+worker | drop-in only with a reachable Postgres; see decision doc |
| redis 5.3.1 | `persistence/redis_store.py`, `llm/{budget,circuit,key_pool}.py` | no | API+worker | goes away with the Redis removal |
| minio 7.2.20 | `persistence/object_store.py` | pycryptodome | API+worker | replace by `boto3` S3 adapter |
| qdrant-client 1.19.1 | `indexing/dense.py`, `persistence/qdrant_store.py` | grpcio, protobuf | API+worker | imports fastembed itself (`qdrant_client/fastembed_common.py`, try/except) |
| neo4j 6.3.1 | `persistence/neo4j_store.py` | no | API+worker | needs a bolt endpoint |
| fastembed 0.8.0 | lazy: `indexing/embeddings.py:50,66`, `indexing/reranker.py:46` | onnxruntime (23.6 MB wheel, ~41 MB installed), pillow, tokenizers, hf-xet | worker, chat **[planned]** | heavy: ~124 MB installed plus 152 MB models; needed in any process that embeds a query or reranks |
| bm25s 0.3.11, numpy 2.5.3 (undeclared direct) | `indexing/lexical.py:19-20` | numpy (~42 MB installed incl. libs) | worker + any retrieval | fine |
| networkx 3.7 | `graph/communities.py:16` | no | worker | 7 MB |
| tree-sitter 0.26, tree-sitter-language-pack 1.20 | `repo_intel/analyzers/treesitter.py:38-39` | Rust abi3; **grammars downloaded lazily into a user cache** (58 MB for 15 libs measured) | worker | needs `XDG_CACHE_HOME` and prefetch (the Dockerfile does); read-only HOME on Lambda would fail; finding F-2 |
| python-hcl2 | lazy `repo_intel/analyzers/config.py:284` | no | worker | |
| google-genai 2.24, httpx 0.28, cryptography | `llm/providers/gemini.py:15-18`, `groq.py:13`, `ingestion/github.py:14` | cryptography (Rust) | worker, chat **[planned]** | 18 MB |
| playwright 1.63 | `exports/pdf.py:26-28` | Node driver 103 MB; Chromium 433 MB + headless shell 271 MB measured on disk (shared dir) | export only | must not be in the API image |
| jinja2, pyyaml, orjson, structlog, opentelemetry-sdk + otlp exporter (grpcio 12.7 MB) | various | yes (small) | all | grpcio comes from the OTLP gRPC exporter |
| opentelemetry-instrumentation-fastapi, sse-starlette, tomli-w | **no import found** | - | nobody | dead dependencies (`pyproject.toml:35,37`) |

Sizes: whole runtime closure about 341 MB installed (326 MB without pywin32), of which "API core" (fastapi, sqlalchemy,
asyncpg, redis, pydantic, otel, httpx, minio...) is about 54 MB and the worker-only remainder about 288 MB
(playwright 110, fastembed tree 124, qdrant-client 18, google-genai tree 18, networkx 7, tree-sitter pack 6).
Models on disk: 152 MB (`.model-cache`).

**API import weight (measured):** importing `unfold.api.main` loads fastembed, onnxruntime, networkx, bm25s,
tree-sitter, qdrant-client, neo4j, grpc and numpy, 2 096 modules, 5.6-14.0 s wall across three runs on this Windows
host. The causes are `api/main.py:22` (`worker.queue` -> `orchestration.pipeline` for two constants, `worker/queue.py:21`)
and `api/deps.py` -> `runtime.py:13-18` (constructs every store). `playwright`, `jinja2`, `google.genai` are not
loaded. The API therefore cannot be a lean function today; it can be made lean by import hygiene alone (no
behaviour change).

Container build (`docker/backend.Dockerfile`): one multi-stage file, `uv sync --frozen --no-dev` for the whole closure
into both targets; `api` target (`:40-46`) includes the 152 MB models and the playwright Python package but no
browser; `worker` target (`:49-56`) adds `playwright install --with-deps chromium` and 15 grammars, entrypoint
`python -m unfold.worker.run`. Its header says the worker also handles export and maintenance; `worker/run.py:84`
does not. No local `unfold*` images exist, so image sizes are **not measured**.

CI (`.github/workflows/ci.yml`): jobs `backend` (ruff, format, mypy, pytest), `integration` (compose up postgres redis
neo4j qdrant minio, alembic, pytest -m integration), `frontend` (lint, typecheck, vitest, build), `audit` (pip-audit,
npm audit). It does not build the images and does not run evals (`scripts/check.py:41` skips a missing
`evaluation/offline.py`). There is no Terraform in the repository.

## 8. Findings from this audit (not previously recorded)

| # | Finding | Evidence | Impact |
|---|---|---|---|
| F-1 | `analysis_jobs.stage_progress.stages` stops updating after the second stage: `stage()` does `dict(row.stage_progress)` (shallow copy) then mutates the shared nested `stages` dict, so SQLAlchemy sees no change and skips the UPDATE | `orchestration/progress.py:91-95`; measured: all runs show only `VALIDATING_INPUT` and `SECURITY_SCANNING` in `stage_progress.stages` although 15 stages ran | per-stage timestamps are unreliable; any port of this code must not copy the pattern |
| F-2 | **Native crash (access violation, exit 0xC0000005, no Python traceback from the app) in tree-sitter on TypeScript input**, reproducible and deterministic: `frontend/src/lib/api/sse.ts` of this repo, stack in `TreeSitterAnalyzer.variables` at `repo_intel/analyzers/treesitter.py:1539` (`child_by_field_name`). A second TS file also crashed after the first was removed; the three fixtures (including `express_ts_prisma`) analyse fine | reproduced twice on Windows with tree-sitter 0.26.0 / language-pack 1.20.0; **not tested on Linux** | one bad file kills the worker process; the existing exit-code retry would repeat the same crash up to `max_job_attempts` and then record `ANALYSIS_STAGE_FAILED`. Must be reproduced on the Linux image before sizing/retry policy is trusted; consider running analysis in a subprocess per language or catching via `faulthandler` + per-file isolation |
| F-3 | Embedding throughput and memory are the sizing driver: 2 424 chunks took 395 s and a 3.5 GB peak working set | run 4 above | Fargate size and job duration depend on chunks, not files |
| F-4 | A rejected Gemini key (`UNAUTHENTICATED`) is silently disabled and the job proceeds; there is no operator-visible signal beyond one log line | run 1 log | credential lifecycle needs an alert (decision doc topic 11) |
| F-5 | Cleanup never removes expired data: no code selects `sessions.expires_at < now` (`sessions.expires_at` is indexed, `models.py:50`, but no query uses it) | grep over `src` | PRD "data expires" is unenforced until the **[planned]** sweeper lands |
| F-6 | `requeue_incomplete_jobs` re-enqueues every `queued` job at API start, including jobs the supervisor already holds; idempotency is by the in-process `_tasks` dict only | `worker/queue.py:50-52`, `api/main.py:41` | with Lambda this runs on every cold start in every instance; must move to a scheduled, idempotent sweeper |
| F-7 | Redis outage inside the worker raises raw Redis exceptions through the LLM gateway (no `StoreUnavailable` mapping in `llm/`) | grep `RedisError` only in `persistence/redis_store.py` | irrelevant once Redis is removed; relevant for the replacement store's error mapping |
| F-8 | Local `docker compose` Redis is `noeviction` at 256 MB; an unguarded embedding-cache write raises when full (`indexing/dense.py:131-135`) | `docker-compose.yml:26` | cache write failure fails the stage |
| F-9 | `Database` pool defaults assume a long-lived process (pool 10 + overflow 10 per instance) | `persistence/postgres/db.py:16-22` | per-Lambda-instance pools multiply connections; irrelevant if Postgres is dropped |

## 9. Documented but not implemented

| Claim (doc) | Reality |
|---|---|
| HLD "as built"; LangGraph, Deep Agents, LangChain adapter (`docs/HLD.md`, `docs/LLD.md`) | removed (ADR 0001); `agents/__init__.py` empty |
| arq worker, cron maintenance (HLD, LLD) | no arq; one-shot worker (ADR 0003) |
| SSE from Redis Streams, `GET /projects/{id}/events` | replaced by polling; `sse-starlette` still declared but unused |
| Chat service, `/chats`, memory (HLD, LLD) | `chat/__init__.py` empty; **[planned]** phase 3 |
| Exports, artifacts, export queue | renderer exists, unwired; `ArtifactRow` no writer; **[planned]** |
| Simplify / Deepen endpoints | absent; frontend calls them (`frontend/src/lib/api/endpoints.ts:56`) |
| TTL cleanup cron, stale-job sweep every 10 min, "also removes Redis keys" (LLD:262) | per-project cleanup only; no expiry sweep; Redis keys not removed |
| Enqueue failure marks job failed/retryable `QUEUE_UNAVAILABLE` (LLD:74) | logs and leaves `queued` (`projects.py:141-143`) |
| Evaluation: offline benchmark, `evals/baseline.json`, `poe eval-*` | `evaluation/__init__.py` empty; poe tasks point to missing modules; only fixtures exist. **There is no retrieval-quality baseline today** |
| Investigators, `INVESTIGATING_PROJECT`, `findings` | stage never entered; `FindingRow` no writer |
| LLM hierarchical summaries and community labelling (LLD:194-195) | only deterministic summaries; the only LLM call sites are plan and section |
| LLM claim verifier, repair, consistency pass | `grade_claims` is deterministic only (`guide/generate.py:370-420`) |
| `source_files` persisted in Postgres (D3) | no writer; IR snapshot is a blob |
| `GraphBuilder.write_inferred` | defined (`graph/builder.py:358`), no caller |
| FastAPI instrumented by OpenTelemetry | no instrumentation import |
| `tests/contract`, `tests/e2e` | only `unit`, `integration`, `security` |
| Production images in CI | CI does not build them; no `unfold-web` image |

## 10. Read-only AWS calls made in this stage

Exactly one: `aws sts get-caller-identity`. It returned `NoCredentials` ("Unable to locate credentials"). No other AWS
API was called (no Describe/List/Get), nothing was created, no `terraform` command was run. Prices come from the
public AWS Price List bulk files and public documentation (sources in `architecture-decision.md`), which are
unauthenticated HTTP downloads. Because no credentials exist, **no account is identified and none can be assumed to
be the project's deploy target.**
