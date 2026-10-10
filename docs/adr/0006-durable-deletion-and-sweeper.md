# ADR 0006: Deletion and expiry are durable (marker + sweeper)

Status: accepted (2026-10-10) · Phase 3

## Context
`DELETE /projects/{id}` only enqueued a cleanup in memory: an API restart lost it and the data lived on until TTLs.
Session expiry removed nothing at all. Correctness must not depend on TTLs.

## Decision
- Migration 0004 adds `projects.deleting_at` (indexed, as is `expires_at`). `DELETE` writes the marker, then
  enqueues; from the marker on every owner route answers 404 and listings omit the project.
- `cleanup.sweeper.sweep(rt, queue)` is the single idempotent entry point. One pass: expired sessions -> their
  projects marked; expired projects marked; every marked project cleaned (`cleanup_project`); sessions with nothing
  left removed with their rate-limit keys; stale analysis jobs requeued (not for deleting projects); stale exports
  restarted; expired artifacts deleted. Callers: API start-up and a `SWEEP_INTERVAL_SECONDS` loop (0 disables), and
  `python -m unfold.worker.run sweep` for an external scheduler.
- `cleanup_project` clears blobs (prefix), IR cache, Qdrant points, Neo4j nodes, progress rows, project-scoped
  rate-limit keys (`<bucket>:project:<id>`) and the chat/job usage keys, then deletes the project row (cascading
  every table). The row goes last so a failure leaves the marker for the next pass.
- A job with a fresh heartbeat defers cleanup (`{"deferred": 1}`): the analysis stops at its next stage boundary
  because the pipeline treats a marked project as removed, and the next pass finishes.

## Not covered
Per-IP and global rate-limit counters and the LLM response cache have no owner and expire by their own TTL (an hour
and a configured cache TTL). Neither holds anything a user could read back.

## Consequences
- Proven by integration tests that inventory every store before and after (Postgres tables, blobs, vectors, graph
  nodes, progress, Redis keys), including the "API restarted before the cleanup job ran" case.
- Phase 4 schedules `sweep` (EventBridge -> task); nothing else changes.
