# ADR 0016: Quotas, client identity and LLM coordination without Redis

Status: proposed (2026-10-10) · Phase 4 stage B · Resolves the "Known exception" in `docs/adr/0002`.

## Context
Limits are enforced with Redis Lua scripts keyed on session id and on the address from `TRUSTED_PROXY_IPS`
(`api/deps.py:40-56`, `persistence/redis_store.py:15-20`). A Vercel to Function URL hop has no stable peer address,
so peer-based trust cannot be reused. LLM coordination (budget, circuit, key pool, per-job usage, caches) imports
`redis` directly (`llm/budget.py`, `circuit.py`, `key_pool.py`, `gateway.py:70-79`).

## Decision
- Identity: session id for authenticated limits; client address from header `X-Unfold-Client:
  v1.<ts>.<ip>.<HMAC-SHA256>` set by the Vercel proxy and verified (60 s skew) with a secret from SSM; unverified
  callers fall back to the Lambda request-context address (a coarse shared key). Never a raw client header.
- Store: DynamoDB atomic `ADD` counters, fixed window in the key, TTL cosmetic, fail closed (503). Job caps
  (active per session, queued global, concurrent tasks) use conditional counters released on terminal state and
  healed by the sweeper from a GSI.
- LLM: per-job usage item with conditional `ADD` (cost cap, survives attempts); per-model minute and day counters in
  DynamoDB for cross-process quota; concurrency, cooldown, circuit and key rotation in process; key disablement
  persisted; response and embedding caches dropped.
- Defaults and the limits table: `docs/infra/architecture-decision.md` §4.9. New caps: upload URLs, chat per hour
  and per day, exports, active jobs per session, queued jobs, chunks per project, API reserved concurrency.

## Alternatives considered
Honouring `X-Forwarded-For` from any peer (spoofable), API Gateway usage plans (per API key, not per user), WAF rate
rules (Vercel addresses only), keeping ElastiCache (always-on cost), in-memory only (invalid across Lambda
instances).

## Consequences
Provider windows become fixed (approximate) instead of sliding. A failed conditional write still consumes a write
unit (DynamoDB docs). Production guard addition: `PROXY_HMAC_SECRET` required when running as Lambda.
