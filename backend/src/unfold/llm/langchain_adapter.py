"""LangChain chat model over the LLM Gateway, used by Deep Agents only (LLD §13, §15).

`GatewayChatModel` converts LangChain messages (System / Human / AI with `tool_calls` / Tool) into an
`LLMRequest`, calls `LLMClient.generate`, and returns an `AIMessage` with `tool_calls` and `usage_metadata`.
All budgets, fallback, redaction and accounting therefore apply to agent calls too. Async only.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, ConfigDict

from unfold.core.errors import ErrorCode, GenerationError
from unfold.domain.enums import ModelCapability, Priority
from unfold.llm.schemas import LLMClient, LLMRequest, LLMResponse, Message, ToolCall, ToolSpec


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in (None, "text") and "text" in block:
                parts.append(str(block["text"]))
        return "".join(parts)
    return "" if content is None else str(content)


def to_tool_specs(tools: Sequence[Any]) -> list[ToolSpec]:
    """LangChain tools / pydantic classes / OpenAI tool dicts / callables → `ToolSpec`."""
    specs: list[ToolSpec] = []
    for tool in tools:
        oa = convert_to_openai_tool(tool)
        fn = oa.get("function", {})
        specs.append(
            ToolSpec(
                name=str(fn.get("name", "")),
                description=str(fn.get("description", "")),
                parameters=dict(fn.get("parameters") or {"type": "object", "properties": {}}),
            )
        )
    return specs


def to_gateway_messages(messages: Sequence[BaseMessage]) -> tuple[str, list[Message]]:
    """Split LangChain messages into (system prompt, gateway messages)."""
    system: list[str] = []
    out: list[Message] = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            text = _text_of(msg.content)
            if text:
                system.append(text)
        elif isinstance(msg, HumanMessage):
            out.append(Message(role="user", content=_text_of(msg.content)))
        elif isinstance(msg, AIMessage):
            calls = [
                ToolCall(
                    id=str(tc.get("id") or f"call_{i}"), name=tc["name"], arguments=dict(tc.get("args") or {})
                )
                for i, tc in enumerate(msg.tool_calls)
            ]
            out.append(Message(role="assistant", content=_text_of(msg.content), tool_calls=calls))
        elif isinstance(msg, ToolMessage):
            out.append(
                Message(
                    role="tool",
                    content=_text_of(msg.content),
                    tool_call_id=msg.tool_call_id,
                    name=msg.name,
                )
            )
        else:  # ChatMessage / FunctionMessage etc.: treat as user text
            out.append(Message(role="user", content=_text_of(msg.content)))
    return "\n\n".join(system), out


def to_ai_message(resp: LLMResponse) -> AIMessage:
    return AIMessage(
        content=resp.text,
        tool_calls=[
            {"name": c.name, "args": dict(c.arguments), "id": c.id, "type": "tool_call"}
            for c in resp.tool_calls
        ],
        usage_metadata={
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
            "total_tokens": resp.usage.input_tokens + resp.usage.output_tokens,
        },
        response_metadata={
            "model_name": resp.model,
            "provider": resp.provider,
            "finish_reason": resp.finish_reason,
            "cached": resp.cached,
        },
    )


def _tool_choice_instruction(tool_choice: Any, tools: Sequence[ToolSpec]) -> str:
    """LLMRequest has no tool_choice field (contract CR); express forced tool use as an instruction."""
    if not tools or tool_choice in (None, "auto", "none", False):
        return ""
    if tool_choice in ("any", "required", True):
        return "You MUST respond by calling exactly one of the available tools; do not answer in plain text."
    name = tool_choice
    if isinstance(tool_choice, dict):
        name = (tool_choice.get("function") or {}).get("name") or tool_choice.get("name")
    if isinstance(name, str) and any(t.name == name for t in tools):
        return f"You MUST respond by calling the `{name}` tool; do not answer in plain text."
    return ""


class GatewayChatModel(BaseChatModel):
    """`BaseChatModel` backed by `LLMClient` (normally `LLMGateway`)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    gateway: Any  # LLMClient (Protocol; not pydantic-validatable)
    capability: ModelCapability = ModelCapability.REASONING
    priority: Priority = Priority.P1
    purpose: str = "agent"
    job_id: str | None = None
    project_id: str | None = None
    temperature: float = 0.2
    max_output_tokens: int = 4096

    @property
    def _llm_type(self) -> str:
        return "unfold-gateway"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"capability": self.capability.value, "priority": int(self.priority), "purpose": self.purpose}

    def build_request(
        self,
        messages: Sequence[BaseMessage],
        *,
        tools: Sequence[Any] | None = None,
        tool_choice: Any = None,
    ) -> LLMRequest:
        system, converted = to_gateway_messages(messages)
        specs = to_tool_specs(tools or [])
        instruction = _tool_choice_instruction(tool_choice, specs)
        if instruction:
            system = f"{system}\n\n{instruction}" if system else instruction
        return LLMRequest(
            purpose=self.purpose,
            capability=self.capability,
            priority=self.priority,
            system=system,
            messages=converted,
            tools=specs,
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
            job_id=self.job_id,
            project_id=self.project_id,
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise GenerationError(
            "GatewayChatModel is async-only: use ainvoke/astream (agents run on the event loop)",
            code=ErrorCode.INTERNAL_ERROR,
        )

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        req = self.build_request(messages, tools=kwargs.get("tools"), tool_choice=kwargs.get("tool_choice"))
        client: LLMClient = self.gateway
        resp = await client.generate(req)
        message = to_ai_message(resp)
        return ChatResult(
            generations=[ChatGeneration(message=message)],
            llm_output={"model_name": resp.model, "provider": resp.provider},
        )

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        formatted = [convert_to_openai_tool(t) for t in tools]
        return self.bind(tools=formatted, tool_choice=tool_choice, **kwargs)

    def with_structured_output(
        self,
        schema: dict[str, Any] | type,
        *,
        include_raw: bool = False,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, dict[str, Any] | BaseModel]:
        """Pydantic schemas use the gateway's native structured output (validated + repaired);
        dict/TypedDict schemas fall back to LangChain's tool-calling implementation."""
        if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
            return super().with_structured_output(schema, include_raw=include_raw, **kwargs)
        model_cls: type[BaseModel] = schema

        async def _run(value: LanguageModelInput) -> Any:
            messages = self._convert_input(value).to_messages()
            req = self.build_request(messages)
            client: LLMClient = self.gateway
            if not include_raw:
                return await client.generate_structured(req, model_cls)
            try:
                parsed = await client.generate_structured(req, model_cls)
            except Exception as exc:
                return {"raw": None, "parsed": None, "parsing_error": exc}
            raw = AIMessage(content=parsed.model_dump_json())
            return {"raw": raw, "parsed": parsed, "parsing_error": None}

        def _sync(_: LanguageModelInput) -> Any:
            raise GenerationError(
                "GatewayChatModel is async-only: use ainvoke", code=ErrorCode.INTERNAL_ERROR
            )

        return RunnableLambda(_sync, afunc=_run, name=f"GatewayStructured[{model_cls.__name__}]")
