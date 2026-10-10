# ADR 0008: Frontend contract: polling, JSON chat, no section variants

Status: accepted (2026-10-10) · Phase 3

## Decision
- Progress is polled (`GET /projects/{id}/progress?after=`) with exponential backoff to 10 s. The hook reports
  `waking` when the first answer takes more than 4 s so a cold-started service reads as "waking up", not "broken".
  The project gate and the chat pending note use the same cue.
- Chat sends one `POST /chats/{id}/messages` and renders the whole reply; `lib/api/sse.ts` and the SSE event
  vocabulary in the client are deleted. Citations arrive renumbered, so label `E<n>` is entry `n` of the evidence.
- Exports offer PDF and Markdown and poll `GET /exports/{id}`; downloads use the same-origin `download_url`.
- The "Explain simpler" / "Go deeper" buttons on guide sections called `.../simplify` and `.../deepen`, routes that
  never existed and that HLD/LLD listed only as intent. They are removed rather than built (phase 3 scope). The
  chat modes `simpler` and `deeper` (reachable with "Ask about this") cover the need.
- `error.tsx`, `not-found.tsx` and `loading.tsx` exist at the app root; the project segment has its own loading UI.
- `backend/tests/unit/test_frontend_contract.py` extracts every path in `endpoints.ts` and fails when the API's
  OpenAPI does not serve it.

## Consequences
- Found and fixed while driving the real stack: the provider-less tooltip crashed the guide page, and the progress
  page did not refresh the project when a job went from queued straight to finished.
