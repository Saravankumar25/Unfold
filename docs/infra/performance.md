# Unfold performance: worker profile, API latency, sizing (phase 5, measured locally)

Everything here was measured on one laptop with no AWS call. **A laptop is not Fargate or Lambda**: read the numbers
as shapes, ratios and upper-bound sanity checks. What needs a real account stays listed under "Phase 6" at the end.

## 1. Method and machine

| Item | Value |
|---|---|
| Machine | Windows 11, Intel Core Ultra 5 125H (14 cores / 18 threads), 16 GB RAM with 0.9 to 2.5 GB free |
| Load | **Shared with other agents** (builds, tests, Terraform): system CPU was 20 to 70 % during the runs (recorded per run as `system_cpu_percent_during_run`). Absolute times are pessimistic and noisy; the spread between repeated runs is shown, not hidden |
| Profile | The AWS profile in one process: DynamoDB and S3 over moto, embedded dense artifact, in-memory graph, counter-store LLM coordination. This is what the Fargate task runs. Moto adds a few tens of MB and some CPU |
| CPU | Worker runs pinned to the first 4 logical CPUs (`--cpus 4`, a Fargate vCPU is one hardware thread). API runs pinned to 2 (a 3 538 MB Lambda gets about 2 vCPU) |
| Models | Scripted model (`unfold.evaluation.scripted.ScriptedLLM`: real prompts, canned answers; calls and prompt characters are meaningful, tokens and LLM seconds are not) and the real Gemini gateway (`gemini-3.5-flash-lite` for critique, `gemini-3.5-flash` for plan, write, revise; free tier) |
| Inputs | Fixture repositories under `evals/fixtures/repos` (largest: 33 files, about 100 chunks) and, as the large input, this repository's own `backend/src/unfold` (184 files, **2 464 chunks**; parsed only, never executed) |
| Tools | `scripts/profile_worker.py` (stages from the pipeline's own spans, RSS sampled every 20 ms, process CPU seconds, LLM calls/tokens/seconds by purpose and by chapter, retrieval and flow-trace latency, DynamoDB request counts) and `scripts/profile_api.py` (real localhost HTTP, non-LLM endpoints and chat). Both only wrap functions; nothing under `backend/src` changed. `uv run --with psutil python ../scripts/profile_worker.py <fixture\|dir> out.json [--cpus 4] [--llm gemini]` |
| Settings | Defaults of `Settings` (`MODEL_THREADS=6`, `SECTION_CONCURRENCY=2`) unless a row says otherwise |

## 2. Worker: wall time per stage (seconds, scripted model, 4 pinned CPUs)

| Stage | cli_tool (15 chunks) | fastapi_rag (102 chunks) run 3 | same, `MODEL_THREADS=4` | unfold/src (2 464 chunks) |
|---|---|---|---|---|
| analyze (parse, IR) | 0.4 | 0.7 | 1.6 | 24.4 |
| graph, lexical | 0.2 | 0.3 | 0.4 | 1.5 |
| dense (embedding) | 5.7 | 25.3 | 17.2 | **1 196** |
| subsystems, plan | 0.4 | 0.5 | 0.5 | 3.4 |
| research (retrieval and trace) | 18.1 | 72.6 | 43.2 | 367 |
| sections, verify, visuals | 2.4 | 6.3 | 7.3 | 5.3 |
| **Total** | **27.8** | **105.9** | **71.5** | **1 599** |

Whole-run spread on `fastapi_rag` (102 chunks), same code, 4 CPUs: 226 s (system load 71 %), 115 s, 106 s, 134 s
(unpinned), and **79 s and 71 s with `MODEL_THREADS=2` and `4`**. Other fixtures: `express_ts_prisma` 155 s (99 chunks),
`terraform_k8s` 192 s (95 chunks), `multi_module` 38 s (8 chunks); the first two ran under heavy load.

**Finding 1: ONNX thread oversubscription is the largest lever.** The default `MODEL_THREADS=6`, two chapters in
flight and 4 vCPU means up to 12 busy threads on 4 CPUs. Isolated, one cross-encoder rerank of 30 passages
(the `rerank_top` of every retrieval) took 9.2 s with the library default, 6.3 s with 6 threads, **2.0 s with 4 threads**
on 4 CPUs (2.9 s with 2 CPUs and 2 threads). In the pipeline, setting `MODEL_THREADS=4` (or 2) cut a
102-chunk run from 106 to 115 s to 71 to 79 s, retrieval p50 from 8 to 9 s to 5 s, and dense from 25 to 17 s. Recommended
for the worker task environment: **`MODEL_THREADS=4`** (the vCPU count); for the API function **`MODEL_THREADS=2`**.
Whether Fargate's `os.cpu_count()` shows the task or the host is unknown (phase 6), so set it explicitly.

**Finding 2: the 1 800 s job timeout is close for a large repository on a loaded 4-CPU machine.** 2 464 chunks took
1 599 s with default threads and no LLM wait. Dense embedding is linear in chunks (about 2 chunks/s here, 6 chunks/s measured
earlier on a quiet machine). At the 5 000 chunk cap that is 800 to 2 400 s for embedding alone, plus the model calls.
Until a Fargate run says otherwise: either `MAX_CHUNKS_PER_PROJECT` about 2 500 or `JOB_TIMEOUT_SECONDS` 3 600
(a retry resumes from stage checkpoints but not from inside the dense stage).

## 3. Worker: memory and CPU

| Input | Chunks | Peak RSS | Peak by stage | Process CPU seconds | Average cores busy |
|---|---|---|---|---|---|
| cli_tool | 15 | 517 MB | dense 333, research 515 | 85 | 3.0 |
| multi_module | 8 | 527 MB | | 104 | 2.8 |
| fastapi_rag (3 runs, default threads) | 102 | 1.06 to 1.33 GB | dense 717, research 1 056 to 1 320 | 273 to 351 | 1.2 (loaded) to 3.1 |
| fastapi_rag `MODEL_THREADS=4` / `2` | 102 | 1.32 / 1.43 GB | | 227 / 179 | 3.2 / 2.3 |
| terraform_k8s | 95 | 1.55 GB | research 1 552 | 247 | 1.3 |
| unfold/src | 2 464 | **2.73 GB** | dense 2 590, research 2 726, writing 1 621 | 3 164 | 2.0 |

The idle process (imports, runtime, moto) is about 150 MB. Memory grows with chunks at about 0.8 MB per chunk during
dense and the research stage adds 0.3 to 0.7 GB (reranker session, retrieval working sets). Extrapolated to the 5 000 chunk cap:
about 4.6 to 5.3 GB. The Windows "peak commit" figure (6.4 GB for the large run) counts reserved address space, not RAM,
and is not comparable to a Linux cgroup; Linux `memory.peak` must be read on Fargate (phase 6). An earlier run of a 2 424-chunk input on this laptop reported a 3.5 GB working set (other machine state), so size against 3.5 GB at 2 500 chunks and about 7 GB at the cap if the lower slope does not hold.

### Fargate size

| Option | Verdict |
|---|---|
| 2 vCPU / 4 to 8 GB | wall about 1.5x longer (isolated rerank 2.9 s on 2 CPUs against 2.0 s on 4), similar vCPU-seconds per job, and 4 GB is too small beyond about 3 000 chunks |
| **4 vCPU / 8 GB (current default)** | **keep.** Average parallelism is 2.0 to 3.2 busy cores, so 4 vCPU is used; 8 GB is 3x a typical repository and about 1.1 to 1.7x the extrapolated worst case (5 to 7 GB at 5 000 chunks), which is why the chunk cap of 2 500 in finding 2 also protects memory |
| 8 vCPU / 16 GB | not usable: the default Fargate On-Demand quota is 6 vCPU, and cost per job rises with sub-linear speedup |

`analysis_cpu = 4096`, `analysis_memory = 8192`, 20 GB ephemeral (default) are supported. Add `MODEL_THREADS=4` to the
analysis task environment, and decide the chunk cap or timeout as in finding 2. Nothing in Terraform was edited here.

## 4. Worker: model calls, tokens and seconds (real Gemini, `cli_tool`, one run)

15 chunks, 4 chapters, one attempt, 129 s wall (a 27.8 s scripted run of the same input: the other 100 s is model latency).
13 calls, **17.3k input and 9.3k output tokens**, 5 calls retried, 1 provider rate-limit event.

| Role (purpose) | Calls | Tokens in / out | Call latency p50 / p95 | Seconds in calls |
|---|---|---|---|---|
| planner (`guide.plan`) | 1 | 896 / 650 | 10.0 s | 10.0 |
| investigator (`guide.research`) | 0 | 0 | none: the plan supplied the questions, retrieval is local | 0 (chapter research 0.0 to 8.4 s wall) |
| writer (`guide.section`) | 4 | 7 957 / 6 167 | 13.4 / 49.1 s | 80.6 |
| reviser: critique (`guide.critique`) | 4 | 3 799 / 242 | 2.8 / 2.9 s | 8.9 |
| reviser: rewrite (`guide.revise`) | 4 | 4 617 / 2 262 | 8.2 / 16.0 s | 31.4 |

Per chapter (calls, tokens in / out): `overview` 3, 4 159 / 1 682; `usage` 3, 4 029 / 2 497; `config-deploy` 3, 3 325 / 1 257;
`internals` 3, 4 860 / 3 235 (its writer call took 49 s). Chapter wall: writer 5.6 to 49 s, reviser 9.4 to 24 s.

Earlier real runs on `fastapi_rag` (`operations.md` section 7, same engine): 19 and 20 calls, 41.1k / 11.8k and
42.6k / 12.3k tokens, 287 and 400 s with two rate-limit waits. For the large input the scripted prompts total 157k
characters over 17 calls; at 3.8 characters per token (calibrated on the real run: 65.6k characters, 17.3k tokens) that
is about 41k input tokens before rewrites, so a large guide is estimated at **55k input and 18k output tokens**
(derived, not run: a real model run on 2 464 chunks was not made to protect the free-tier quota).

| Guide | Calls | Tokens in / out | Source |
|---|---|---|---|
| small (15 chunks) | 13 | 17k / 9k | measured |
| typical (about 100 chunks) | 19 to 20 | 42k / 12k | measured, two runs |
| large (about 2 500 chunks) | about 25 | about 55k / 18k | derived |

The configured ceilings (150 calls, 1.5 M tokens per job) are 6x to 8x and 27x above the largest figure; they are
a runaway guard, not a budget. A cap of 60 calls and 300k tokens would still clear every measured guide by 3x or more.

## 5. Retrieval and flow trace

Retrieval is one `HybridRetriever.retrieve` call (lexical, dense query, structural, graph, fuse, cross-encoder rerank,
expand). Seconds per call, from the spans, scripted model, 4 CPUs:

| Run | n | p50 | p95 | rerank p50 | other components p50 |
|---|---|---|---|---|---|
| fastapi_rag, default threads | 16 | 7.9 to 9.1 | 11.0 to 11.5 | 7.5 to 8.9 | dense 0.2 to 0.35, graph 0.01 to 0.1, lexical 0.03 to 0.17 |
| fastapi_rag, `MODEL_THREADS=4` / `2` | 16 | 5.3 / 5.1 | 6.2 / 5.6 | 5.0 / 4.9 | dense 0.09 to 0.18 |
| cli_tool | 10 | 2.9 | 4.0 | 2.4 | |
| unfold/src (2 464 chunks) | 30 | 21.0 | 36.6 | 19.9 | dense 0.4, lexical 0.03 |

The rerank is 85 to 95 % of every retrieval and does not depend on repository size except through the 30-passage cap
(the large run's higher figure is CPU contention with 2 concurrent chapters on 4 CPUs during a 26-minute run). The flow
trace (`trace_flow`, a graph walk) is negligible: p50 under 0.1 ms, p95 at most 4 ms, max 21 ms (8 to 22 calls per run).

## 6. API

### 6.1 Non-LLM endpoints (real localhost HTTP, uvicorn plus httpx, moto stores, 2 pinned CPUs, a finished 102-chunk guide, n = 100 unless noted)

| Endpoint | p50 ms | p95 ms | DynamoDB requests | RRU / WRU per call |
|---|---|---|---|---|
| `GET /health` | 2.8 | 4.2 | 0 | 0 / 0 |
| `GET /sessions/current` | 62 | 89 | 3 | 2.5 / 0 |
| `GET /projects/{id}` | 60 | 89 | 3 | 3 / 0 |
| `GET /projects/{id}/progress` (the poll) | 151 | 206 | 5 | 7 / 0 |
| `GET /projects/{id}/guide` | 446 | 695 | 4 | 27 / 0 |
| `GET /guide/sections/{key}` | 161 | 223 | 4 | 11 / 0 |
| `GET /evidence/{chunk}` | 32 | 49 | 2 | 2 / 0 |
| `GET /architecture` (n = 50) | 285 | 484 | 4 | 18 / 0 |
| `POST /sessions/current/touch` | 27 | 38 | 3 | 2 / 1 |
| `POST /projects/{id}/chats` (n = 25) | 121 | 533 | 4 | 3 / 8 |

Moto costs tens of milliseconds per DynamoDB call (a status poll is 5 calls), so these are upper bounds for the handler
and adapter layer and a regression guard (the earlier in-process run measured the same shapes). Network, S3 and DynamoDB
service latency are not in them.

### 6.2 Chat first token (22 questions scripted; 3 real Gemini, each asked streamed and as JSON)

| Model | First `response.delta` p50 / p95 | Full stream p50 / p95 | Server retrieval p50 |
|---|---|---|---|
| scripted (n = 22) | 3.96 s / 5.40 s | 4.09 s / 5.59 s | 3.86 s |
| real Gemini (`gemini-3.5-flash`, n = 3) | **6.6 s** / 6.9 s (max) | 10.2 s / 12.1 s | 3.2 s |

First token is dominated by retrieval (cross-encoder rerank on 2 CPUs), then the provider's first token (about 3.4 s
here). A turn costs 16.5 DynamoDB requests, about 15 WRU and 13 RRU. The numbers exclude the models' first load in a
fresh process: loaded from disk, the reranker took 4.2 to 8.2 s and the embedder about 5 s, so **the first chat after an
API cold start pays roughly 10 s more**. Suggested budget until measured on Lambda: first token p95 under 10 s; the lever is
`rerank_top` (30), lowering it cuts first token roughly in proportion.

### 6.3 Cold start proxy (API image dependency set: `uv sync --no-default-groups --group ai`, 218 MB of site-packages on disk)

| Measure | Result |
|---|---|
| `python -X importtime -c "import unfold.api.aws"` (the Lambda entry) | 7.3 to 8.0 s (first run 14 s), 1 049 modules; on the development venv the same import took 5.4 to 6.6 s at the same time |
| Process start to first `/health` 200 (`uvicorn unfold.api.aws:create_aws_app --factory`, 5 runs) | 12.4 to 13.6 s |
| Modules loaded | `bm25s`, `numpy`, `boto3` present; `onnxruntime`, `fastembed`, `networkx`, `minio`, `redis` not loaded at import |
| Image size | `api-lambda` 991 MB uncompressed, 312 MB gzip proxy (`operations.md` section 3, measured before networkx moved into the `ai` group) |

The same import measured 1.1 to 1.4 s earlier in phase 5 and 4.6 s `docker run` to `/health` on Linux; today's values are
4 to 6x higher with `fastapi` alone at 2.1 s, which points at the Windows file system and the machine load, not at the application code. Treat 7 s as a pessimistic Windows bound; **the Lambda cold start
(image pull, extension, imports on Linux) must be measured on AWS**.

## 7. DynamoDB request profile (current pipeline, counted at the client)

| Run | Requests | Estimated WRU | Estimated RRU |
|---|---|---|---|
| cli_tool, scripted | 213 | 302 | 145 |
| fastapi_rag, scripted (3 runs) | 262 to 268 | about 600 | 262 to 265 |
| fastapi_rag, real Gemini retries (cli_tool) | 345 | 536 | 211 |
| unfold/src (2 464 chunks) | 403 | 665 | 313 |

This replaces the earlier 258 requests, 350 WRU, 190 RRU: the 21-stage pipeline writes more (briefs, per-chapter
verification, progress events). Requests grow with chapters and events, not with chunks (chunks are one S3 object).
Polling adds 7 RRU per `/progress` call (240 polls per job is 1 680 RRU). All counts include creating the session and
project rows; units are estimated from payload size, not billed.

## 8. Phase 6 (requires AWS, not claimed)

Lambda cold start p50/p95/p99 and the first chat after it; Function URL streaming through Vercel; Fargate image pull,
task start and `os.cpu_count()`; Fargate memory (`memory.peak`) and wall clock of the same inputs, arm64 viability;
whether the Xeon/Graviton vCPU is slower than this laptop's thread (assumed 1.5x in `cost.md`); the actual `RunTask`
quota; export task time on Fargate; real DynamoDB and S3 latency; Cost Explorer against `cost.md`.
