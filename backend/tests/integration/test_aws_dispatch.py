"""Dispatch, task-stopped and sweeper behaviour: duplicate delivery, capacity, retry/recovery, no lost jobs.

The control store, queue and object store are the AWS adapters over in-process AWS. The task runner is a
recording double (what ECS would do is exercised separately against moto ECS); everything the handlers decide is
real. Real RunTask quotas, STOPPED-event payloads for OOM and clientToken behaviour require AWS.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import pytest

from unfold.core.errors import UnfoldError
from unfold.domain.records import JobRec, ProjectRec
from unfold.runtime import Runtime
from unfold.worker.dispatch import Dispatcher, FargateRunner, RunningTask, heartbeat_line
from unfold.worker.sqs_queue import SqsJobQueue


class RecordingRunner:
    """Stands in for ECS: remembers what was started, and what is 'running'."""

    def __init__(self) -> None:
        self.tasks: dict[str, RunningTask] = {}
        self.started: list[tuple[str, str]] = []
        self.stopped: list[str] = []

    async def running(self) -> list[RunningTask]:
        return list(self.tasks.values())

    async def start(self, job_id: str, client_token: str) -> str:
        # ECS dedupes a repeated client token: the same token returns the task it already started
        for t in self.tasks.values():
            if (job_id, client_token) in self.started and t.job_id == job_id:
                return t.arn
        arn = f"arn:aws:ecs:us-east-1:123456789012:task/unfold-test/{uuid.uuid4().hex}"
        self.tasks[arn] = RunningTask(arn, job_id, datetime.now(UTC))
        self.started.append((job_id, client_token))
        return arn

    async def stop(self, task_arn: str, reason: str) -> None:
        self.stopped.append(task_arn)
        self.tasks.pop(task_arn, None)


class Env:
    def __init__(self, aws: Any) -> None:
        self.aws = aws
        self.rt = Runtime.create(aws.settings)
        self.queue = SqsJobQueue(aws.settings)
        self.runner = RecordingRunner()
        sqs = boto3.client("sqs", region_name="us-east-1")
        self.sqs = sqs
        self.d = Dispatcher(self.rt, self.queue, self.runner, dlq=(sqs, aws.dlq_url))

    async def job(self, *, age: timedelta = timedelta(0), **kw: Any) -> JobRec:
        now = datetime.now(UTC)
        sess = await self.rt.control.create_session(uuid.uuid4().hex, now, now + timedelta(hours=1))
        p = ProjectRec(str(uuid.uuid4()), sess.id, "github", "beginner", "t", now, now + timedelta(hours=1))
        j = JobRec(str(uuid.uuid4()), p.id, now - age, **kw)
        await self.rt.control.create_project(p, j)
        return j

    def sent(self, url: str | None = None) -> list[dict[str, Any]]:
        got = self.sqs.receive_message(
            QueueUrl=url or self.aws.queue_url, MaxNumberOfMessages=10, VisibilityTimeout=0
        )
        return [json.loads(m["Body"]) for m in got.get("Messages", [])]

    def drain(self) -> None:
        self.sqs.purge_queue(QueueUrl=self.aws.queue_url)


@pytest.fixture
def env(aws: Any) -> Env:
    return Env(aws)


def stopped_detail(
    job_id: str, exit_code: int | None, *, stop_code: str = "EssentialContainerExited"
) -> dict[str, Any]:
    container: dict[str, Any] = {"name": "worker"}
    if exit_code is not None:
        container["exitCode"] = exit_code
    return {
        "lastStatus": "STOPPED",
        "stopCode": stop_code,
        "stoppedReason": "Essential container in task exited",
        "overrides": {"containerOverrides": [{"name": "worker", "command": ["analysis", job_id]}]},
        "containers": [container],
    }


# --- dispatcher ---------------------------------------------------------------------------------------------------


async def test_queued_job_starts_one_task(env: Env) -> None:
    job = await env.job()
    assert await env.d.handle_message("m1", {"type": "analysis", "ref": job.id}) == "started"
    assert [j for j, _ in env.runner.started] == [job.id]


async def test_duplicate_delivery_starts_one_task(env: Env) -> None:
    job = await env.job()
    body = {"type": "analysis", "ref": job.id}
    assert await env.d.handle_message("m1", body) == "started"
    assert await env.d.handle_message("m1", body) in ("duplicate",)  # same message redelivered
    assert await env.d.handle_message("m2", body) == "duplicate"  # a second message for the same job
    assert len(env.runner.started) == 1


async def test_finished_or_unknown_jobs_start_nothing(env: Env) -> None:
    done = await env.job(status="succeeded")
    assert await env.d.handle_message("m", {"type": "analysis", "ref": done.id}) == "skipped"
    assert await env.d.handle_message("m", {"type": "analysis", "ref": str(uuid.uuid4())}) == "skipped"
    assert await env.d.handle_message("m", {"type": "wat", "ref": "x"}) == "ignored"
    assert env.runner.started == []


async def test_capacity_defers_without_error_and_a_stopped_task_resends(env: Env) -> None:
    first, second = await env.job(), await env.job()
    assert await env.d.handle_message("m1", {"type": "analysis", "ref": first.id}) == "started"
    assert await env.d.handle_message("m2", {"type": "analysis", "ref": second.id}) == "deferred"  # cap is 1
    assert env.runner.started == [(first.id, "m1")]

    # the first task ends; the freed slot is handed to the waiting job without anyone polling
    env.runner.tasks.clear()
    env.drain()
    outcome = await env.d.handle_task_stopped(stopped_detail(first.id, 0))
    assert outcome == "ok"
    bodies = env.sent()
    assert {"type": "analysis", "ref": second.id, "attempt": 1} in bodies


async def test_cleanup_message_runs_inline(env: Env) -> None:
    job = await env.job()
    await env.rt.objects.put_bytes(f"projects/{job.project_id}/ir/1.json.gz", b"x")
    assert await env.d.handle_message("m", {"type": "cleanup", "ref": job.project_id}) == "cleaned"
    assert await env.rt.control.get_project(job.project_id) is None
    assert await env.rt.control.get_job(job.id) is None
    assert await env.rt.objects.delete_prefix(f"projects/{job.project_id}/") == 0  # blobs were removed too


# --- task stopped -------------------------------------------------------------------------------------------------


async def test_exit_75_resends_with_a_backoff(env: Env) -> None:
    job = await env.job(status="queued", attempt=1)
    assert await env.d.handle_task_stopped(stopped_detail(job.id, 75)) == "retry"
    assert env.sent() == []  # deferred (DelaySeconds), not visible yet, but sent


async def test_crash_is_retried_until_attempts_are_exhausted_then_the_job_fails(env: Env) -> None:
    job = await env.job(status="running", attempt=1, heartbeat_at=datetime.now(UTC))
    assert await env.d.handle_task_stopped(stopped_detail(job.id, 137)) == "crashed"
    exhausted = await env.job(status="running", attempt=env.rt.settings.max_job_attempts)
    assert await env.d.handle_task_stopped(
        stopped_detail(exhausted.id, None, stop_code="TaskFailedToStart")
    ) == ("abandoned")
    final = await env.rt.control.get_job(exhausted.id)
    assert final is not None and final.status == "failed" and final.error_code == "ANALYSIS_STAGE_FAILED"
    # the event stream tells the client, so polling terminates
    events = await env.rt.progress.read_after(exhausted.project_id, 0)
    assert events and events[-1][1]["type"] == "failed"


async def test_stopped_task_of_a_finished_job_is_ignored(env: Env) -> None:
    job = await env.job(status="succeeded")
    assert await env.d.handle_task_stopped(stopped_detail(job.id, 0)) == "ok"
    assert await env.d.handle_task_stopped({"overrides": {}, "containers": []}) == "ignored"


# --- sweeper ------------------------------------------------------------------------------------------------------


async def test_sweeper_resends_a_job_whose_enqueue_was_lost(env: Env) -> None:
    lost = await env.job(age=timedelta(minutes=30))  # committed, never enqueued (the outbox gap)
    young = await env.job()
    report = await env.d.sweep()
    refs = [b["ref"] for b in env.sent()]
    assert lost.id in refs and young.id not in refs
    assert report.resent == 1
    await env.d.sweep()  # idempotent: a second run only adds a duplicate message, never a second job


async def test_sweeper_recovers_stale_running_jobs_but_not_live_ones(env: Env) -> None:
    now = datetime.now(UTC)
    dead = await env.job(status="running", attempt=1, heartbeat_at=now - timedelta(minutes=20))
    alive = await env.job(status="running", attempt=1, heartbeat_at=now - timedelta(minutes=20))
    env.runner.tasks["arn:t1"] = RunningTask("arn:t1", alive.id, now)  # a live task still holds this one
    await env.d.sweep()
    refs = [b["ref"] for b in env.sent()]
    assert dead.id in refs and alive.id not in refs


async def test_sweeper_stops_tasks_past_the_job_deadline(env: Env) -> None:
    job = await env.job(status="running", attempt=1, heartbeat_at=datetime.now(UTC))
    limit = timedelta(seconds=env.rt.settings.job_timeout_seconds) + timedelta(minutes=6)
    env.runner.tasks["arn:old"] = RunningTask("arn:old", job.id, datetime.now(UTC) - limit)
    report = await env.d.sweep()
    assert env.runner.stopped == ["arn:old"] and report.stopped == 1


async def test_sweeper_deletes_expired_data_from_every_store(env: Env) -> None:
    now = datetime.now(UTC)
    old_sess = await env.rt.control.create_session(
        uuid.uuid4().hex, now - timedelta(days=5), now - timedelta(days=4)
    )
    p = ProjectRec(
        str(uuid.uuid4()),
        old_sess.id,
        "github",
        "beginner",
        "t",
        now - timedelta(days=5),
        now - timedelta(days=1),
    )
    j = JobRec(str(uuid.uuid4()), p.id, now - timedelta(days=5), status="succeeded")
    await env.rt.control.create_project(p, j)
    await env.rt.objects.put_bytes(f"projects/{p.id}/ir/1.json.gz", b"x")
    await env.rt.progress.publish(p.id, {"type": "ready"})
    report = await env.d.sweep()
    assert report.cleaned_projects >= 1
    assert await env.rt.control.get_project(p.id) is None
    assert await env.rt.progress.read_after(p.id, 0) == []
    assert await env.rt.objects.delete_prefix(f"projects/{p.id}/") == 0
    await env.d.sweep()  # the session goes once it has no projects left
    assert await env.rt.control.session_by_token(old_sess.token_hash) is None


async def test_sweeper_fails_jobs_whose_message_was_dead_lettered(env: Env) -> None:
    job = await env.job()
    boto3.client("sqs", region_name="us-east-1").send_message(
        QueueUrl=env.aws.dlq_url, MessageBody=json.dumps({"type": "analysis", "ref": job.id, "attempt": 1})
    )
    report = await env.d.sweep()
    assert report.dead_lettered == 1
    got = await env.rt.control.get_job(job.id)
    assert got is not None and got.status == "failed"
    assert env.sent(env.aws.dlq_url) == []  # drained


async def test_sweeper_heals_the_cap_counters(env: Env) -> None:
    await env.job()
    report = await env.d.sweep()
    assert (report.queued, report.running) == (1, 0)


def test_heartbeat_is_a_valid_embedded_metric_line() -> None:
    line = json.loads(heartbeat_line("Unfold"))
    assert line["sweeper_heartbeat"] == 1
    assert line["_aws"]["CloudWatchMetrics"][0]["Metrics"][0]["Name"] == "sweeper_heartbeat"


# --- the ECS adapter: request shape (moto 5.2 cannot run awsvpc tasks; real RunTask requires AWS) ------------------------


class FakeEcs:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.tasks: dict[str, dict[str, Any]] = {}
        self.fail = False

    def run_task(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(("run_task", kw))
        if self.fail:
            return {"tasks": [], "failures": [{"reason": "RESOURCE:CPU"}]}
        arn = f"arn:aws:ecs:us-east-1:1:task/c/{len(self.tasks)}"
        self.tasks[arn] = {"taskArn": arn, "startedBy": kw["startedBy"], "startedAt": datetime.now(UTC)}
        return {"tasks": [{"taskArn": arn}], "failures": []}

    def list_tasks(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(("list_tasks", kw))
        return {"taskArns": list(self.tasks)}

    def describe_tasks(self, **kw: Any) -> dict[str, Any]:
        return {"tasks": [self.tasks[a] for a in kw["tasks"]]}

    def stop_task(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(("stop_task", kw))
        self.tasks.pop(kw["task"], None)
        return {}


async def test_fargate_runner_sends_the_documented_run_task_request(aws: Any) -> None:
    ecs = FakeEcs()
    runner = FargateRunner(aws.settings, client=ecs)
    job_id = str(uuid.uuid4())
    arn = await runner.start(job_id, "msg-1")
    (_, req) = ecs.calls[0]
    assert req["launchType"] == "FARGATE" and req["count"] == 1
    assert req["startedBy"] == job_id and req["clientToken"] == "msg-1"
    assert req["overrides"]["containerOverrides"] == [{"name": "worker", "command": ["analysis", job_id]}]
    vpc = req["networkConfiguration"]["awsvpcConfiguration"]
    assert vpc == {
        "subnets": ["subnet-1", "subnet-2"],
        "securityGroups": ["sg-1"],
        "assignPublicIp": "ENABLED",
    }
    assert [(t.arn, t.job_id) for t in await runner.running()] == [(arn, job_id)]
    await runner.stop(arn, "over time")
    assert await runner.running() == []


async def test_fargate_runner_surfaces_capacity_failures_as_retryable(aws: Any) -> None:
    ecs = FakeEcs()
    ecs.fail = True
    with pytest.raises(UnfoldError) as exc:
        await FargateRunner(aws.settings, client=ecs).start(str(uuid.uuid4()), "t")
    assert exc.value.retryable and "RESOURCE:CPU" in exc.value.message
