# ADR 0005: Exports are a one-shot worker job

Status: accepted (2026-10-10) · Phase 3

## Context
PDF export needs Chromium and tens of seconds; it cannot run in the API process of a function runtime.

## Decision
- `POST /projects/{id}/exports/guide?format=pdf|md` and `POST /chats/{id}/exports?format=pdf|md` insert an
  `artifacts` row (`pending`, id = export id) and call `JobQueue.enqueue_export(export_id)`; the id is the
  idempotency key. A pending export of the same kind within ten minutes is returned instead of a second one.
- The task is `python -m unfold.worker.run export <export_id>`. `exports.service.run_export` renders (Jinja ->
  Chromium for PDF, `exports.markdown` for Markdown), writes `projects/{id}/exports/{export_id}.{pdf|md}` through
  `BlobStore.put_bytes`, then marks the row `ready`. A finished export is left alone; a killed worker leaves the row
  `pending` and a rerun overwrites the same key, so nothing is duplicated (integration test kills the process after
  the blob is written and before the row commits).
- Retryable render failures exit 75 and the supervisor retries; permanent ones mark the artifact `failed`; a
  supervisor that gives up marks it `failed` too. The sweeper restarts exports pending for more than five minutes
  and deletes expired artifacts (`EXPORT_TTL_SECONDS`).
- Download is `GET /exports/{id}/download`: proxied through `BlobStore.get_bytes` by default, or a 302 to
  `BlobStore.presigned_get_url` (`EXPORT_DOWNLOAD=presigned`), which S3 implements natively and which avoids function
  response-size limits. Exports are limited per project per hour.
- Markdown neutralises inline HTML and fences code longer than any inner backtick run; PDF reuses the sanitising
  templates and the network-blocked, CSP-pinned Chromium page.

## Consequences
- The API never loads Chromium. Locally one export renders at a time (a single slot in `SubprocessJobQueue`).
- `ArtifactKind` gained `guide_md` and `chat_md`; `artifacts.kind` needed no migration.
