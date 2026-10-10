# ADR 0003: One-shot worker, stage checkpoints, polled progress

Status: accepted (2026-10-10) · Phase 2

## Context
The target runtime starts a bounded Fargate task per job and scales to zero. The arq worker (a long-lived loop)
does not fit, nothing consumed its queue anyway, and Server-Sent Events need a persistent connection.

## Decision
- `python -m unfold.worker.run analysis <job_id>` loads the job, runs the pipeline and exits. Exit 75 means
  "returned to the queue, run me again". The local `JobQueue` adapter starts one such process per job; Fargate
  `RunTask` is the AWS launcher. `arq` is removed.
- `orchestration.pipeline.run_analysis` is plain async functions in nine stage groups. Each group persists its
  output, then writes a `job_checkpoints` row; a rerun skips finished groups, so no stage duplicates rows.
- Progress is rows in `job_events`; `GET /projects/{id}/progress?after=<seq>` returns events after a cursor plus
  the durable job. The SSE route and per-session stream cap are gone.
- Limits: `JOB_TIMEOUT_SECONDS` wall clock, `ANALYSIS_LLM_CALL_BUDGET` per job through the gateway,
  `MAX_JOB_ATTEMPTS` before the job is failed non-retryably (the dead-letter state), archive limits at extraction.
- Graph and dense indexing are optional: failure marks the job `degraded` and retrieval adapts. The lexical index
  and the project model are required.

## Not done here
- LangGraph stays out (ADR 0001): checkpoints are our own table.
- Workflow investigation (`INVESTIGATING_PROJECT`) is skipped, not faked; sequence diagrams wait for it.
- Section simplify/deepen routes, chat and exports are phase 3.
