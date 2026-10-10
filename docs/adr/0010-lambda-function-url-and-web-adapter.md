# ADR 0010: API runs as a container Lambda behind a Function URL with Lambda Web Adapter

Status: proposed (2026-10-10) · Phase 4 stage B · Supersedes the "API Gateway HTTP API + Mangum" assumption in
`six-phase-plan.md` Appendix A and `docs/adr/0002` (phase 4 column).

## Context
Phase 3 **[planned]** adds chat with a `stream=false|true` switch. The API also carries sessions, project creation,
polling and guide reads. Facts retrieved 2026-10-10 (sources in `docs/infra/architecture-decision.md` §1):
- HTTP API: 30 s integration timeout, no response streaming.
- REST API: 29 s default; response streaming GA 2025-11-19, up to 15 minutes with `responseTransferMode: STREAM`;
  $3.50 per million requests (HTTP API $1.00).
- Lambda Function URL: `RESPONSE_STREAM`, function timeout up to 15 min, 200 MB streamed, auth NONE or AWS_IAM,
  free; throttling only through reserved concurrency (RPS cap = 10 x reserved).
- Mangum has no streaming path; AWS Lambda Web Adapter (LWA) streams FastAPI `StreamingResponse` and ships a
  FastAPI example behind a Function URL.
- All browser traffic arrives from the Vercel proxy (4.5 MB body limit, 300 s duration), so API Gateway usage plans
  and WAF cannot distinguish end users.

## Decision
One Lambda (container image, `unfold-api`) behind a Lambda Function URL with `InvokeMode=RESPONSE_STREAM`, fronted
by the existing Next.js route-handler proxy, run with LWA (`AWS_LWA_INVOKE_MODE=response_stream`, readiness on
`/health`). No API Gateway. Identity and throttling are application-level (ADR 0016). Memory about 3.5 GB (2 vCPU,
tune by benchmark), timeout 120 s, reserved concurrency 10 where the account allows.

## Alternatives considered
- HTTP API + Mangum: simplest and cheapest, but cannot stream and caps every request at 30 s; chat would need a
  second front door. Rejected.
- REST API + LWA/streaming: supported by the documented protocol, **no AWS-published LWA + REST sample, unverified
  end to end**; adds a component and 3.5x the request price for features that are unusable behind Vercel. Kept as
  the migration path if per-client throttling or WAF becomes necessary.
- Lambda SnapStart: supported for Python container images (3.12+) but bills cache storage while idle (about $3.95
  per GB-month, derived) and is incompatible with `/tmp` > 512 MB. Off by default.

## Consequences
- Cold start is a property of image pull + imports + model load and is **unmeasured**; import hygiene (stage C WS1)
  removes fastembed/networkx/tree-sitter/qdrant/neo4j from the non-chat import path.
- No Lambda handler code is needed (LWA proxies to uvicorn); the API must stop doing start-up work per cold start
  (`ensure_bucket`, `requeue_incomplete_jobs`, `api/main.py:40-41`).
- A Function URL is directly callable; abuse is bounded by reserved concurrency, per-IP/global quotas and budgets.
- Acceptance tests that require AWS: end-to-end streaming through Vercel, cold start p50/p95/p99, reserved
  concurrency feasibility in a new account.
