"""Groq provider: OpenAI-compatible Chat Completions over httpx (LLD §13). Enabled only when Groq keys exist.

Tools map to OpenAI `tools`, structured output uses JSON mode (`response_format={"type":"json_object"}`) plus the
schema in the system prompt (portable across Groq models), streaming parses SSE `data:` lines.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
import orjson
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

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
_CONTEXT_CODES = ("context_length_exceeded",)
_CONTEXT_HINTS = (
    "context length",
    "context_length",
    "reduce the length",
    "too many tokens",
    "maximum context",
)
_INVALID_OUTPUT_CODES = ("tool_use_failed", "json_validate_failed", "output_parse_failed")


def _parse_retry_after(headers: httpx.Headers) -> float | None:
    for name in ("retry-after", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
        value = headers.get(name)
        if not value:
            continue
        text = value.strip().lower()
        try:
            if text.endswith("ms"):
                return float(text[:-2]) / 1000.0
            if text.endswith("s") and "m" in text[:-1]:  # e.g. "1m30.5s"
                minutes, seconds = text[:-1].split("m", 1)
                return float(minutes) * 60 + float(seconds or 0)
            return float(text.rstrip("s"))
        except ValueError:
            continue
    return None


def map_http_error(response: httpx.Response) -> ProviderCallError:
    code = response.status_code
    try:
        body = response.json()
    except ValueError:
        body = {}
    err = body.get("error", {}) if isinstance(body, dict) else {}
    err = err if isinstance(err, dict) else {}
    message = str(err.get("message") or response.reason_phrase or "groq error")
    err_code = str(err.get("code") or err.get("type") or "")
    low = message.lower()
    if code == 429:
        return RateLimited(
            f"groq rate limited: {err_code or code}", retry_after_s=_parse_retry_after(response.headers)
        )
    if code in (401, 403):
        return AuthError(f"groq credential rejected: {code}", status=code)
    if code == 413 or err_code in _CONTEXT_CODES or (code == 400 and any(h in low for h in _CONTEXT_HINTS)):
        return BadRequest(f"groq context too long: {message[:200]}", context_length=True, status=code)
    if err_code in _INVALID_OUTPUT_CODES:
        return InvalidOutput(
            f"groq invalid output: {err_code}", raw=str(err.get("failed_generation", ""))[:4000]
        )
    if code == 404:
        return BadRequest(f"groq model unavailable: {message[:200]}", model_specific=True, status=code)
    if code in (408, 409) or code >= 500:
        return TransientError(f"groq unavailable: {code}", status=code)
    if 400 <= code < 500:
        return BadRequest(f"groq rejected request: {message[:300]}", status=code)
    return TransientError(f"groq error: {code}", status=code)


class GroqProvider:
    name = "groq"

    def __init__(
        self,
        settings: Settings,
        keys: Sequence[SecretStr],
        *,
        base_url: str = GROQ_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._keys = list(keys)
        self._tiers: dict[str, str | None] = {
            "fast": settings.groq_model_fast,
            "balanced": settings.groq_model_balanced,
            "reasoning": settings.groq_model_balanced,
        }
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(settings.llm_request_timeout_seconds),
            transport=transport,
        )

    @property
    def key_count(self) -> int:
        return len(self._keys)

    def models_for(self, capability: ModelCapability) -> list[str]:
        if capability is ModelCapability.LONG_CONTEXT:
            return []  # Gemini-only tier: Groq windows are much smaller
        return tier_models(self._tiers, capability)

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- mapping --------------------------------------------------------------------------------------------
    @staticmethod
    def build_messages(system: str, messages: Sequence[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if system:
            out.append({"role": "system", "content": system})
        for msg in messages:
            if msg.role == "user":
                out.append({"role": "user", "content": msg.content})
            elif msg.role == "assistant":
                item: dict[str, Any] = {"role": "assistant", "content": msg.content or None}
                if msg.tool_calls:
                    item["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": orjson.dumps(c.arguments).decode()},
                        }
                        for c in msg.tool_calls
                    ]
                out.append(item)
            else:
                out.append({"role": "tool", "tool_call_id": msg.tool_call_id or "", "content": msg.content})
        return out

    @staticmethod
    def build_tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": to_provider_schema(t.parameters or {"type": "object", "properties": {}}),
                },
            }
            for t in tools
        ]

    def build_body(
        self, model: str, req: LLMRequest, *, json_schema: dict[str, Any] | None = None, stream: bool = False
    ) -> dict[str, Any]:
        system = req.system
        if json_schema is not None:
            schema_text = orjson.dumps(to_provider_schema(json_schema)).decode()
            system = (
                f"{system}\n\n" if system else ""
            ) + f"Respond with a single JSON object that conforms to this JSON Schema:\n{schema_text}"
        body: dict[str, Any] = {
            "model": model,
            "messages": self.build_messages(system, req.messages),
            "temperature": req.temperature,
            "max_completion_tokens": req.max_output_tokens,
        }
        if json_schema is not None:
            body["response_format"] = {"type": "json_object"}
        elif req.tools:
            body["tools"] = self.build_tools(req.tools)
            body["tool_choice"] = "auto"
        if stream:
            body["stream"] = True
        return body

    def parse_body(self, model: str, data: dict[str, Any]) -> LLMResponse:
        choices = data.get("choices") or []
        if not choices:
            raise InvalidOutput("groq returned no choices")
        choice = choices[0]
        message = choice.get("message") or {}
        calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            fn = raw.get("function") or {}
            args_text = fn.get("arguments") or "{}"
            try:
                args = orjson.loads(args_text) if isinstance(args_text, str) else dict(args_text)
            except orjson.JSONDecodeError as exc:
                raise InvalidOutput(
                    "groq returned malformed tool arguments", raw=str(args_text)[:4000]
                ) from exc
            calls.append(
                ToolCall(
                    id=str(raw.get("id") or f"call_{uuid.uuid4().hex[:16]}"),
                    name=str(fn.get("name") or ""),
                    arguments=args if isinstance(args, dict) else {"value": args},
                )
            )
        usage = data.get("usage") or {}
        return LLMResponse(
            text=str(message.get("content") or ""),
            tool_calls=calls,
            provider=self.name,
            model=model,
            usage=Usage(
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
            ),
            finish_reason=choice.get("finish_reason"),
        )

    def _headers(self, key_index: int) -> dict[str, str]:
        if not 0 <= key_index < len(self._keys):
            raise AuthError(f"groq key index {key_index} is not configured")
        return {"Authorization": f"Bearer {self._keys[key_index].get_secret_value()}"}

    async def _post(self, key_index: int, body: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = await self._http.post("/chat/completions", json=body, headers=self._headers(key_index))
        except httpx.TimeoutException as exc:
            raise TransientError("groq request timed out") from exc
        except httpx.TransportError as exc:
            raise TransientError(f"groq transport error: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise map_http_error(resp)
        try:
            data = resp.json()
        except ValueError as exc:
            raise TransientError("groq returned a non-JSON body") from exc
        if not isinstance(data, dict):
            raise TransientError("groq returned an unexpected body")
        return data

    # --- Provider protocol ------------------------------------------------------------------------------------
    async def generate(self, model: str, key_index: int, req: LLMRequest) -> LLMResponse:
        return self.parse_body(model, await self._post(key_index, self.build_body(model, req)))

    async def generate_json(
        self, model: str, key_index: int, req: LLMRequest, json_schema: dict[str, Any]
    ) -> tuple[dict[str, Any], LLMResponse]:
        data = await self._post(key_index, self.build_body(model, req, json_schema=json_schema))
        parsed = self.parse_body(model, data)
        return parse_json_object(parsed.text), parsed

    async def stream(self, model: str, key_index: int, req: LLMRequest) -> AsyncIterator[str]:
        body = self.build_body(model, req, stream=True)
        try:
            async with self._http.stream(
                "POST", "/chat/completions", json=body, headers=self._headers(key_index)
            ) as resp:
                if resp.status_code != 200:
                    await resp.aread()
                    raise map_http_error(resp)
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        return
                    try:
                        event = orjson.loads(payload)
                    except orjson.JSONDecodeError:
                        continue
                    if isinstance(event, dict) and event.get("error"):
                        raise TransientError("groq stream error event")
                    for choice in (event.get("choices") or [])[:1] if isinstance(event, dict) else []:
                        text = (choice.get("delta") or {}).get("content")
                        if text:
                            yield str(text)
        except ProviderCallError:
            raise
        except httpx.TimeoutException as exc:
            raise TransientError("groq stream timed out") from exc
        except httpx.TransportError as exc:
            raise TransientError(f"groq transport error: {type(exc).__name__}") from exc
