"""Chat routes (LLD §18, §20). One answer generator, two transports on the same route:

* `stream=false` (default): the turn is generated to completion and returned as one JSON body. This works behind
  any buffering proxy or function runtime.
* `stream=true`: the same events as NDJSON lines (`unfold.chat.models.ChatEvent`), flushed as they happen.

Everything that can fail before the first token (ownership, readiness, limits, history, the section key) is
resolved before the response starts, so those failures are ordinary HTTP errors in both modes; a failure after
that point is an `error` line in the stream, or the normal error response in JSON mode.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from unfold.api.deps import (
    RuntimeDep,
    SessionDep,
    enforce_ip_limits,
    enforce_rate_limit,
    owned_chat,
    owned_project,
)
from unfold.api.schemas import (
    ChatDetailOut,
    ChatMessageOut,
    ChatReplyOut,
    ChatSessionOut,
    ErrorEnvelope,
    PostChatMessage,
)
from unfold.chat.models import ChatEvent
from unfold.chat.service import require_ready, run_turn
from unfold.core.errors import ErrorCode, UnfoldError
from unfold.core.logging import get_logger
from unfold.domain.enums import ChatMode
from unfold.domain.models import EvidenceRef
from unfold.domain.records import ChatMessageRec, ChatRec
from unfold.runtime import Runtime

router = APIRouter(
    tags=["chat"],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}, 429: {"model": ErrorEnvelope}},
)
log = get_logger(__name__)


def _session_out(chat: ChatRec) -> ChatSessionOut:
    return ChatSessionOut(
        id=chat.id,
        project_id=chat.project_id,
        title=chat.title,
        message_count=chat.message_count,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
    )


def _message_out(row: ChatMessageRec) -> ChatMessageOut:
    return ChatMessageOut(
        id=row.id,
        seq=row.seq,
        role="assistant" if row.role == "assistant" else "user",
        mode=ChatMode(row.mode),
        text=str((row.content or {}).get("text", "")),
        evidence=[EvidenceRef.model_validate(e) for e in row.evidence or []],
        created_at=row.created_at,
    )


@router.post("/projects/{project_id}/chats", status_code=201, response_model=ChatSessionOut)
async def create_chat(project_id: str, rt: RuntimeDep, session: SessionDep) -> ChatSessionOut:
    project = await owned_project(rt, session, project_id)
    require_ready(project)
    now = datetime.now(UTC)
    chat = ChatRec(id=str(uuid.uuid4()), project_id=project.id, created_at=now, updated_at=now)
    await rt.control.create_chat(chat, max_per_project=rt.settings.max_chats_per_project)
    return _session_out(chat)


@router.get("/projects/{project_id}/chats", response_model=list[ChatSessionOut])
async def list_chats(project_id: str, rt: RuntimeDep, session: SessionDep) -> list[ChatSessionOut]:
    project = await owned_project(rt, session, project_id)
    return [_session_out(c) for c in await rt.control.chats_of_project(project.id)]


@router.get("/chats/{chat_id}", response_model=ChatDetailOut)
async def get_chat(chat_id: str, rt: RuntimeDep, session: SessionDep) -> ChatDetailOut:
    chat, _ = await owned_chat(rt, session, chat_id)
    rows = await rt.control.chat_messages(chat.id)
    chat.message_count = len(rows)
    return ChatDetailOut(chat=_session_out(chat), messages=[_message_out(r) for r in rows])


# --- transports -----------------------------------------------------------------------------------------------


async def _chained(first: ChatEvent, rest: AsyncGenerator[ChatEvent, None]) -> AsyncIterator[ChatEvent]:
    async with contextlib.aclosing(rest):  # releases the model stream when the client goes away
        yield first
        async for event in rest:
            yield event


async def _ndjson(events: AsyncIterator[ChatEvent]) -> AsyncIterator[bytes]:
    try:
        async for event in events:
            yield event.model_dump_json(exclude_none=True).encode() + b"\n"
    except UnfoldError as exc:
        log.warning("chat.stream_error", code=exc.code.value, http_status=exc.http_status)
        err = ChatEvent(type="error", code=exc.code.value, message=exc.message, retryable=exc.retryable)
    except Exception as exc:
        log.exception("chat.stream_failed", error_type=type(exc).__name__)
        err = ChatEvent(
            type="error", code=ErrorCode.INTERNAL_ERROR.value, message="Internal error.", retryable=True
        )
    else:
        return
    yield err.model_dump_json(exclude_none=True).encode() + b"\n"


async def _json_reply(rt: Runtime, chat_id: str, events: AsyncIterator[ChatEvent]) -> ChatReplyOut:
    user_id: str | None = None
    done: ChatEvent | None = None
    async for event in events:
        if event.type == "chat.started":
            user_id = event.message_id
        elif event.type == "response.completed":
            done = event
    if done is None or done.message_id is None or user_id is None or done.seq is None:
        raise UnfoldError("The answer did not complete.", code=ErrorCode.GENERATION_FAILED, retryable=True)
    rows = await rt.control.chat_messages(chat_id, after_seq=done.seq - 2)
    by_id = {r.id: r for r in rows}
    return ChatReplyOut(
        user_message=_message_out(by_id[user_id]),
        message=_message_out(by_id[done.message_id]),
        grounding=done.grounding or {},
        timings=done.timings or {},
    )


@router.post(
    "/chats/{chat_id}/messages",
    response_model=ChatReplyOut,
    responses={
        200: {
            "description": "`ChatReplyOut` JSON; with `stream=true`, `application/x-ndjson`: one `ChatEvent` per line",
            "content": {"application/x-ndjson": {"schema": ChatEvent.model_json_schema()}},
        }
    },
)
async def post_message(
    chat_id: str,
    body: PostChatMessage,
    request: Request,
    rt: RuntimeDep,
    session: SessionDep,
    stream: Annotated[bool, Query(description="NDJSON event lines instead of one JSON reply")] = False,
) -> ChatReplyOut | StreamingResponse:
    chat, project = await owned_chat(rt, session, chat_id)
    require_ready(project)
    cfg = rt.settings
    await enforce_rate_limit(rt, session, "chat", cfg.max_chat_messages_per_minute, 60)
    await enforce_rate_limit(rt, session, "chat_hour", cfg.max_chat_messages_per_session_per_hour, 3600)
    # the daily global ceiling protects the model quota; the per-IP one stops a single network using it all up
    await enforce_ip_limits(
        rt,
        request,
        "chat",
        cfg.max_chat_messages_per_ip_per_day,
        cfg.max_chat_messages_global_per_day,
        86_400,
    )
    events = run_turn(
        rt,
        chat,
        project,
        text=body.text,
        mode=body.mode,
        section_key=body.section_key,
        transport="ndjson" if stream else "json",
    )
    first = await anext(events)  # runs every pre-generation check; failures here are plain HTTP errors
    pipeline = _chained(first, events)
    if stream:
        return StreamingResponse(
            _ndjson(pipeline),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
    return await _json_reply(rt, chat.id, pipeline)
