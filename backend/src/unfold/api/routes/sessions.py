"""Anonymous sessions (LLD §20, PRD §3.8): random token in an HttpOnly cookie; only its SHA-256 is stored."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from unfold.api.deps import RuntimeDep, SessionDep, enforce_ip_limits
from unfold.api.schemas import ProjectSummary, SessionCurrentOut, SessionOut
from unfold.core.config import Settings
from unfold.core.ids import new_session_token, token_hash
from unfold.persistence.postgres.models import AnalysisJobRow, ProjectRow, SessionRow

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
    row = SessionRow(
        token_hash=token_hash(token),
        created_at=now,
        last_active_at=now,
        expires_at=now + timedelta(seconds=rt.settings.session_ttl_seconds),
        status="active",
    )
    async with rt.db.transaction() as s:
        s.add(row)
    _set_cookie(response, rt.settings, token, row.expires_at)
    return SessionOut(session_id=str(row.id), created_at=row.created_at, expires_at=row.expires_at)


@router.get("/current", response_model=SessionCurrentOut)
async def current_session(rt: RuntimeDep, session: SessionDep) -> SessionCurrentOut:
    async with rt.db.session() as s:
        rows = (
            await s.execute(
                select(ProjectRow, AnalysisJobRow)
                .join(AnalysisJobRow, AnalysisJobRow.project_id == ProjectRow.id)
                .where(ProjectRow.session_id == session.id, ProjectRow.deleting_at.is_(None))
                .order_by(ProjectRow.created_at.desc(), AnalysisJobRow.created_at.desc())
            )
        ).all()
    seen: set[str] = set()
    projects: list[ProjectSummary] = []
    for project, job in rows:
        if str(project.id) in seen:
            continue
        seen.add(str(project.id))
        projects.append(
            ProjectSummary(
                id=str(project.id),
                name=project.detected_name,
                input_type=project.input_type,
                source_locator=project.source_locator,
                status=job.status,
                current_stage=job.current_stage,
                created_at=project.created_at,
            )
        )
    return SessionCurrentOut(
        session_id=str(session.id),
        created_at=session.created_at,
        expires_at=session.expires_at,
        projects=projects,
    )


@router.post("/current/touch", response_model=SessionOut)
async def touch_session(rt: RuntimeDep, session: SessionDep) -> SessionOut:
    """Sliding expiry, capped at created_at + max lifetime (the PRD requires data to expire)."""
    now = datetime.now(UTC)
    async with rt.db.transaction() as s:
        row = await s.get(SessionRow, session.id, with_for_update=True)
        assert row is not None
        cap = row.created_at + timedelta(seconds=rt.settings.session_max_lifetime_seconds)
        row.expires_at = min(now + timedelta(seconds=rt.settings.session_ttl_seconds), cap)
        row.last_active_at = now
        expires_at = row.expires_at
    return SessionOut(session_id=str(session.id), created_at=session.created_at, expires_at=expires_at)
