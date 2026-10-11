# Reliability: failure injection on the local stack (phase 5, tasks 4 and 12)

Status: tests written and run 2026-10-11 against real Postgres, Redis, MinIO, Qdrant and Neo4j (isolated database,
Redis db, bucket and vector prefix). The AWS-specific cases (SQS duplicate delivery, DLQ, Aurora resume, "waking up"
UX) are **not run here**: they need phase 4 merged and a deployment. The tests below assert the contract those cases
must satisfy, so they are adapter-agnostic: a job is claimed under a row lock, every stage group writes a checkpoint,
a chapter is finished only when its `verification` is written, a regeneration swaps a chapter in one transaction.

Test file: `backend/tests/integration/test_pipeline_failure_recovery.py` (`uv run pytest -m integration -k failure`).
Doubles: the model (`ScriptedLLM`, or a scripted *provider* behind the real `LLMGateway` for 429, timeout, circuit,
key pool and budget), a proxy over the database that refuses a checkpoint write or every statement, a recording
queue. A "kill" cancels the running job at a chosen model call: the row stays `running`, exactly as after SIGKILL;
the heartbeat is then aged to what the sweeper waits for. Everything else is real, including the sweeper.

One project is walked through a chain of failures so the early stages run once (the `chain` fixture); each test asserts
one row below. Invariants checked after every interruption (`_assert_consistent`): one row per planned chapter, no
chapter with content but no brief, `verification` present if and only if status is `ready`/`degraded`, no duplicate
claims or diagram blocks, no unsupported statement standing in a finished chapter.

## Matrix

| # | Failure | Expected behaviour | Test | Result |
|---|---|---|---|---|
| 1 | LLM 429 (short retry-after) | waited out on the same model; usage records `rate_limited` and `retries` | `test_429_rejected_key_and_transient_errors_are_absorbed_by_the_gateway` | pass |
| 2 | LLM key rejected (401) | key disabled in the pool, the next key serves, the call succeeds | same | pass |
| 3 | LLM 5xx, retries used up | circuit for that model opens, fallback model serves, later calls skip the open model | same | pass |
| 4 | LLM stops answering mid-guide (every candidate times out) | `PROVIDER_RATE_LIMITED`, **retryable**, job back to `queued`; all written chapters kept and none shown as verified; the retry never calls the planner again and finishes | `test_provider_that_stops_answering_mid_guide_is_a_retryable_failure_that_resumes` | pass |
| 5 | Provider capacity exhausted while writing chapter 3 | retryable failure; finished chapters kept; resume skips plan and research | `test_resume_after_a_kill_skips_finished_work_and_capacity_loss_is_retryable` | pass |
| 6 | Worker killed between research and write | row stays `running`, no failure recorded; checkpoints `analyze`..`research` exist, no `sections`; every chapter has its brief and no content | `test_worker_killed_between_research_and_write_keeps_the_briefs_and_no_content` | pass |
| 7 | Same job delivered twice at once | exactly one claim (attempt 1); the second delivery returns "not claimed", makes no model call, writes nothing | `test_duplicate_delivery_runs_the_job_once` | pass |
| 8 | Delivery while the first worker's heartbeat is fresh; worker dead and heartbeat stale | refused (no model call); the sweeper re-enqueues the stale job | `test_live_heartbeat_blocks_a_third_worker_and_the_sweeper_requeues_a_dead_one` | pass |
| 9 | Killed during revision of chapter 2 | chapter 1 verified and final, chapter 2 still its unrevised draft (`generating`, no `verification`); no chapter in between | `test_kill_during_revision_leaves_each_chapter_whole_and_verification_consistent` | pass |
| 10 | Resume after 9 | verified chapters byte-identical; remaining chapters verified; job succeeds | `test_resume_after_revision_kill_does_not_touch_verified_chapters_and_finishes` | pass |
| 11 | Job redelivered after success | no-op, no model call, rows unchanged | `test_job_redelivered_after_success_is_a_no_op` | pass |
| 12 | Job redelivered after it **failed for good** | no-op: status, typed error code, attempt and stage unchanged (**R-2**) | `test_a_job_that_failed_for_good_and_is_delivered_again_keeps_its_failure_record` | pass (failed before the fix) |
| 13 | Database refuses the checkpoint write at the end of verification | `STORE_UNAVAILABLE`, **retryable**, job `queued`; the next attempt skips every verified chapter and finishes with no model call (**R-1**) | `test_database_refusing_a_checkpoint_write_is_a_retryable_failure_not_a_dead_job` | pass |
| 14 | Database refuses the dense-index checkpoint after the vectors were written | retryable; the stage reruns and holds exactly one set of points (vector, chunk counts equal an uninterrupted run) | `test_database_refusing_the_dense_index_checkpoint_reruns_that_stage_without_duplicates` | pass |
| 15 | Database paused for a whole attempt (nothing can record the failure either) | the worker raises, the row still says `running` with a stale heartbeat, nothing half written; the sweeper path of row 8 resumes it | `test_database_paused_for_a_whole_attempt_leaves_a_job_the_sweeper_can_resume` | pass |
| 16 | Nothing duplicated after all of the above | vectors, graph nodes, chunks, summaries, sections and diagrams equal an uninterrupted run of the same repository | `test_no_stage_left_duplicate_rows_after_all_the_interruptions` | pass |
| 17 | Regenerate while the analysis is unfinished | 409 `PROJECT_NOT_READY`; no regeneration row; the original job's status and attempt unchanged | `test_regenerate_while_the_analysis_is_unfinished_is_refused_and_changes_nothing` | pass |
| 18 | Regenerate the same chapter twice, another chapter meanwhile | same job id (202), no second enqueue; the other chapter gets 409 `CONFLICT` | `test_regeneration_killed_midway_leaves_the_chapter_whole_and_rerun_swaps_it_once` | pass |
| 19 | Regeneration killed during its revision | every chapter byte-identical to before; the project still reads as succeeded | same | pass |
| 20 | Killed regeneration rerun, then delivered again | chapter swapped in once (`version + 1`, not per attempt); other chapters untouched; second delivery is a no-op | same | pass |
| 21 | Regenerate, then delete the project (with a regeneration whose worker is alive) | cleanup is deferred while that worker is alive, then removes every row (including `kind=regenerate` jobs and their checkpoints), every Redis key naming the project or any of its jobs (usage hashes, the regeneration quota), blobs, vectors and graph nodes; a second pass finds nothing | `test_deleting_a_project_with_regenerations_leaves_nothing_behind` | pass |
| 22 | Budget runs out while chapters are written | typed `LLM_BUDGET_EXHAUSTED`, not retryable, job `failed`; chapters written before it are kept, none half written | `test_budget_exhaustion_degrades_enrichments_and_fails_typed_before_writing_is_starved` | pass |
| 23 | Budget covers writing but not critique and revision | **degraded, not failed**: `guide_critique` and `guide_revision` recorded, every chapter finished as `degraded` with its unsupported claim qualified or removed | same | pass |

Results are those of the runs recorded in "Run record" below.

## Run record

All on the local stack, 2026-10-11, `uv run pytest -m integration -k "recovery or failure or redteam"` with the isolated
database, Redis db, bucket and vector prefix:

* First formal run: 22 passed, 2 failed in 42 min 38 s. The two failures were a wrong assertion of mine in the
  regeneration test (it expected the recording queue to hold only its own job; the app's start-up sweep also handed
  back unrelated stale jobs of the shared test database) and, as a consequence, the deletion test that depends on it.
  No product defect.
* After correcting that assertion (`queue.analyses.count(job_id) == 1`), the two tests were rerun: 2 passed (the chain
  fixture runs again for them), exit 0.
* The 22 that passed in the first run include tests of other owners that match the filter
  (`test_chat_api`, `test_exports`, `test_pipeline::test_rerun_after_failure_resumes_without_duplicates`, ...).
* A job that was killed was always recovered through the same two steps: its heartbeat went stale (aged by hand
  here, `STALE_AFTER` = 5 minutes for the sweeper, `ALIVE_WITHIN` = 2 minutes for a worker) and a worker claimed it
  again. Nothing here measures how long that takes on AWS.
* Timings on this machine are not evidence of anything: the host was shared with other agents' test runs.

## Defects found and fixed

| Id | Defect | Fix | Test |
|---|---|---|---|
| R-1 | Any database connection error inside a job (a paused or restarting Aurora, a dropped connection) was caught by the generic handler and ended the job `FAILED` / `INTERNAL_ERROR` / not retryable, although every stage is checkpointed and resumable | `orchestration/pipeline.py:181,196`: connection-level `DBAPIError` (`OperationalError`, `InterfaceError`, invalidated connection) and `ConnectionError` become a retryable `StoreUnavailable` | row 13, 14 |
| R-2 | A copy of a job that had already failed for good (at-least-once delivery, late retry) was claimed again, flipped back to `running`, crashed on "FAILED_x is terminal" and **overwrote the typed error with `INTERNAL_ERROR`** | `orchestration/progress.py:65`: `start()` treats `failed` like `succeeded` (not claimable) | row 12 (failed before the fix) |

Before the fix R-2 failed with `INTERNAL_ERROR != UNSAFE_ARCHIVE` on the second delivery. R-1 was fixed before the
first complete chain run, so the old behaviour was reproduced afterwards by switching the new classification off
(a database refusing the first checkpoint write of a fresh job):

| Classification | Outcome | Job row |
|---|---|---|
| old (`except Exception` only) | `RunOutcome(succeeded=False, retry=False)` | `failed`, `INTERNAL_ERROR`, `retryable=False` |
| new (`_store_unreachable`) | `RunOutcome(succeeded=False, retry=True)` | `queued`, `STORE_UNAVAILABLE`, `retryable=True` |

## Behaviour worth knowing (not defects)

* A job that ended `failed` is final. There is no route that re-runs it; "try again" is a new project, and finished
  chapters are not reused across jobs (ADR 0017: one analysis job per project). That is why row 22 does not resume.
* Budget exhaustion only degrades the enrichments (critique, revision: priority P2, refused first). A budget too
  small for the planner, research and writer calls (P1, allowed up to 1.3x the soft limit) is a typed failure, not
  a degraded guide: nothing is invented to fill the gap.
* `section_concurrency` was 1 in these tests so "the Nth call" is a place. With 2, a failing chapter lets the other
  chapter of its wave finish before the stage raises; that is what the resume skips, and row 5 asserts only
  "at least two chapters kept".

## Not covered

* SQS duplicate delivery, visibility timeout, DLQ drain, Step Functions retry and Aurora auto-resume latency
  (phase 4 artefacts; requires AWS or the phase 4 adapters merged).
* The "waking up" UX on the frontend while the database resumes (frontend owner).
* Redis outage mid-job (budget and progress go through Redis): the gateway fails closed with a typed error, but no
  injection test exists for it yet.
* A kill inside the three-statement window of `_persist_chapter` (regeneration): it is one transaction, so the
  cancellation tests above cover it only up to its start.
