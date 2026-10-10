"""The investigation engine's stages reach clients on the AWS profile exactly as on the local one: every stage of
the pipeline (21 with research and revision) is an ordered, replayable DynamoDB event carrying its index and total."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from unfold.domain.enums import PIPELINE, JobStage
from unfold.domain.records import JobRec, ProjectRec
from unfold.orchestration.progress import STAGE_TOTAL, ProgressReporter
from unfold.runtime import Runtime


def test_the_pipeline_has_the_engine_stages() -> None:
    assert STAGE_TOTAL == len(PIPELINE) == 21
    names = [s.value for s in PIPELINE]
    assert names.index("RESEARCHING_GUIDE") < names.index("GENERATING_GUIDE") < names.index("VERIFYING_GUIDE")
    assert names.index("VERIFYING_GUIDE") < names.index("REVISING_GUIDE") < names.index("GENERATING_VISUALS")


async def test_every_stage_is_an_ordered_event_with_its_index_and_total(aws: Any) -> None:
    rt = Runtime.create(aws.settings)
    now = datetime.now(UTC)
    session = await rt.control.create_session(uuid.uuid4().hex, now, now + timedelta(hours=1))
    project = ProjectRec(str(uuid.uuid4()), session.id, "zip", "beginner", "t", now, now + timedelta(hours=1))
    job = JobRec(str(uuid.uuid4()), project.id, now)
    await rt.control.create_project(project, job)
    reporter = ProgressReporter(rt.control, rt.progress, project.id, job.id)
    assert await reporter.start(alive_within=timedelta(minutes=2)) == 1

    path = [s for s in PIPELINE if s not in (JobStage.RECEIVED, JobStage.READY)]
    for stage in path:
        await reporter.stage(stage)
    await reporter.succeed()

    events = await rt.progress.read_after(project.id, 0, limit=100)
    seqs = [seq for seq, _ in events]
    assert seqs == list(range(1, len(path) + 2))  # contiguous: nothing lost, replayable from any cursor
    assert [e["stage"] for _, e in events] == [s.value for s in path] + ["READY"]
    assert {e["total"] for _, e in events} == {21}
    assert [e["index"] for _, e in events] == [s.order for s in path] + [JobStage.READY.order]
    assert events[-1][1]["type"] == "ready"
    # a client that returns after a restart resumes from its cursor
    tail = await rt.progress.read_after(project.id, seqs[-3], limit=100)
    assert [seq for seq, _ in tail] == seqs[-2:]

    stored = await rt.control.get_job(job.id)
    assert stored is not None and set(stored.stage_progress["stages"]) == {s.value for s in path}
