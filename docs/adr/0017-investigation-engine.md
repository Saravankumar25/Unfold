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
5. Track a per-chapter **input hash**; support single-chapter regeneration.
6. Gate all of it with a **rubric** (`evaluation/`) that scores coverage, groundedness, citation accuracy, depth,
   specificity, redundancy, readability and uncertainty honesty against a recorded baseline. Length is never a score.

## Consequences

- More LLM calls per guide (bounded: <= 4 per chapter for research, <= 2 revision rounds). Cost per chapter is logged
  and compared to the baseline; the per-job token budget still applies.
- No agent framework (ADR 0001 stands): the investigator is a plain bounded loop.
- Additive schema only (`guide_section.research`, `.verification`, `.input_hash`); v1 plans and sections still load.
- Phase 4 backends (embedded dense, in-memory graph) must keep the rubric at or above baseline.
- Cross-revision reuse of parsed files is out of scope; recorded as a known limitation.

## Alternatives rejected

- **LangGraph / Deep Agents for research:** more moving parts, no measured gain; revisit only if the rubric shows the
  plain loop plateaus.
- **LLM-only verification:** costly and unreliable; deterministic checks first.
- **Bigger context window with more chunks:** raises cost and noise, does not trace execution.
