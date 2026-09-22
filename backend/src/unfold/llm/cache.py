"""LLM response cache (LLD §13, AI_infrastructure §53). Only for `req.cacheable` requests.

Key = sha256 of the canonical JSON of the *redacted* request (plus output kind / schema), so identical prompts
reuse answers for 24 h. Job/project ids, priority and exclusions are not part of the key.
"""

from __future__ import annotations

import hashlib
from typing import Any

import orjson

from unfold.llm.schemas import LLMRequest
from unfold.persistence.redis_store import RedisStore

CACHE_TTL_SECONDS = 86_400
_EXCLUDED_FIELDS = {"job_id", "project_id", "priority", "exclude_models", "cacheable"}


def cache_key(req: LLMRequest, kind: str = "text", *, prefix: str = "llm") -> str:
    payload = req.model_dump(mode="json", exclude=_EXCLUDED_FIELDS)
    canonical = orjson.dumps({"kind": kind, "req": payload}, option=orjson.OPT_SORT_KEYS)
    return f"{prefix}:cache:{hashlib.sha256(canonical).hexdigest()}"


class ResponseCache:
    def __init__(
        self, redis: RedisStore, *, prefix: str = "llm", ttl_seconds: int = CACHE_TTL_SECONDS
    ) -> None:
        self._redis = redis
        self._prefix = prefix
        self._ttl = ttl_seconds

    def key(self, req: LLMRequest, kind: str = "text") -> str:
        return cache_key(req, kind, prefix=self._prefix)

    async def get(self, req: LLMRequest, kind: str = "text") -> Any | None:
        return await self._redis.get_json(self.key(req, kind))

    async def set(self, req: LLMRequest, value: Any, kind: str = "text") -> None:
        await self._redis.set_json(self.key(req, kind), value, self._ttl)
