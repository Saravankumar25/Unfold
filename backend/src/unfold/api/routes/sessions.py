"""Anonymous sessions (LLD §20, PRD §3.8): random token in an HttpOnly cookie; only its SHA-256 is stored."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request, Response

from unfold.api.deps import RuntimeDep, SessionDep, enforce_ip_limits
from unfold.api.schemas import ProjectSummary, SessionCurrentOut, SessionOut
from unfold.core.config import Settings
from unfold.core.ids import new_session_token, token_hash
from unfold.domain.records import SessionRec

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _set_cookie(response: Response, settings: Settings, token: str, expires_at: datetime) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.session_max_lifetime_seconds,
        path="/",
    )


@router.post("", status_code=201, response_model=SessionOut)
async def create_session(request: Request, response: Response, rt: RuntimeDep) -> SessionOut:
    cfg = rt.settings
    await enforce_ip_limits(
        rt, request, "sessions", cfg.max_sessions_per_ip_per_hour, cfg.max_sessions_per_hour
    )
    token = new_session_token()
    now = datetime.now(UTC)
    row = await rt.control.create_session(
        token_hash(token), now, now + timedelta(seconds=rt.settings.session_ttl_seconds)
    )
    _set_cookie(response, rt.settings, token, row.expires_at)
    return SessionOut(session_id=row.id, created_at=row.created_at, expires_at=row.expires_at)


@router.get("/current", response_model=SessionCurrentOut)
async def current_session(rt: RuntimeDep, session: SessionDep) -> SessionCurrentOut:
    now = datetime.now(UTC)
    projects = [
        ProjectSummary(
            id=project.id,
            name=project.detected_name,
            input_type=project.input_type,
            source_locator=project.source_locator,
            status=job.status,
            current_stage=job.current_stage,
            created_at=project.created_at,
        )
        for project, job in await rt.control.projects_of_session(session.id)
        if job is not None and project.expires_at > now  # no job: an upload that was never completed
    ]
    return SessionCurrentOut(
        session_id=session.id,
        created_at=session.created_at,
        expires_at=session.expires_at,
        projects=projects,
    )


@router.post("/current/touch", response_model=SessionOut)
async def touch_session(rt: RuntimeDep, session: SessionDep) -> SessionOut:
    """Sliding expiry, capped at created_at + max lifetime (the PRD requires data to expire)."""
    now = datetime.now(UTC)
    ttl = timedelta(seconds=rt.settings.session_ttl_seconds)
    cap_span = timedelta(seconds=rt.settings.session_max_lifetime_seconds)

    def slide(row: SessionRec) -> bool | None:
        row.expires_at = min(now + ttl, row.created_at + cap_span)
        row.last_active_at = now
        return True

    row = await rt.control.update_session(session.token_hash, slide)
    return SessionOut(session_id=session.id, created_at=session.created_at, expires_at=row.expires_at)
