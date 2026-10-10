"""Chat turn orchestration (LLD §18): load history and project facts, run the answer generator, persist the turn.

The user message and the answer are written together after generation succeeds, so a failed or cancelled
generation leaves no half turn behind and a client retry is clean.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from sqlalchemy import func, select

from unfold.chat.answer import AnswerRequest, generate_answer
from unfold.chat.models import ChatEvent
from unfold.core.errors import ErrorCode, NotFound, ProjectNotReady, UnfoldError
from unfold.domain.enums import ChatMode
from unfold.domain.models import SessionProfile
from unfold.guide.generate import project_facts
from unfold.guide.models import SectionContent
from unfold.persistence.postgres.models import (
    ChatMessageRow,
    ChatSessionRow,
    GuideSectionRow,
    ProjectRow,
    SummaryRow,
)
from unfold.project_ir.snapshot import load_ir
from unfold.retrieval.hybrid import HybridRetriever
from unfold.runtime import Runtime

TITLE_CHARS = 60


async def _history(rt: Runtime, chat_id: uuid.UUID) -> list[tuple[str, str]]:
    limit = max(0, rt.settings.chat_history_messages)
    async with rt.db.session() as s:
        rows = (
            await s.execute(
                select(ChatMessageRow.role, ChatMessageRow.content)
                .where(ChatMessageRow.chat_session_id == chat_id)
                .order_by(ChatMessageRow.seq.desc())
                .limit(limit)
            )
        ).all()
    return [(role, str((content or {}).get("text", ""))) for role, content in reversed(rows)]


async def _section(rt: Runtime, project_id: uuid.UUID, key: str) -> tuple[str, str]:
    async with rt.db.session() as s:
        row = (
            await s.execute(
                select(GuideSectionRow).where(
                    GuideSectionRow.project_id == project_id, GuideSectionRow.section_key == key
                )
            )
        ).scalar_one_or_none()
    if row is None:
        raise NotFound("Section not found.")
    summary = SectionContent.model_validate(row.content).summary if row.content else ""
    return row.title, summary


def require_ready(project: ProjectRow) -> None:
    if not project.ir_object_key or not project.lexical_object_key:
        raise ProjectNotReady("This project has not finished analysis yet.")


async def _facts(rt: Runtime, project: ProjectRow) -> tuple[list[str], HybridRetriever]:
    require_ready(project)
    assert project.ir_object_key is not None
    ir = await load_ir(rt.objects, project.ir_object_key)
    async with rt.db.session() as s:
        subsystems = (
            (
                await s.execute(
                    select(SummaryRow).where(
                        SummaryRow.project_id == project.id, SummaryRow.level == "subsystem"
                    )
                )
            )
            .scalars()
            .all()
        )
    facts = project_facts(ir, [(r.title, [str(m) for m in r.members or []]) for r in subsystems])
    return facts, HybridRetriever.for_project(rt, str(project.id), ir, project.lexical_object_key)


async def run_turn(
    rt: Runtime,
    chat: ChatSessionRow,
    project: ProjectRow,
    *,
    text: str,
    mode: ChatMode,
    section_key: str | None,
    transport: str,
    retriever: HybridRetriever | None = None,
) -> AsyncIterator[ChatEvent]:
    """Stream one turn's events. Raises `UnfoldError` on failure (the transport decides how to show it)."""
    if rt.llm is None:
        raise UnfoldError(
            "No language model is configured.",
            code=ErrorCode.PROVIDER_NOT_CONFIGURED,
            http_status=503,
            retryable=False,
        )
    facts, built = await _facts(rt, project)
    history = await _history(rt, chat.id)
    section = await _section(rt, project.id, section_key) if section_key else None
    user_message_id = uuid.uuid4()
    yield ChatEvent(type="chat.started", message_id=str(user_message_id))
    request = AnswerRequest(
        chat_id=str(chat.id),
        project_id=str(project.id),
        question=text,
        mode=mode,
        history=history,
        facts=facts,
        profile=SessionProfile(technical_level=project.technical_level, instruction=project.user_instruction),
        section=section,
    )
    async for event in generate_answer(
        rt.llm,
        retriever or built,
        request,
        context_tokens=rt.settings.chat_context_tokens,
        transport=transport,
    ):
        if event.type != "response.completed":
            yield event
            continue
        assistant_id, seq = await _persist_turn(
            rt, chat.id, user_message_id, text, mode, event, section_key=section_key
        )
        yield event.model_copy(update={"message_id": str(assistant_id), "seq": seq})


async def _persist_turn(
    rt: Runtime,
    chat_id: uuid.UUID,
    user_message_id: uuid.UUID,
    question: str,
    mode: ChatMode,
    done: ChatEvent,
    *,
    section_key: str | None,
) -> tuple[uuid.UUID, int]:
    assistant_id = uuid.uuid4()
    async with rt.db.transaction() as s:
        chat = await s.get(ChatSessionRow, chat_id, with_for_update=True)  # serialises seq per chat
        if chat is None:
            raise NotFound("Chat not found.")
        last = (
            await s.execute(
                select(func.coalesce(func.max(ChatMessageRow.seq), 0)).where(
                    ChatMessageRow.chat_session_id == chat_id
                )
            )
        ).scalar_one()
        s.add(
            ChatMessageRow(
                id=user_message_id,
                chat_session_id=chat_id,
                seq=int(last) + 1,
                role="user",
                mode=mode.value,
                content={"text": question},
                evidence=[],
                model_metadata={"section_key": section_key} if section_key else {},
            )
        )
        s.add(
            ChatMessageRow(
                id=assistant_id,
                chat_session_id=chat_id,
                seq=int(last) + 2,
                role="assistant",
                mode=mode.value,
                content={"text": done.text or ""},
                evidence=[e.model_dump(mode="json") for e in done.evidence or []],
                model_metadata={"grounding": done.grounding or {}, "timings": done.timings or {}},
            )
        )
        if not chat.title:
            chat.title = " ".join(question.split())[:TITLE_CHARS]
    return assistant_id, int(last) + 2
