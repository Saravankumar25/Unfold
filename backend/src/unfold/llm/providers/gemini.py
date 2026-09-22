"""Gemini provider over `google-genai` (LLD §13). The only module that imports `google.genai`.

One `genai.Client` per credential (async surface `.aio`). SDK-level retries and automatic function calling are
disabled: the gateway owns retry/fallback and runs the tool loop. Thought parts are never returned as text.
"""

from __future__ import annotations

import re
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Sequence
from typing import Any, cast

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import SecretStr

from unfold.core.config import Settings
from unfold.domain.enums import ModelCapability
from unfold.llm.json_schema import to_provider_schema
from unfold.llm.providers.base import (
    AuthError,
    BadRequest,
    InvalidOutput,
    ProviderCallError,
    RateLimited,
    TransientError,
    parse_json_object,
    tier_models,
)
from unfold.llm.schemas import LLMRequest, LLMResponse, Message, ToolCall, ToolSpec, Usage

GENERATED_ID_PREFIX = "gcall_"  # ids we mint when Gemini returns function calls without ids (never sent back)
_SIGNATURE_CACHE_SIZE = 4096
# Gemini 3 thinking depth per capability tier: cheap/latency-bound tiers think minimally.
_THINKING_LEVELS: dict[ModelCapability, str] = {
    ModelCapability.FAST: "MINIMAL",
    ModelCapability.STRUCTURED: "LOW",
    ModelCapability.EVALUATOR: "LOW",
    ModelCapability.BALANCED: "LOW",
    ModelCapability.LONG_CONTEXT: "LOW",
    ModelCapability.REASONING: "MEDIUM",
}
_DURATION = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*s\s*$")
_CONTEXT_HINTS = ("exceeds the maximum number of tokens", "input token count", "context length", "too long")
_AUTH_HINTS = (
    "api key not valid",
    "api_key_invalid",
    "api key expired",
    "permission denied",
    "unauthenticated",
)


def parse_retry_delay(details: Any) -> float | None:
    """Extract `google.rpc.RetryInfo.retryDelay` ("17s", "1.5s") from an APIError payload, if present."""
    payload = details.get("error", details) if isinstance(details, dict) else None
    items = payload.get("details") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict) and str(item.get("@type", "")).endswith("google.rpc.RetryInfo"):
            match = _DURATION.match(str(item.get("retryDelay", "")))
            if match:
                return float(match.group(1))
    return None


def _retry_after_header(exc: genai_errors.APIError) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        value = headers.get("retry-after")
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def map_error(exc: BaseException) -> ProviderCallError:
    """Map SDK/transport exceptions to typed provider errors. Never includes key material."""
    if isinstance(exc, ProviderCallError):
        return exc
    if isinstance(exc, genai_errors.APIError):
        code = int(exc.code or 0)
        status = str(exc.status or "").upper()
        message = str(exc.message or status or "Gemini API error")
        low = message.lower()
        if code == 429 or status == "RESOURCE_EXHAUSTED":
            retry = parse_retry_delay(exc.details)
            if retry is None:
                retry = _retry_after_header(exc)
            return RateLimited(f"gemini rate limited: {status or code}", retry_after_s=retry, status=code)
        if (
            code in (401, 403)
            or status in ("PERMISSION_DENIED", "UNAUTHENTICATED")
            or any(h in low for h in _AUTH_HINTS)
        ):
            return AuthError(f"gemini credential rejected: {status or code}", status=code)
        if code in (408, 499) or code >= 500 or status in ("UNAVAILABLE", "DEADLINE_EXCEEDED", "INTERNAL"):
            return TransientError(f"gemini unavailable: {status or code}", status=code)
        if code == 404 or status == "NOT_FOUND":
            return BadRequest(f"gemini model unavailable: {message[:200]}", model_specific=True, status=code)
        if code == 400 or status in ("INVALID_ARGUMENT", "FAILED_PRECONDITION"):
            ctx = any(h in low for h in _CONTEXT_HINTS)
            return BadRequest(f"gemini rejected request: {message[:300]}", context_length=ctx, status=code)
        return TransientError(f"gemini error: {status or code}", status=code)
    if isinstance(exc, httpx.TimeoutException | TimeoutError):
        return TransientError("gemini request timed out")
    if isinstance(exc, httpx.TransportError):
        return TransientError(f"gemini transport error: {type(exc).__name__}")
    return TransientError(f"gemini unexpected error: {type(exc).__name__}")


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        settings: Settings,
        keys: Sequence[SecretStr],
        *,
        httpx_client: httpx.AsyncClient | None = None,
        thinking_budget: int = 1024,
        fast_thinking_budget: int = 0,
    ) -> None:
        self._settings = settings
        self._keys = list(keys)
        self._httpx_client = httpx_client  # tests inject a MockTransport-backed client
        self._clients: dict[int, genai.Client] = {}
        self._thinking_budget = thinking_budget
        self._fast_thinking_budget = fast_thinking_budget
        self._tiers: dict[str, str | None] = {
            "fast": settings.gemini_model_fast,
            "balanced": settings.gemini_model_balanced,
            "reasoning": settings.gemini_model_reasoning,
        }
        # Gemini thinking models attach `thought_signature` to function calls; it must be echoed back on the next
        # turn. `ToolCall` has no field for it, so we keep a bounded id → signature map (see report: contract CR).
        self._signatures: OrderedDict[str, bytes] = OrderedDict()

    @property
    def key_count(self) -> int:
        return len(self._keys)

    def models_for(self, capability: ModelCapability) -> list[str]:
        return tier_models(self._tiers, capability)

    # --- client -------------------------------------------------------------------------------------------
    def _client(self, key_index: int) -> genai.Client:
        client = self._clients.get(key_index)
        if client is None:
            if not 0 <= key_index < len(self._keys):
                raise AuthError(f"gemini key index {key_index} is not configured")
            opts = types.HttpOptions(timeout=int(self._settings.llm_request_timeout_seconds * 1000))
            if self._httpx_client is not None:
                opts.httpx_async_client = self._httpx_client
            client = genai.Client(api_key=self._keys[key_index].get_secret_value(), http_options=opts)
            self._clients[key_index] = client
        return client

    # --- request mapping ------------------------------------------------------------------------------------
    def _remember_signature(self, call_id: str, signature: bytes | None) -> None:
        if not signature:
            return
        self._signatures[call_id] = signature
        self._signatures.move_to_end(call_id)
        while len(self._signatures) > _SIGNATURE_CACHE_SIZE:
            self._signatures.popitem(last=False)

    def build_contents(self, messages: Sequence[Message]) -> list[types.Content]:
        names_by_id: dict[str, str] = {}
        contents: list[types.Content] = []

        def append(role: str, parts: list[types.Part]) -> None:
            if not parts:
                return
            if contents and contents[-1].role == role and contents[-1].parts is not None:
                contents[-1].parts.extend(parts)
            else:
                contents.append(types.Content(role=role, parts=parts))

        for msg in messages:
            if msg.role == "user":
                append("user", [types.Part(text=msg.content)] if msg.content else [])
            elif msg.role == "assistant":
                parts: list[types.Part] = []
                if msg.content:
                    parts.append(types.Part(text=msg.content))
                for call in msg.tool_calls:
                    names_by_id[call.id] = call.name
                    fc = types.FunctionCall(
                        id=None if call.id.startswith(GENERATED_ID_PREFIX) else call.id,
                        name=call.name,
                        args=dict(call.arguments),
                    )
                    parts.append(
                        types.Part(function_call=fc, thought_signature=self._signatures.get(call.id))
                    )
                append("model", parts)
            else:  # tool result
                call_id = msg.tool_call_id or ""
                name = msg.name or names_by_id.get(call_id) or "tool"
                fr = types.FunctionResponse(
                    id=None if (not call_id or call_id.startswith(GENERATED_ID_PREFIX)) else call_id,
                    name=name,
                    response={"result": msg.content},
                )
                append("user", [types.Part(function_response=fr)])
        return contents

    def _contents(self, req: LLMRequest) -> types.ContentListUnion:
        return cast("types.ContentListUnion", self.build_contents(req.messages))

    @staticmethod
    def build_tools(tools: Sequence[ToolSpec]) -> list[types.Tool]:
        if not tools:
            return []
        decls = [
            types.FunctionDeclaration(
                name=t.name,
                description=t.description,
                parameters_json_schema=to_provider_schema(
                    t.parameters or {"type": "object", "properties": {}}
                ),
            )
            for t in tools
        ]
        return [types.Tool(function_declarations=decls)]

    def _thinking(self, model: str, req: LLMRequest) -> types.ThinkingConfig | None:
        name = model.lower()
        if name.startswith("gemini-3"):
            # Gemini 3 uses discrete thinking levels; `thinking_budget=0` is rejected by the lite models.
            level = _THINKING_LEVELS.get(req.capability, "LOW")
            return types.ThinkingConfig(thinking_level=level, include_thoughts=False)  # type: ignore[arg-type]
        if not name.startswith("gemini-2.5"):
            return None
        budget = (
            self._fast_thinking_budget if req.capability is ModelCapability.FAST else self._thinking_budget
        )
        budget = min(budget, max(0, req.max_output_tokens // 2))
        if "pro" in name:
            budget = max(budget, 128)  # pro models cannot disable thinking
        return types.ThinkingConfig(thinking_budget=budget, include_thoughts=False)

    def build_config(
        self, model: str, req: LLMRequest, *, json_schema: dict[str, Any] | None = None
    ) -> types.GenerateContentConfig:
        cfg = types.GenerateContentConfig(
            system_instruction=req.system or None,
            temperature=req.temperature,
            max_output_tokens=req.max_output_tokens,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=self._thinking(model, req),
        )
        if json_schema is not None:
            cfg.response_mime_type = "application/json"
            cfg.response_json_schema = to_provider_schema(json_schema)
        elif req.tools:
            cfg.tools = list(self.build_tools(req.tools))
        return cfg

    # --- response mapping -----------------------------------------------------------------------------------
    def parse_response(self, model: str, resp: types.GenerateContentResponse) -> LLMResponse:
        candidates = resp.candidates or []
        if not candidates:
            feedback = resp.prompt_feedback
            reason = getattr(getattr(feedback, "block_reason", None), "value", None) if feedback else None
            if reason:
                raise BadRequest(f"gemini blocked the prompt: {reason}")
            raise InvalidOutput("gemini returned no candidates")
        cand = candidates[0]
        finish = cand.finish_reason.value if cand.finish_reason is not None else None
        if finish == "MALFORMED_FUNCTION_CALL":
            raise InvalidOutput("gemini produced a malformed function call")
        texts: list[str] = []
        calls: list[ToolCall] = []
        for part in cand.content.parts if cand.content and cand.content.parts else []:
            if part.thought:
                continue
            if part.function_call is not None:
                fc = part.function_call
                call_id = fc.id or f"{GENERATED_ID_PREFIX}{uuid.uuid4().hex[:16]}"
                self._remember_signature(call_id, part.thought_signature)
                calls.append(ToolCall(id=call_id, name=fc.name or "", arguments=dict(fc.args or {})))
            elif part.text:
                texts.append(part.text)
        return LLMResponse(
            text="".join(texts),
            tool_calls=calls,
            provider=self.name,
            model=model,
            usage=self.parse_usage(resp.usage_metadata),
            finish_reason=finish,
        )

    @staticmethod
    def parse_usage(meta: types.GenerateContentResponseUsageMetadata | None) -> Usage:
        if meta is None:
            return Usage()
        output = (meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0)
        return Usage(input_tokens=meta.prompt_token_count or 0, output_tokens=output)

    # --- Provider protocol ------------------------------------------------------------------------------------
    async def generate(self, model: str, key_index: int, req: LLMRequest) -> LLMResponse:
        client = self._client(key_index)
        try:
            resp = await client.aio.models.generate_content(
                model=model, contents=self._contents(req), config=self.build_config(model, req)
            )
        except Exception as exc:
            raise map_error(exc) from exc
        return self.parse_response(model, resp)

    async def stream(self, model: str, key_index: int, req: LLMRequest) -> AsyncIterator[str]:
        client = self._client(key_index)
        try:
            chunks = await client.aio.models.generate_content_stream(
                model=model, contents=self._contents(req), config=self.build_config(model, req)
            )
            async for chunk in chunks:
                for cand in (chunk.candidates or [])[:1]:
                    for part in cand.content.parts if cand.content and cand.content.parts else []:
                        if part.text and not part.thought:
                            yield part.text
        except ProviderCallError:
            raise
        except Exception as exc:
            raise map_error(exc) from exc

    async def generate_json(
        self, model: str, key_index: int, req: LLMRequest, json_schema: dict[str, Any]
    ) -> tuple[dict[str, Any], LLMResponse]:
        client = self._client(key_index)
        try:
            resp = await client.aio.models.generate_content(
                model=model,
                contents=self._contents(req),
                config=self.build_config(model, req, json_schema=json_schema),
            )
        except Exception as exc:
            raise map_error(exc) from exc
        parsed = self.parse_response(model, resp)
        return parse_json_object(parsed.text), parsed
