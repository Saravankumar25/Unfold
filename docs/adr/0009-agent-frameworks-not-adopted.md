# ADR 0009: Deep Agents and other agent frameworks: not adopted

Status: accepted (2026-10-10) · Phase 3 · Confirms ADR 0001

ADR 0001 required evals showing an agent beats plain retrieval-then-write on groundedness at equal token cost
before re-adding one. Phase 3 built those evals (ADR 0007); the plain path scores 1.0 on grounded ratio and
citation validity with a scripted model, and a real guide plus chat run (Gemini, one public repository) produced
cited, evidence-backed answers with 1 planner call, N section calls and 1 call per chat turn. There is no measured gap
an agent could close, and an agent loop would multiply calls per turn against a per-chat budget. Deep Agents and
LangGraph stay out; `unfold/agents/` keeps only the `WorkflowFinding` contracts that diagrams already use.
