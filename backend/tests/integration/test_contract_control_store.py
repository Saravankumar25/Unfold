"""`ControlStore` contract: one suite, run against the DynamoDB adapter (in-process AWS) and the Postgres one.

The DynamoDB parametrisation needs no services and runs in the default test run; the Postgres one is an
integration test (`-m integration`, needs the compose stores). Both must satisfy every invariant the pipeline
relies on: exclusive claim, caps, idempotent replace and cleanup.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from unfold.core.errors import Conflict, NotFound, RateLimited
from unfold.domain.records import DiagramRec, JobRec, ProjectRec, SectionRec, SessionRec, SummaryRec
from unfold.ports import ControlStore
from unfold.runtime import Runtime


@pytest.fixture(params=["dynamodb", pytest.param("postgres", marks=pytest.mark.integration)])
def control(request: pytest.FixtureRequest) -> ControlStore:
    # sync on purpose: `runtime` is an async session fixture and cannot be requested from a running loop
    if request.param == "dynamodb":
        rt = Runtime.create(request.getfixturevalue("aws").settings)
    else:
        rt = request.getfixturevalue("runtime")
    store: ControlStore = rt.control
    return store


def _uid() -> str:
    return str(uuid.uuid4())


async def _session(control: ControlStore, *, hours: float = 1) -> SessionRec:
    now = datetime.now(UTC)
    return await control.create_session(
        uuid.uuid4().hex + uuid.uuid4().hex, now, now + timedelta(hours=hours)
    )


def _project(session: SessionRec, **kw: object) -> ProjectRec:
    now = datetime.now(UTC)
    base: dict[str, object] = {
        "id": _uid(),
        "session_id": session.id,
        "input_type": "github",
        "technical_level": "beginner",
        "analysis_version": "test",
        "created_at": now,
        "expires_at": now + timedelta(hours=1),
    }
    return ProjectRec(**{**base, **kw})  # type: ignore[arg-type]


def _job(project: ProjectRec, **kw: object) -> JobRec:
    base: dict[str, object] = {"id": _uid(), "project_id": project.id, "created_at": datetime.now(UTC)}
    return JobRec(**{**base, **kw})  # type: ignore[arg-type]


async def _project_with_job(
    control: ControlStore, session: SessionRec, **caps: int
) -> tuple[ProjectRec, JobRec]:
    p = _project(session)
    j = _job(p)
    await control.create_project(p, j, **caps)
    return p, j


async def test_session_roundtrip_update_and_expiry(control: ControlStore) -> None:
    s = await _session(control)
    got = await control.session_by_token(s.token_hash)
    assert got is not None and got.id == s.id and got.status == "active"
    assert await control.session_by_token("0" * 64) is None

    later = datetime.now(UTC) + timedelta(hours=5)

    def slide(rec: SessionRec) -> bool:
        rec.expires_at = later
        return True

    updated = await control.update_session(s.token_hash, slide)
    assert abs((updated.expires_at - later).total_seconds()) < 1
    again = await control.session_by_token(s.token_hash)
    assert again is not None and abs((again.expires_at - later).total_seconds()) < 1

    dead = await _session(control, hours=-1)
    expired = {e.token_hash for e in await control.expired_sessions(datetime.now(UTC), 200)}
    assert dead.token_hash in expired and s.token_hash not in expired
    await control.delete_session(dead.token_hash)
    assert await control.session_by_token(dead.token_hash) is None
    await control.delete_session(s.token_hash)


async def test_project_job_and_listing(control: ControlStore) -> None:
    s = await _session(control)
    p1, j1 = await _project_with_job(control, s)
    await asyncio.sleep(0.01)
    p2, j2 = await _project_with_job(control, s)
    got = await control.get_project(p1.id)
    assert got is not None and got.session_id == s.id and got.technical_level == "beginner"
    assert (await control.get_job(j1.id)) is not None
    latest = await control.latest_job(p2.id)
    assert latest is not None and latest.id == j2.id and latest.status == "queued"

    listed = await control.projects_of_session(s.id)
    assert [p.id for p, _ in listed] == [p2.id, p1.id]  # newest first
    assert all(job is not None for _, job in listed)

    def named(rec: ProjectRec) -> None:
        rec.detected_name = "renamed"
        rec.stats = {"chunks": 3, "ratio": 0.5}

    after = await control.update_project(p1.id, named)
    assert after.detected_name == "renamed"
    fresh = await control.get_project(p1.id)
    assert fresh is not None and fresh.stats == {"chunks": 3, "ratio": 0.5}

    with pytest.raises(NotFound):
        await control.update_job(_uid(), lambda j: True)
    for p in (p1, p2):
        await control.delete_project_data(p.id)
    await control.delete_session(s.token_hash)


async def test_pending_upload_has_no_job_until_one_is_added(control: ControlStore) -> None:
    s = await _session(control)
    p = _project(s, input_type="zip", upload_object_key="projects/x/upload/source.zip")
    await control.create_project(p, None)
    assert await control.latest_job(p.id) is None
    listed = await control.projects_of_session(s.id)
    assert [(x.id, job) for x, job in listed] == [(p.id, None)]
    j = _job(p)
    await control.add_job(j)
    latest = await control.latest_job(p.id)
    assert latest is not None and latest.id == j.id
    with pytest.raises(
        Conflict
    ):  # the first job is exclusive: a concurrent or repeated completion is refused
        await control.add_job(_job(p))
    await control.delete_project_data(p.id)
    await control.delete_session(s.token_hash)


async def test_claim_is_exclusive_under_concurrency(control: ControlStore) -> None:
    s = await _session(control)
    p, j = await _project_with_job(control, s)
    winners: list[int] = []

    def claim_once(job: JobRec) -> bool | None:
        # the contract of the mutator: pure, and returning False means "do not write"
        if job.status == "running":
            return False
        job.status = "running"
        job.attempt += 1
        job.heartbeat_at = datetime.now(UTC)
        return True

    async def worker() -> None:
        before = await control.get_job(j.id)
        assert before is not None
        done = await control.update_job(j.id, claim_once)
        if done.attempt == 1 and done.status == "running":
            winners.append(1)

    await asyncio.gather(*(worker() for _ in range(6)))
    final = await control.get_job(j.id)
    assert final is not None and final.attempt == 1 and final.status == "running"  # one increment, not six
    await control.delete_project_data(p.id)
    await control.delete_session(s.token_hash)


async def test_active_job_cap_per_session_and_release(control: ControlStore) -> None:
    s = await _session(control)
    a, ja = await _project_with_job(control, s, max_active_per_session=2)
    b, _ = await _project_with_job(control, s, max_active_per_session=2)
    with pytest.raises(RateLimited):
        await _project_with_job(control, s, max_active_per_session=2)

    def finish(job: JobRec) -> bool:
        job.status = "succeeded"
        job.completed_at = datetime.now(UTC)
        return True

    await control.update_job(ja.id, finish)  # a finished job frees its slot
    c, _ = await _project_with_job(control, s, max_active_per_session=2)
    other = await _session(control)  # another session is unaffected
    d, _ = await _project_with_job(control, other, max_active_per_session=2)
    for p in (a, b, c, d):
        await control.delete_project_data(p.id)
    await control.delete_session(s.token_hash)
    await control.delete_session(other.token_hash)


async def test_deleting_a_project_releases_its_slot(control: ControlStore) -> None:
    s = await _session(control)
    a, _ = await _project_with_job(control, s, max_active_per_session=1)
    with pytest.raises(RateLimited):
        await _project_with_job(control, s, max_active_per_session=1)
    await control.delete_project_data(a.id)
    b, _ = await _project_with_job(control, s, max_active_per_session=1)
    await control.delete_project_data(b.id)
    await control.delete_session(s.token_hash)


async def test_recoverable_jobs_finds_stale_and_old_queued(control: ControlStore) -> None:
    s = await _session(control)
    now = datetime.now(UTC)
    old = await _project_with_job(control, s)
    fresh_p = _project(s)
    fresh = _job(
        fresh_p, created_at=now + timedelta(hours=1)
    )  # "created" after the cutoff: not yet old enough
    await control.create_project(fresh_p, fresh)
    stale_p, stale = await _project_with_job(control, s)

    def run(job: JobRec) -> bool:
        job.status = "running"
        job.heartbeat_at = now - timedelta(minutes=30)
        return True

    await control.update_job(stale.id, run)
    found = {
        j.id
        for j in await control.recoverable_jobs(
            queued_before=now + timedelta(minutes=1),
            heartbeat_before=now - timedelta(minutes=5),
            limit=100_000,
        )
    }
    assert old[1].id in found and stale.id in found and fresh.id not in found
    for p in (old[0], fresh_p, stale_p):
        await control.delete_project_data(p.id)
    await control.delete_session(s.token_hash)


async def test_checkpoints_are_idempotent(control: ControlStore) -> None:
    s = await _session(control)
    p, j = await _project_with_job(control, s)
    assert await control.checkpoints(j.id) == set()
    await control.put_checkpoint(j.id, "analyze", {"chunks": 3})
    await control.put_checkpoint(j.id, "analyze", {"chunks": 3})
    await control.put_checkpoint(j.id, "graph", {})
    assert await control.checkpoints(j.id) == {"analyze", "graph"}
    await control.delete_project_data(p.id)
    await control.delete_session(s.token_hash)


async def test_guide_content_replace_and_update(control: ControlStore) -> None:
    s = await _session(control)
    p, _ = await _project_with_job(control, s)
    pid = p.id
    await control.replace_summaries(
        pid,
        [
            SummaryRec(pid, "project", pid, "Proj", facts={"facts": {"score": 0.75}}),
            SummaryRec(pid, "subsystem", "s1", "API", members=["a.py"], facts={"cohesion": 0.5}),
            SummaryRec(pid, "subsystem", "s2", "DB", members=["b.py"]),
        ],
    )
    assert {r.key for r in await control.summaries(pid, "subsystem")} == {"s1", "s2"}
    await control.replace_summaries(pid, [SummaryRec(pid, "subsystem", "s2", "DB2")])
    rows = await control.summaries(pid)
    assert [(r.level, r.key, r.title) for r in rows] == [("subsystem", "s2", "DB2")]

    assert await control.get_plan(pid) is None
    secs = [SectionRec(pid, "overview", "Overview", 0), SectionRec(pid, "flow", "Flow", 1)]
    await control.replace_plan(pid, {"sections": [{"key": "overview"}, {"key": "flow"}]}, "v1", secs)
    assert (await control.get_plan(pid)) == {"sections": [{"key": "overview"}, {"key": "flow"}]}
    assert [x.section_key for x in await control.sections(pid)] == ["overview", "flow"]

    def write(rec: SectionRec) -> bool:
        rec.content = {"summary": "hi", "score": 0.5}
        rec.claims = [{"id": 1}]
        rec.status = "generating"
        rec.version += 1
        return True

    sec = await control.update_section(pid, "flow", write)
    assert sec.version == 2
    got = await control.get_section(pid, "flow")
    assert got is not None and got.content == {"summary": "hi", "score": 0.5} and got.status == "generating"
    assert await control.get_section(pid, "nope") is None

    await control.replace_plan(
        pid, {"sections": [{"key": "flow"}]}, "v1", [SectionRec(pid, "flow", "Flow", 0)]
    )
    assert [x.section_key for x in await control.sections(pid)] == ["flow"]  # stale section removed

    await control.replace_diagrams(
        pid,
        [
            DiagramRec("dg:1", pid, "architecture", "Arch", {"nodes": []}, "graph TD"),
            DiagramRec("dg:2", pid, "request_flow", "Flow", {"nodes": []}, "graph LR"),
        ],
    )
    assert {d.id for d in await control.diagrams(pid)} == {"dg:1", "dg:2"}
    assert [d.id for d in await control.diagrams(pid, "architecture")] == ["dg:1"]
    other = await control.get_diagram(pid, "dg:2")
    assert other is not None and other.mermaid == "graph LR"
    assert await control.get_diagram(_uid(), "dg:2") is None  # ids are scoped to their project
    await control.replace_diagrams(pid, [DiagramRec("dg:3", pid, "architecture", "A2", {}, "x")])
    assert {d.id for d in await control.diagrams(pid)} == {"dg:3"}
    await control.delete_project_data(pid)
    await control.delete_session(s.token_hash)


async def test_delete_project_data_is_complete_and_idempotent(control: ControlStore) -> None:
    s = await _session(control)
    p, j = await _project_with_job(control, s)
    await control.put_checkpoint(j.id, "analyze", {})
    await control.replace_summaries(p.id, [SummaryRec(p.id, "project", p.id, "P")])
    await control.replace_plan(p.id, {}, "v1", [SectionRec(p.id, "a", "A", 0)])
    await control.delete_project_data(p.id)
    await control.delete_project_data(p.id)  # a rerun after a crash is a no-op
    assert await control.get_project(p.id) is None
    assert await control.get_job(j.id) is None
    assert await control.summaries(p.id) == [] and await control.sections(p.id) == []
    assert await control.get_plan(p.id) is None
    assert await control.projects_of_session(s.id) == []
    await control.delete_session(s.token_hash)


async def test_expired_projects_are_listed_for_the_sweeper(control: ControlStore) -> None:
    s = await _session(control)
    gone = _project(s, expires_at=datetime.now(UTC) - timedelta(minutes=1))
    live = _project(s)
    await control.create_project(gone, None)
    await control.create_project(live, None)
    ids = {p.id for p in await control.expired_projects(datetime.now(UTC), 200)}
    assert gone.id in ids and live.id not in ids
    for p in (gone, live):
        await control.delete_project_data(p.id)
    await control.delete_session(s.token_hash)
