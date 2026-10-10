# ADR 0001: Drop LangGraph, Deep Agents and the LangChain adapter

Status: accepted (2026-10-10) · Phase 1

## Context
`deepagents`, `langgraph`, `langgraph-checkpoint-postgres`, `langchain-core` and `psycopg` were declared but imported
nowhere except `llm/langchain_adapter.py` (a LangChain chat model that only Deep Agents used). The guide is a
deterministic pipeline (acquire, analyse, index, plan, write each section with retrieved evidence, verify, persist),
and the target runtime is a one-shot Fargate task that checkpoints stages in Postgres.

## Decision
Remove all five dependencies and the adapter. The pipeline is plain async functions with a stage-checkpoint table
(phase 2). Re-adding an agent framework requires phase-3 evals showing it beats retrieval-then-write on groundedness
at equal token cost.

## Consequences
- Smaller worker and API images; one less failure surface and no framework-owned persistence.
- Resume semantics are ours: each stage writes a checkpoint row and is skipped on rerun.
- Chat uses retrieval plus the gateway directly.
