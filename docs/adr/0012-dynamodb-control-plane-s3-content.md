# ADR 0012: DynamoDB control plane and S3 content; no relational database

Status: proposed (2026-10-10) · Phase 4 stage B

## Context
The code uses Postgres as a document store with parent-keyed partitions: single-row locks, one monotonic cursor,
unique keys, cascade delete, JSONB read and written whole, one join, no extensions, no aggregation
(`docs/infra/existing-architecture.md` §6). Aurora Serverless v2 supports min 0 ACU auto-pause on PostgreSQL >=
16.3 / 15.7 / 14.12 / 13.15 in us-east-1 and ap-south-1 with ~15 s resume (30 s or longer after 24 h), but it
requires a VPC, is blocked from pausing by any open connection or RDS Proxy, and its Data API has a 1 MiB response,
45 s timeout, no array parameters and no PostgreSQL 18 support; the Python path would not be asyncpg.

## Decision
Option B. One on-demand DynamoDB table (PK/SK, three sparse GSIs, TTL as backstop, deletion protection, PITR off)
holds sessions, projects, jobs, checkpoints, events, plan/sections/diagrams/summaries, quotas and LLM counters.
Chunks, IR, BM25 and dense artifacts live in S3. A `ControlStore` and `ChunkStore` protocol with a DynamoDB adapter
and the existing SQLAlchemy adapter (reference and local dev) replaces direct ORM use. The table design and the
17-row invariant mapping are in `docs/infra/architecture-decision.md` §4.4.

## Alternatives considered
- A. Aurora + DynamoDB: costs NAT or a driver rewrite plus a 15-30 s wake on the first request, and buys no SQL
  capability the code uses. Rejected.
- C. Postgres on a small always-on host (EC2 or RDS db.t4g.micro $11.68/$15.33 per month): zero code change,
  non-zero idle cost, API in a VPC. Documented fallback.

## Consequences
- Largest code change of phase 4 (about 12 files). FK integrity becomes explicit: conditional transactions plus an
  idempotent sweeper (invariant 8 is "partial").
- Per-event writes are transactional (counter + event + project liveness check), strictly ordered per project.
- DynamoDB TTL is never relied on (deletion "typically within a few days"; expired items stay readable).
- Reconsider Option C if the stage C contract suite cannot reach parity within the time box.
