"""FastAPI dependencies: runtime access, anonymous session auth, ownership checks (LLD §20, HLD D9)."""

from __future__ import annotations

import ipaddress
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select

from unfold.core.errors import ErrorCode, NotFound, RateLimited, SessionError
from unfold.core.ids import token_hash
from unfold.persistence.postgres.models import ChatSessionRow, ProjectRow, SessionRow
from unfold.runtime import Runtime


def get_runtime(request: Request) -> Runtime:
    rt: Runtime = request.app.state.runtime
    return rt


RuntimeDep = Annotated[Runtime, Depends(get_runtime)]


@dataclass(frozen=True)
class CurrentSession:
    id: uuid.UUID
    expires_at: datetime
    created_at: datetime


def session_token_from(request: Request, cookie_name: str) -> str | None:
    """Cookie only: a header fallback would widen where the token can leak (audit S-7)."""
    return request.cookies.get(cookie_name)


def client_ip(request: Request) -> str:
    """Peer address as rewritten by the trusted-proxy middleware; a forged X-Forwarded-For is never honoured.

    Normalised to a rate-limit key: IPv6 clients share their /64, so rotating addresses inside one allocation
    does not mint new buckets. A peer that is not an IP (unix socket, test transport) keeps its own capped key
    instead of collapsing every such client into one bucket.
    """
    host = request.client.host if request.client else ""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return host[:64] or "unknown"
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped  # ::ffff:a.b.c.d is an IPv4 client, not a member of ::/64
    if isinstance(addr, ipaddress.IPv6Address):
        return str(ipaddress.ip_network(f"{addr}/64", strict=False).network_address) + "/64"
    return str(addr)


async def require_session(request: Request, rt: RuntimeDep) -> CurrentSession:
    token = session_token_from(request, rt.settings.session_cookie_name)
    if not token or len(token) > 200:
        raise SessionError("A session is required. Create one with POST /api/v1/sessions.")
    async with rt.db.session() as s:
        row = (
            await s.execute(select(SessionRow).where(SessionRow.token_hash == token_hash(token)))
        ).scalar_one_or_none()
    if row is None:
        raise SessionError("Unknown session.", code=ErrorCode.SESSION_EXPIRED)
    if row.status != "active" or row.expires_at <= datetime.now(UTC):
        raise SessionError("Your session has expired.", code=ErrorCode.SESSION_EXPIRED)
    return CurrentSession(row.id, row.expires_at, row.created_at)


SessionDep = Annotated[CurrentSession, Depends(require_session)]


def parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise NotFound("Not found.") from exc


async def owned_project(rt: Runtime, session: CurrentSession, project_id: str) -> ProjectRow:
    """404 (not 403) on foreign ids so project ids are not enumerable."""
    pid = parse_uuid(project_id)
    async with rt.db.session() as s:
        row = await s.get(ProjectRow, pid)
    if row is None or row.session_id != session.id:
        raise NotFound("Project not found.")
    return row


async def owned_chat(rt: Runtime, session: CurrentSession, chat_id: str) -> tuple[ChatSessionRow, ProjectRow]:
    cid = parse_uuid(chat_id)
    async with rt.db.session() as s:
        chat = await s.get(ChatSessionRow, cid)
        project = await s.get(ProjectRow, chat.project_id) if chat else None
    if chat is None or project is None or project.session_id != session.id:
        raise NotFound("Chat not found.")
    return chat, project


async def enforce_rate_limit(
    rt: Runtime, session: CurrentSession, bucket: str, limit: int, window_s: int
) -> None:
    if not await rt.limiter.hit(f"{bucket}:{session.id}", limit, window_s):
        raise RateLimited(f"Too many requests; limit is {limit} per {window_s // 60 or 1} minute(s).")


async def enforce_ip_limits(
    rt: Runtime, request: Request, bucket: str, per_ip: int, global_limit: int, window_s: int = 3600
) -> None:
    """Per-IP and process-wide ceilings for unauthenticated or session-bypassing creation (audit S-1)."""
    if not await rt.limiter.hit(f"{bucket}:ip:{client_ip(request)}", per_ip, window_s):
        raise RateLimited("Too many requests from your network; try again later.")
    if not await rt.limiter.hit(f"{bucket}:global", global_limit, window_s):
        raise RateLimited("The service is busy; try again later.")
