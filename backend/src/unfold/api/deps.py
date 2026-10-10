"""FastAPI dependencies: runtime access, anonymous session auth, ownership checks (LLD §20, HLD D9)."""

from __future__ import annotations

import ipaddress
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request

from unfold.api import proxy_auth
from unfold.core.errors import ErrorCode, NotFound, RateLimited, SessionError
from unfold.core.ids import token_hash
from unfold.domain.records import ChatRec, ProjectRec
from unfold.ports import session_rate_key
from unfold.runtime import Runtime

API_PREFIX = "/api/v1"


def get_runtime(request: Request) -> Runtime:
    rt: Runtime = request.app.state.runtime
    return rt


RuntimeDep = Annotated[Runtime, Depends(get_runtime)]


@dataclass(frozen=True)
class CurrentSession:
    id: str
    expires_at: datetime
    created_at: datetime
    token_hash: str = ""


def session_token_from(request: Request, cookie_name: str) -> str | None:
    """Cookie only: a header fallback would widen where the token can leak (audit S-7)."""
    return request.cookies.get(cookie_name)


def _bucket(host: str) -> str:
    """Normalised to a rate-limit key: IPv6 clients share their /64, so rotating addresses inside one allocation
    does not mint new buckets. A host that is not an IP (unix socket, test transport) keeps its own capped key
    instead of collapsing every such client into one bucket."""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return host[:64] or "unknown"
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped  # ::ffff:a.b.c.d is an IPv4 client, not a member of ::/64
    if isinstance(addr, ipaddress.IPv6Address):
        return str(ipaddress.ip_network(f"{addr}/64", strict=False).network_address) + "/64"
    return str(addr)


def client_ip(request: Request) -> str:
    """Rate-limit key of the caller. With `PROXY_HMAC_SECRET` set (AWS) it is the address the Vercel proxy
    signed, or the shared `unverified` bucket; otherwise the peer address as rewritten by the trusted-proxy
    middleware. A forged X-Forwarded-For is never honoured."""
    runtime: Runtime | None = getattr(getattr(request.scope.get("app"), "state", None), "runtime", None)
    key = runtime.settings.proxy_hmac_secret if runtime is not None else None
    if key is not None:
        ip = proxy_auth.verified_ip(request.headers.get(proxy_auth.HEADER), key.get_secret_value().encode())
        return _bucket(ip) if ip else proxy_auth.UNVERIFIED
    return _bucket(request.client.host if request.client else "")


async def require_session(request: Request, rt: RuntimeDep) -> CurrentSession:
    token = session_token_from(request, rt.settings.session_cookie_name)
    if not token or len(token) > 200:
        raise SessionError("A session is required. Create one with POST /api/v1/sessions.")
    row = await rt.control.session_by_token(token_hash(token))
    if row is None:
        raise SessionError("Unknown session.", code=ErrorCode.SESSION_EXPIRED)
    # compared on every read: a store's own expiry (TTL) is a storage backstop and may lag by days
    if row.status != "active" or row.expires_at <= datetime.now(UTC):
        raise SessionError("Your session has expired.", code=ErrorCode.SESSION_EXPIRED)
    return CurrentSession(row.id, row.expires_at, row.created_at, row.token_hash)


SessionDep = Annotated[CurrentSession, Depends(require_session)]


def parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise NotFound("Not found.") from exc


async def owned_project(rt: Runtime, session: CurrentSession, project_id: str) -> ProjectRec:
    """404 (not 403) on foreign ids so project ids are not enumerable. Expired or deleting counts as missing."""
    row = await rt.control.get_project(str(parse_uuid(project_id)))
    if (
        row is None
        or row.session_id != session.id
        or row.deleting_at is not None
        or row.expires_at <= datetime.now(UTC)
    ):
        raise NotFound("Project not found.")
    return row


async def owned_chat(rt: Runtime, session: CurrentSession, chat_id: str) -> tuple[ChatRec, ProjectRec]:
    """The chat and its project; 404 unless the project is the session's and alive (same rule as projects)."""
    chat = await rt.control.get_chat(str(parse_uuid(chat_id)))
    if chat is None:
        raise NotFound("Chat not found.")
    try:
        project = await owned_project(rt, session, chat.project_id)
    except NotFound:
        raise NotFound("Chat not found.") from None
    return chat, project


async def enforce_rate_limit(
    rt: Runtime, session: CurrentSession, bucket: str, limit: int, window_s: int
) -> None:
    if not await rt.limiter.hit(session_rate_key(bucket, session.id), limit, window_s):
        raise RateLimited(f"Too many requests; limit is {limit} per {window_s // 60 or 1} minute(s).")


async def enforce_ip_limits(
    rt: Runtime, request: Request, bucket: str, per_ip: int, global_limit: int, window_s: int = 3600
) -> None:
    """Per-IP and process-wide ceilings for unauthenticated or session-bypassing creation (audit S-1)."""
    if not await rt.limiter.hit(f"{bucket}:ip:{client_ip(request)}", per_ip, window_s):
        raise RateLimited("Too many requests from your network; try again later.")
    if not await rt.limiter.hit(f"{bucket}:global", global_limit, window_s):
        raise RateLimited("The service is busy; try again later.")
