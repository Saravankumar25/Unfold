"""Every stage reached is timestamped in `stage_progress.stages` (F-1: a shallow copy hid later stages)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from unfold.core.ids import token_hash
from unfold.domain.enums import PIPELINE, JobStage
from unfold.orchestration.progress import ProgressReporter
from unfold.persistence.postgres.models import AnalysisJobRow, ProjectRow, SessionRow
from unfold.runtime import Runtime

pytestmark = pytest.mark.integration


async def test_every_stage_is_timestamped_and_survives_degraded(runtime: Runtime) -> None:
    now = datetime.now(UTC)
    project_id = uuid.uuid4()
    async with runtime.db.transaction() as s:
        session = SessionRow(
            token_hash=token_hash(uuid.uuid4().hex),
            created_at=now,
            last_active_at=now,
            expires_at=now + timedelta(hours=1),
            status="active",
        )
        s.add(session)
        await s.flush()
        s.add(
            ProjectRow(
                id=project_id,
                session_id=session.id,
                input_type="zip",
                source_locator="x.zip",
                detected_name="x",
                technical_level="intermediate",
                analysis_version="test",
                upload_object_key=f"projects/{project_id}/upload/source.zip",
                expires_at=now + timedelta(hours=1),
            )
        )
        await s.flush()
        job = AnalysisJobRow(
            project_id=project_id, status="queued", current_stage="RECEIVED", stage_progress={}, attempt=0
        )
        s.add(job)
        await s.flush()
        job_id = str(job.id)
    try:
        reporter = ProgressReporter(runtime.db, runtime.progress, str(project_id), job_id)
        assert await reporter.start(alive_within=timedelta(minutes=2)) == 1
        stages = [s for s in PIPELINE if s not in (JobStage.RECEIVED, JobStage.READY)][:6]
        for st in stages:
            await reporter.stage(st)
        await reporter.degraded("graph")
        await reporter.stage(JobStage.INDEXING_LEXICAL)
        async with runtime.db.session() as s:
            row = await s.get(AnalysisJobRow, uuid.UUID(job_id))
        assert row is not None
        recorded = row.stage_progress["stages"]
        assert set(recorded) == {s.value for s in stages} | {JobStage.INDEXING_LEXICAL.value}
        assert row.stage_progress["degraded"] == ["graph"]
    finally:
        async with runtime.db.transaction() as s:
            job = await s.get(AnalysisJobRow, uuid.UUID(job_id))
            if job is not None:
                await s.delete(job)
            proj = await s.get(ProjectRow, project_id)
            if proj is not None:
                await s.delete(proj)
