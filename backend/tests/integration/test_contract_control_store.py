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
from unfold.domain.records import (
    ArtifactRec,
    ChatMessageRec,
    ChatRec,
    DiagramRec,
    JobRec,
    ProjectRec,
    SectionRec,
    SessionRec,
    SummaryRec,
)
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


# --- phase 3 records: delete marker, typed jobs, chats, exports ---------------------------------------------------


def _mark_deleting(rec: ProjectRec) -> bool:
    rec.deleting_at = datetime.now(UTC)
    return True


def _finish(job: JobRec) -> bool:
    job.status = "succeeded"
    job.completed_at = datetime.now(UTC)
    return True


async def test_delete_marker_moves_a_project_between_the_sweeper_lists(control: ControlStore) -> None:
    s = await _session(control)
    gone = _project(s, expires_at=datetime.now(UTC) - timedelta(minutes=1))
    await control.create_project(gone, None)
    assert gone.id in {p.id for p in await control.expired_projects(datetime.now(UTC), 500)}
    assert gone.id not in {p.id for p in await control.deleting_projects(500)}

    marked = await control.update_project(gone.id, _mark_deleting)
    assert marked.deleting_at is not None
    assert gone.id not in {p.id for p in await control.expired_projects(datetime.now(UTC), 500)}
    deleting = {p.id: p for p in await control.deleting_projects(500)}
    assert gone.id in deleting and deleting[gone.id].deleting_at is not None
    stored = await control.get_project(gone.id)
    assert stored is not None and stored.deleting_at is not None

    await control.delete_project_data(gone.id)
    assert gone.id not in {p.id for p in await control.deleting_projects(500)}
    await control.delete_session(s.token_hash)


async def test_recoverable_jobs_skips_a_deleting_project(control: ControlStore) -> None:
    s = await _session(control)
    live_p, live = await _project_with_job(control, s)
    dead_p, dead = await _project_with_job(control, s)
    await control.update_project(dead_p.id, _mark_deleting)
    found = {
        j.id
        for j in await control.recoverable_jobs(
            queued_before=datetime.now(UTC) + timedelta(minutes=1),
            heartbeat_before=datetime.now(UTC),
            limit=100_000,
        )
    }
    assert live.id in found and dead.id not in found
    for p in (live_p, dead_p):
        await control.delete_project_data(p.id)
    await control.delete_session(s.token_hash)


async def test_typed_jobs_and_a_single_regeneration_per_project(control: ControlStore) -> None:
    s = await _session(control)
    p, analysis = await _project_with_job(control, s)
    await control.update_job(analysis.id, _finish)
    assert await control.latest_job(p.id, "regenerate") is None

    first, created = await control.add_regeneration(
        _job(
            p, kind="regenerate", section_key="overview", created_at=datetime.now(UTC) + timedelta(seconds=1)
        )
    )
    assert created and first.kind == "regenerate" and first.section_key == "overview"
    again, created_again = await control.add_regeneration(_job(p, kind="regenerate", section_key="flow"))
    assert not created_again and again.id == first.id  # one at a time, whichever chapter was asked for

    latest_analysis = await control.latest_job(p.id, "analysis")
    latest_regen = await control.latest_job(p.id, "regenerate")
    latest_any = await control.latest_job(p.id)
    assert latest_analysis is not None and latest_analysis.id == analysis.id
    assert latest_regen is not None and latest_regen.id == first.id and latest_regen.section_key == "overview"
    assert latest_any is not None and latest_any.id == first.id
    assert [j.kind for j in await control.jobs_of_project(p.id)] == ["analysis", "regenerate"]
    # the session's project listing shows the analysis, never the regeneration
    listed = await control.projects_of_session(s.id)
    assert [(x.id, j.id if j else None) for x, j in listed] == [(p.id, analysis.id)]

    await control.update_job(first.id, _finish)
    nxt, created_next = await control.add_regeneration(_job(p, kind="regenerate", section_key="flow"))
    assert created_next and nxt.id != first.id and nxt.section_key == "flow"
    await control.delete_project_data(p.id)
    await control.delete_session(s.token_hash)


async def test_regeneration_is_exclusive_under_concurrency(control: ControlStore) -> None:
    s = await _session(control)
    p, analysis = await _project_with_job(control, s)
    await control.update_job(analysis.id, _finish)
    results = await asyncio.gather(
        *(control.add_regeneration(_job(p, kind="regenerate", section_key="overview")) for _ in range(4))
    )
    assert sum(1 for _, created in results if created) == 1
    assert len({job.id for job, _ in results}) == 1
    await control.delete_project_data(p.id)
    await control.delete_session(s.token_hash)


async def test_regeneration_counts_against_the_caps_and_a_deleting_project_is_refused(
    control: ControlStore,
) -> None:
    s = await _session(control)
    p, analysis = await _project_with_job(control, s)
    await control.update_job(analysis.id, _finish)
    regen, _ = await control.add_regeneration(
        _job(p, kind="regenerate", section_key="a"), max_active_per_session=1
    )
    with pytest.raises(RateLimited):  # the session already has its one active job
        await _project_with_job(control, s, max_active_per_session=1)
    await control.update_job(regen.id, _finish)
    q, _ = await _project_with_job(control, s, max_active_per_session=1)

    await control.update_project(p.id, _mark_deleting)
    with pytest.raises(NotFound):
        await control.add_regeneration(_job(p, kind="regenerate", section_key="b"))
    for x in (p, q):
        await control.delete_project_data(x.id)
    await control.delete_session(s.token_hash)


async def test_section_research_and_verification_round_trip(control: ControlStore) -> None:
    s = await _session(control)
    p, _ = await _project_with_job(control, s)
    await control.replace_plan(p.id, {}, "v2", [SectionRec(p.id, "a", "A", 0)])

    def store(rec: SectionRec) -> bool:
        rec.research = {"coverage": 0.5, "findings": [{"statement": "x"}]}
        rec.verification = {"rounds": 1, "gaps": []}
        return True

    await control.update_section(p.id, "a", store)
    got = await control.get_section(p.id, "a")
    assert got is not None
    assert got.research == {"coverage": 0.5, "findings": [{"statement": "x"}]}
    assert got.verification == {"rounds": 1, "gaps": []}
    await control.delete_project_data(p.id)
    await control.delete_session(s.token_hash)


def _chat(p: ProjectRec, **kw: object) -> ChatRec:
    now = datetime.now(UTC)
    base: dict[str, object] = {"id": _uid(), "project_id": p.id, "created_at": now, "updated_at": now}
    return ChatRec(**{**base, **kw})  # type: ignore[arg-type]


def _turn(chat: ChatRec, question: str) -> tuple[ChatMessageRec, ChatMessageRec]:
    now = datetime.now(UTC)
    common: dict[str, object] = {"chat_id": chat.id, "created_at": now}
    return (
        ChatMessageRec(id=_uid(), role="user", content={"text": question}, **common),  # type: ignore[arg-type]
        ChatMessageRec(
            id=_uid(),
            role="assistant",
            content={"text": f"answer to {question}"},
            evidence=[{"path": "a.py", "start_line": 1, "end_line": 2}],
            model_metadata={"grounding": {"claims": 1}},
            **common,  # type: ignore[arg-type]
        ),
    )


async def test_chats_cap_ordering_and_messages(control: ControlStore) -> None:
    s = await _session(control)
    p, _ = await _project_with_job(control, s)
    first, second = _chat(p), _chat(p)
    await control.create_chat(first, max_per_project=2)
    await asyncio.sleep(0.01)
    await control.create_chat(second, max_per_project=2)
    with pytest.raises(Conflict):
        await control.create_chat(_chat(p), max_per_project=2)

    got = await control.get_chat(first.id)
    assert got is not None and got.project_id == p.id and got.message_count == 0 and got.title is None
    assert await control.get_chat(_uid()) is None
    assert await control.chat_messages(first.id) == []

    user, assistant = _turn(first, "  Where is   split_text?  ")
    seq = await control.append_turn(first.id, user, assistant, title_chars=12)
    assert seq == 2
    user2, assistant2 = _turn(first, "And the second?")
    assert await control.append_turn(first.id, user2, assistant2, title_chars=12) == 4

    msgs = await control.chat_messages(first.id)
    assert [m.seq for m in msgs] == [1, 2, 3, 4]
    assert [m.role for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[1].evidence == [{"path": "a.py", "start_line": 1, "end_line": 2}]
    assert msgs[1].model_metadata == {"grounding": {"claims": 1}}
    assert msgs[0].content == {"text": "  Where is   split_text?  "}
    assert [m.seq for m in await control.chat_messages(first.id, after_seq=2)] == [3, 4]
    assert [m.seq for m in await control.chat_messages(first.id, last=3)] == [2, 3, 4]
    assert [m.seq for m in await control.chat_messages(first.id, after_seq=1, last=2)] == [3, 4]
    assert await control.chat_messages(first.id, last=0) == []

    refreshed = await control.get_chat(first.id)
    assert refreshed is not None and refreshed.message_count == 4 and refreshed.title == "Where is spl"
    chats = await control.chats_of_project(p.id)
    assert [c.id for c in chats] == [first.id, second.id]  # most recently active first
    assert [c.message_count for c in chats] == [4, 0]

    await control.delete_project_data(p.id)
    assert await control.get_chat(first.id) is None
    assert await control.chat_messages(first.id) == []
    await control.delete_session(s.token_hash)


async def test_chat_turns_get_distinct_contiguous_seq_under_concurrency(control: ControlStore) -> None:
    s = await _session(control)
    p, _ = await _project_with_job(control, s)
    chat = _chat(p)
    await control.create_chat(chat, max_per_project=5)
    await asyncio.gather(
        *(control.append_turn(chat.id, *_turn(chat, f"q{i}"), title_chars=20) for i in range(4))
    )
    assert [m.seq for m in await control.chat_messages(chat.id)] == list(range(1, 9))
    got = await control.get_chat(chat.id)
    assert got is not None and got.message_count == 8
    await control.delete_project_data(p.id)
    await control.delete_session(s.token_hash)


async def test_a_deleting_project_takes_no_new_chat_or_export(control: ControlStore) -> None:
    s = await _session(control)
    p, _ = await _project_with_job(control, s)
    await control.update_project(p.id, _mark_deleting)
    with pytest.raises(NotFound):
        await control.create_chat(_chat(p), max_per_project=5)
    now = datetime.now(UTC)
    rec = ArtifactRec(_uid(), p.id, "guide_md", now, now + timedelta(hours=1), content_type="text/markdown")
    with pytest.raises(NotFound):
        await control.add_artifact(rec, reuse_since=now - timedelta(minutes=10))
    await control.delete_project_data(p.id)
    await control.delete_session(s.token_hash)


def _artifact(p: ProjectRec, kind: str = "guide_md", **kw: object) -> ArtifactRec:
    now = datetime.now(UTC)
    base: dict[str, object] = {
        "id": _uid(),
        "project_id": p.id,
        "kind": kind,
        "created_at": now,
        "expires_at": now + timedelta(hours=1),
        "content_type": "text/markdown",
    }
    return ArtifactRec(**{**base, **kw})  # type: ignore[arg-type]


async def test_exports_reuse_pending_and_follow_their_lifecycle(control: ControlStore) -> None:
    s = await _session(control)
    p, _ = await _project_with_job(control, s)
    chat = _chat(p)
    await control.create_chat(chat, max_per_project=5)
    window = datetime.now(UTC) - timedelta(minutes=10)

    assert await control.pending_artifact(p.id, "guide_md", None, window) is None
    a = await control.add_artifact(_artifact(p), reuse_since=window)
    same = await control.add_artifact(_artifact(p), reuse_since=window)
    assert same.id == a.id  # an identical pending export is reused, not duplicated
    pending = await control.pending_artifact(p.id, "guide_md", None, window)
    assert pending is not None and pending.id == a.id
    other_kind = await control.add_artifact(_artifact(p, "guide_pdf"), reuse_since=window)
    for_chat = await control.add_artifact(_artifact(p, "chat_md", chat_id=chat.id), reuse_since=window)
    assert len({a.id, other_kind.id, for_chat.id}) == 3
    assert for_chat.chat_id == chat.id
    assert await control.pending_artifact(p.id, "chat_md", None, window) is None  # chat-bound only

    def ready(rec: ArtifactRec) -> bool:
        rec.status = "ready"
        rec.object_key = f"projects/{p.id}/exports/{rec.id}.md"
        rec.size_bytes = 42
        return True

    done = await control.update_artifact(a.id, ready)
    assert done.status == "ready" and done.size_bytes == 42
    fresh = await control.get_artifact(a.id)
    assert fresh is not None and fresh.status == "ready" and fresh.object_key == done.object_key
    assert await control.pending_artifact(p.id, "guide_md", None, window) is None
    new_one = await control.add_artifact(_artifact(p), reuse_since=window)
    assert new_one.id != a.id  # a finished export is not reused

    listed = await control.artifacts_of_project(p.id)
    assert {r.id for r in listed} == {a.id, other_kind.id, for_chat.id, new_one.id}
    assert [r.created_at for r in listed] == sorted((r.created_at for r in listed), reverse=True)
    with pytest.raises(NotFound):
        await control.update_artifact(_uid(), ready)

    await control.delete_artifact(a.id)
    assert await control.get_artifact(a.id) is None
    await control.delete_project_data(p.id)
    assert await control.get_artifact(other_kind.id) is None  # exports leave with their project
    await control.delete_session(s.token_hash)


async def test_expired_and_stale_pending_exports_are_found_by_the_sweeper(control: ControlStore) -> None:
    s = await _session(control)
    kept, _ = await _project_with_job(control, s)
    dying, _ = await _project_with_job(control, s)
    now = datetime.now(UTC)
    old = now - timedelta(minutes=30)
    window = old - timedelta(days=1)
    expired = await control.add_artifact(
        _artifact(kept, "guide_md", created_at=old, expires_at=now - timedelta(minutes=1), status="ready"),
        reuse_since=window,
    )
    stale = await control.add_artifact(_artifact(kept, "guide_pdf", created_at=old), reuse_since=window)
    fresh = await control.add_artifact(_artifact(kept, "chat_md"), reuse_since=window)
    orphan = await control.add_artifact(_artifact(dying, "guide_pdf", created_at=old), reuse_since=window)
    await control.update_project(dying.id, _mark_deleting)

    gone = {a.id for a in await control.expired_artifacts(now, 500)}
    assert expired.id in gone and stale.id not in gone
    restart = {
        a.id
        for a in await control.stale_pending_artifacts(
            created_before=now - timedelta(minutes=5), now=now, limit=500
        )
    }
    assert stale.id in restart
    assert fresh.id not in restart  # too young
    assert expired.id not in restart  # not pending
    assert orphan.id not in restart  # its project is being deleted
    for p in (kept, dying):
        await control.delete_project_data(p.id)
    await control.delete_session(s.token_hash)
