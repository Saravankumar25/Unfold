# ADR 0002: Infrastructure ports with local adapters

Status: accepted (2026-10-10) · Phase 1

## Context
The AWS scale-to-zero target replaces Redis (queue, rate limits, progress streams) and MinIO. Callers were coupled to
`RedisStore`, arq and the MinIO client, so the swap would have been a rewrite.

## Decision
`unfold/ports.py` defines four protocols; callers depend on them only.

| Port | Local adapter | Phase 4 adapter |
|---|---|---|
| `BlobStore` | `persistence.object_store.MinioBlobStore` | S3 |
| `JobQueue` | `worker.queue.ArqJobQueue` | SQS + Step Functions |
| `RateLimiter` | `persistence.redis_store.RedisRateLimiter` (atomic Lua) | DynamoDB TTL items |
| `ProgressStore` | `persistence.postgres.progress.PostgresProgressStore` (`job_events`) | same (Postgres) |

`tests/unit/test_architecture.py::test_infrastructure_sdks_only_in_adapters` fails if `redis`, `arq` or `minio` is
imported anywhere else.

## Known exception
LLM coordination (`llm/budget.py`, `circuit.py`, `key_pool.py`, `_redis.py`) and the embedding cache
(`indexing/dense.py`) use Redis directly, as Lua scripts and `mget`. They are allow-listed in the architecture test.
Phase 4 decides: DynamoDB conditional writes, or in-task state (a one-shot worker serves one job, so per-job budget
needs no shared store; cross-job provider quota does).

## Consequences
- Progress is durable and replayable from Postgres; the Redis stream is gone.
- `Runtime.redis` remains for the cache and LLM coordination only; application code uses `rt.limiter`, `rt.progress`,
  `rt.objects`.
