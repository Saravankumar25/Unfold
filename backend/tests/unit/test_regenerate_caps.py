"""Chapter regeneration is not an analysis: the per-session active-job and global queue caps (which gate new
analyses) must not turn a one-chapter redo into a 429 once the project and session limiter slots are spent."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from unfold.api.main import create_app
from unfold.guide.models import ChapterPlan, GuidePlan
from unfold.runtime import Runtime
from unfold.worker.sqs_queue import SqsJobQueue


@pytest.fixture
async def app(aws: Any) -> AsyncIterator[FastAPI]:
    application = create_app(aws.settings, queue=SqsJobQueue(aws.settings))
    async with application.router.lifespan_context(application):
        yield application


async def test_regeneration_is_not_refused_by_the_analysis_caps(aws: Any, app: FastAPI) -> None:
    rt: Runtime = app.state.runtime
    cap = aws.settings.max_active_jobs_per_session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        assert (await client.post("/api/v1/sessions")).status_code == 201

        async def analyse(name: str) -> tuple[str, str]:
            res = await client.post(
                "/api/v1/projects/github", json={"url": f"https://github.com/octo/{name}"}
            )
            assert res.status_code == 202, res.text
            return res.json()["project_id"], res.json()["job_id"]

        ready, job_id = await analyse("ready")

        def done(job: Any) -> bool:
            job.status = "succeeded"
            return True

        await rt.control.update_job(job_id, done)
        plan = GuidePlan(
            estimated_read_minutes=1,
            sections=[
                ChapterPlan(key="overview", title="Overview", kind="backbone", purpose="p", target_minutes=1)
            ],
        )
        await rt.control.replace_plan(ready, plan.model_dump(), "2", [])
        for i in range(cap):  # the session now holds every active-analysis slot it may have
            await analyse(f"busy{i}")
        refused = await client.post("/api/v1/projects/github", json={"url": "https://github.com/octo/over"})
        assert refused.status_code == 429  # the cap is real for analyses

        regen = await client.post(f"/api/v1/projects/{ready}/sections/overview/regenerate")
        assert regen.status_code == 202, regen.text
        again = await client.post(f"/api/v1/projects/{ready}/sections/overview/regenerate")
        assert again.status_code == 202 and again.json()["job_id"] == regen.json()["job_id"]
