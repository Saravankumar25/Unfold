# ADR 0014: ZIP uploads go browser to S3 with a presigned POST

Status: proposed (2026-10-10) · Phase 4 stage B

## Context
`POST /projects/zip` currently streams up to 50 MiB through the API (`api/routes/projects.py:169-211`) and the
Next proxy streams it (`frontend/src/app/api/[...path]/route.ts:5`). Lambda accepts 6 MB; a Vercel Function body is
limited to 4.5 MB (documented 2026-10-10). The path cannot work on the target runtime at 50 MiB.

## Decision
`POST /projects/zip/upload-url` (session, quotas, project in `awaiting_upload`) returns an S3 presigned POST with
`content-length-range 1..max_zip_bytes`, exact key `projects/{id}/upload/source.zip`, content type
`application/zip`, 10-minute expiry. The browser POSTs to S3 (bucket CORS for the web origin; CSP `connect-src`
extended). `POST /projects/{id}/zip/complete` verifies ownership, `HeadObject` size, ZIP magic via a 4-byte ranged
GET, then creates the job. Extraction limits and bomb checks remain in the worker (`ingestion/archive.py`). The
multipart route stays for local dev behind `ALLOW_DIRECT_UPLOAD`.

## Alternatives considered
Presigned PUT (cannot constrain size), proxying through Lambda or Vercel (payload limits), multipart upload from the
browser (more calls, same limits needed).

## Consequences
Orphan uploads are reaped by the sweeper and a 4-day S3 lifecycle rule (backstop only). Presigned URLs die with the
signing credentials, hence a short expiry. A browser-to-S3 CORS and CSP test requires AWS.
