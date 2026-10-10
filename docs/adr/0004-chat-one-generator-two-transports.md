# ADR 0004: Chat answers: one generator, JSON by default, NDJSON on request

Status: accepted (2026-10-10) · Phase 3

## Context
Chat must be grounded and injection-safe, and its transport must survive the AWS target, where a response behind an
API gateway or function runtime may be buffered or capped (`docs/infra/strategy.md`). Phase 4 decides the runtime;
phase 3 must not bake a choice into chat logic.

## Decision
- `unfold/chat/answer.py::generate_answer` is the only place an answer is produced: plan and retrieve with
  `HybridRetriever`, compile a budgeted fenced context, stream the model through the gateway, drop unknown
  citations, grade every cited sentence with `guide.generate.grade_claims`, renumber citations `[E1]..[Ek]` so a
  label indexes the returned evidence list.
- `POST /chats/{id}/messages` is the only route. `stream=false` (default) returns one `ChatReplyOut` JSON body;
  `stream=true` returns the same `ChatEvent`s as NDJSON lines. Everything that can fail before the first token
  (ownership, readiness, limits, history, section key) is resolved first, so those failures are normal HTTP errors in
  both modes. The transport code is the last 40 lines of `api/routes/chat.py`.
- First-token latency (`first_token_ms`) and `retrieval_ms`/`total_ms` are measured inside the generator for both
  transports, logged as `chat.answered`, recorded in the `unfold.chat.first_token` histogram and returned.
- Budget is per chat: model calls are accounted under `chat:<id>` with `CHAT_LLM_CALL_BUDGET` / `CHAT_LLM_TOKEN_BUDGET`
  (gateway `_admit`); exhaustion is a 429 that is not retryable. Messages are limited per session per minute.
- Prompt safety: the question, history and guide-section context are redacted and fenced like repository text,
  evidence only arrives through `compile_context`, the system prompt carries `UNTRUSTED_POLICY`, the scanner's
  verdict on the question is logged, and the answer is redacted again before it is stored or returned.
- A turn is persisted (user and assistant message, one transaction) only after generation succeeds.

## Consequences
- The frontend uses the JSON transport (no streaming reader); NDJSON stays available for a runtime that can flush.
- Deltas of a streamed answer are not redacted piecewise; the final text is. Inputs are redacted before they reach
  the model, so the residual risk is a secret the user typed that the model repeats.
- Dense and graph retrieval run in the API process (embedder, reranker load on first use: the first answer is slow).
