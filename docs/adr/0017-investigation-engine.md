# 0017. Investigate before writing: research briefs, adaptive plans, verified chapters

Status: accepted (2026-10-10). Design: `docs/design/investigation-engine.md`. Owner: phase 3.

## Context

Phase 2 produces a guide with one planner call and, per section, one retrieval and one writer call. Verification grades
claims by identifier overlap and never revises. The repository map (IR, graph, hybrid retrieval) already exists but
is not used to plan or to research. Documentation quality is therefore bounded by one retrieval per section.

## Decision

1. Add a deterministic **architecture model** and **flow tracing** derived from the existing IR and graph. No new store.
2. Replace the fixed outline with a **repository-specific chapter plan** (objective, questions, targets, dependencies,
   expected evidence, depth), built from a deterministic skeleton and refined by one LLM call.
3. Add a bounded **investigation step** per chapter that produces a structured **research brief**; the writer
   receives the brief, not a chunk dump.
4. Add **deterministic claim and citation verification** (path, line range, symbol, excerpt, edge) before one semantic
   critique, and a **bounded revision loop** (<= 2 rounds, failing blocks only).
5. Support single-chapter regeneration (a per-chapter **input hash** was part of the decision; see "As built").
6. Gate all of it with a **rubric** (`evaluation/`) that scores coverage, groundedness, citation accuracy, depth,
   specificity, redundancy, readability and uncertainty honesty against a recorded baseline. Length is never a score.

## Consequences

- More LLM calls per guide (bounded: <= 4 per chapter for research, <= 2 revision rounds). Cost per chapter is logged
  and compared to the baseline; the per-job token budget still applies.
- No agent framework (ADR 0001 stands): the investigator is a plain bounded loop.
- Additive schema only (`guide_section.research`, `.verification`; `.input_hash` is reserved and never written); v1 plans and sections still load.
- Phase 4 backends (embedded dense, in-memory graph) must keep the rubric at or above baseline.
- Cross-revision reuse of parsed files is out of scope; recorded as a known limitation.

## Alternatives rejected

- **LangGraph / Deep Agents for research:** more moving parts, no measured gain; revisit only if the rubric shows the
  plain loop plateaus.
- **LLM-only verification:** costly and unreliable; deterministic checks first.
- **Bigger context window with more chunks:** raises cost and noise, does not trace execution.

## On AWS (phase 4, after the phase 3 merge)

The engine runs unchanged on the DynamoDB/S3/SQS profile: it reads and writes only through `ControlStore`,
`ChunkStore`, `BlobStore` and `JobQueue`. Research, writing, verification, revision and regeneration execute in the
Fargate `analysis` task; the API Lambda only records the job. The per-chapter caps are settings
(`RESEARCH_MAX_*`, `REVISION_MAX_ROUNDS`). Task size and timeout for the longer pipeline are not re-measured on
Fargate (requires AWS); see `docs/infra/operations.md` section 7 for what was run locally.

## As built (phase 3B, 2026-10-11)

- `REVISING_GUIDE` is the 21st stage; verification and repair run per chapter and `guide_sections.verification` is
  the per-chapter checkpoint. Critique and rewrite are priority P2 so the per-job budget refuses them before the
  writer; a refusal degrades to deterministic qualify-or-remove, it never fails the job.
- Regeneration is an `analysis_jobs` row of kind `regenerate` on the existing queue port, not a new port or store; it
  is invisible to the project's own status, so a failed regeneration cannot brick a finished guide.
- Two measured runs per repository passed the gate (`evals/baselines/pipeline-v2/`, `docs/design/quality-report.md`).
  Human review of readability and judged depth is not done.
- Input-hash reuse was dropped after review: Chapter-level reuse by input hash is not built: a project has exactly one analysis job (created with the project, resumed by checkpoint on retry or sweep) and a regeneration always redoes its chapter, so no second job ever finds finished chapters to compare. `guide_sections.input_hash` stays as an unwritten nullable column (additive, already migrated); a re-analysis feature would need to write and read it.
