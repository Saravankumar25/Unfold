"""LLM Gateway contracts (LLD §13). Business code depends ONLY on these types and `LLMClient`, never on SDKs."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, Field

from unfold.domain.enums import ModelCapability, Priority

T = TypeVar("T", bound=BaseModel)

# Accounting key prefix: the gateway budgets calls and tokens per chat (`chat:<id>`), not per analysis job.
CHAT_JOB_PREFIX = "chat:"


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)  # assistant turns that requested tools
    tool_call_id: str | None = None  # tool turns: id of the call being answered
    name: str | None = None  # tool turns: tool name


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema (object)


class LLMRequest(BaseModel):
    purpose: str  # e.g. "guide.section", "chat.answer", "agent.architecture" — used for tracing/accounting
    capability: ModelCapability
    priority: Priority = Priority.P1
    system: str = ""
    messages: list[Message]
    tools: list[ToolSpec] = Field(default_factory=list)
    temperature: float = 0.2
    max_output_tokens: int = 4096
    cacheable: bool = False
    exclude_models: set[str] = Field(default_factory=set)
    job_id: str | None = None
    project_id: str | None = None


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class LLMResponse(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    provider: str
    model: str
    usage: Usage = Field(default_factory=Usage)
    latency_ms: float = 0.0
    cached: bool = False
    finish_reason: str | None = None


class LLMClient(Protocol):
    """Implemented by `unfold.llm.gateway.LLMGateway`. Tests may pass any object with this shape."""

    async def generate(self, req: LLMRequest) -> LLMResponse: ...

    async def generate_structured(self, req: LLMRequest, schema: type[T]) -> T: ...

    def stream(self, req: LLMRequest) -> AsyncIterator[str]: ...
