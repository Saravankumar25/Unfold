"""AWS control handlers (ADR 0011): the dispatcher, the task-stopped reaction and the sweeper.

All three run from the worker image as Lambda functions. They replace what `SubprocessJobQueue` does locally:
start one bounded task per job, apply the exit-code policy, and heal what is left. Every decision is idempotent,
because SQS and EventBridge deliver at least once:

* the dispatcher never starts a second task for a job that has one, and never exceeds the concurrency cap
  (a message that finds no free slot is dropped; the next task-stopped event or sweeper run re-sends it);
* the claim at the start of the job is the real mutual exclusion, so a rare duplicate task exits harmlessly;
* the sweeper is the outbox reconciliation: whatever is `queued` or stale gets (re-)sent, DLQ'd messages fail
  their job with a typed error, expired data is deleted explicitly (never left to TTL), counters are healed.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from unfold.cleanup.service import cleanup_project
from unfold.core.config import Settings, get_settings
from unfold.core.errors import ErrorCode, UnfoldError
from unfold.core.logging import configure_logging, get_logger
from unfold.orchestration.constants import ALIVE_WITHIN, RETRY_EXIT_CODE
from unfold.ports import JobQueue
from unfold.runtime import Runtime
from unfold.worker.recovery import STALE_AFTER, fail_abandoned_job
from unfold.worker.sqs_queue import SqsJobQueue, queue_error, sqs_client

log = get_logger(__name__)

OUTBOX_AGE = timedelta(minutes=10)  # `queued` for this long with no task: the enqueue was lost
OVERAGE_SLACK = timedelta(minutes=5)  # a task this far past the job timeout is stopped
EXPIRY_BATCH = 20  # projects cleaned inline per sweep (Lambda time bound; the next run continues)
HEARTBEAT_NAMESPACE = "Unfold"


@dataclass(frozen=True)
class RunningTask:
    arn: str
    job_id: str
    started_at: datetime


class TaskRunner(Protocol):
    async def running(self) -> list[RunningTask]: ...
    async def start(self, job_id: str, client_token: str) -> str: ...
    async def stop(self, task_arn: str, reason: str) -> None: ...


class FargateRunner:
    """`ecs:RunTask` per job; `startedBy` carries the job id so a task can be matched back to its job."""

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self._s = settings
        if client is None:
            kwargs: dict[str, Any] = {}
            if settings.aws_region_name:
                kwargs["region_name"] = settings.aws_region_name
            if settings.aws_endpoint_url:
                kwargs["endpoint_url"] = settings.aws_endpoint_url
            client = boto3.client(
                "ecs",
                config=Config(
                    retries={"max_attempts": 4, "mode": "standard"}, connect_timeout=3, read_timeout=15
                ),
                **kwargs,
            )
        self._ecs = client

    async def _call(self, name: str, **kwargs: Any) -> Any:
        try:
            return await asyncio.to_thread(lambda: getattr(self._ecs, name)(**kwargs))
        except (ClientError, BotoCoreError) as exc:
            raise UnfoldError(
                f"The task runner is unavailable ({type(exc).__name__}).",
                code=ErrorCode.QUEUE_UNAVAILABLE,
                retryable=True,
                http_status=503,
            ) from exc

    async def running(self) -> list[RunningTask]:
        cluster = self._s.ecs_cluster_arn
        arns: list[str] = []
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"cluster": cluster, "desiredStatus": "RUNNING"}
            if token:
                kwargs["nextToken"] = token
            page = await self._call("list_tasks", **kwargs)
            arns += page.get("taskArns", [])
            token = page.get("nextToken")
            if not token:
                break
        out: list[RunningTask] = []
        for i in range(0, len(arns), 100):
            desc = await self._call("describe_tasks", cluster=cluster, tasks=arns[i : i + 100])
            for t in desc.get("tasks", []):
                started = t.get("startedAt") or t.get("createdAt") or datetime.now(UTC)
                out.append(RunningTask(t["taskArn"], str(t.get("startedBy", "")), started))
        return out

    async def start(self, job_id: str, client_token: str) -> str:
        s = self._s
        resp = await self._call(
            "run_task",
            cluster=s.ecs_cluster_arn,
            taskDefinition=s.task_definition_analysis,
            launchType="FARGATE",
            count=1,
            startedBy=job_id,
            clientToken=client_token[:64],
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": [x for x in s.subnet_ids.split(",") if x],
                    "securityGroups": [x for x in s.security_group_ids.split(",") if x],
                    "assignPublicIp": "ENABLED",
                }
            },
            overrides={"containerOverrides": [{"name": "worker", "command": ["analysis", job_id]}]},
        )
        if resp.get("failures") or not resp.get("tasks"):
            reason = (resp.get("failures") or [{}])[0].get("reason", "no task started")
            raise UnfoldError(
                f"The task could not be started ({reason}).",
                code=ErrorCode.QUEUE_UNAVAILABLE,
                retryable=True,
                http_status=503,
            )
        return str(resp["tasks"][0]["taskArn"])

    async def stop(self, task_arn: str, reason: str) -> None:
        await self._call("stop_task", cluster=self._s.ecs_cluster_arn, task=task_arn, reason=reason[:250])


@dataclass
class SweepReport:
    resent: int = 0
    stopped: int = 0
    cleaned_projects: int = 0
    deleted_sessions: int = 0
    dead_lettered: int = 0
    queued: int = 0
    running: int = 0


class Dispatcher:
    def __init__(self, rt: Runtime, queue: JobQueue, runner: TaskRunner, *, dlq: Any = None) -> None:
        self.rt = rt
        self.queue = queue
        self.runner = runner
        self.s = rt.settings
        self._dlq = dlq  # (client, url) of the dead-letter queue, or None

    # --- SQS message -> task ----------------------------------------------------------------------------

    async def handle_message(self, message_id: str, body: dict[str, Any]) -> str:
        """Returns what was done (for logs and tests); raises a retryable error to leave the message queued."""
        kind, ref = str(body.get("type", "")), str(body.get("ref", ""))
        if kind == "cleanup":
            counts = await cleanup_project(self.rt, ref)
            log.info("cleanup.done", project_id=ref, **counts)
            return "cleaned"
        if kind != "analysis":
            log.warning("dispatch.unknown_message", type=kind)
            return "ignored"
        return await self._dispatch_analysis(message_id, ref)

    async def _dispatch_analysis(self, message_id: str, job_id: str) -> str:
        job = await self.rt.control.get_job(job_id)
        if job is None or job.terminal:
            log.info("dispatch.skipped", job_id=job_id, reason="missing_or_finished")
            return "skipped"
        running = await self.runner.running()
        if any(t.job_id == job_id for t in running):
            return "duplicate"
        if len(running) >= max(1, self.s.max_concurrent_jobs):
            # No slot. Drop the message: a stopped task or the sweeper re-sends `queued` jobs, so waiting here
            # would only burn receives toward the DLQ.
            log.info("dispatch.deferred", job_id=job_id, running=len(running))
            return "deferred"
        arn = await self.runner.start(job_id, message_id)
        log.info("dispatch.started", job_id=job_id, task=arn.rsplit("/", 1)[-1])
        return "started"

    # --- ECS task state change --------------------------------------------------------------------------

    async def handle_task_stopped(self, detail: dict[str, Any]) -> str:
        command = _command(detail)
        if len(command) < 2 or command[0] != "analysis":
            return "ignored"
        job_id = command[1]
        exit_code = _exit_code(detail)
        job = await self.rt.control.get_job(job_id)
        outcome = "ok"
        if job is not None and not job.terminal and exit_code != 0:
            if exit_code == RETRY_EXIT_CODE:
                delay = min(30.0, 5.0 * max(1, job.attempt))
                await self.queue.enqueue_analysis(job_id, job.attempt + 1, defer_s=delay)
                outcome = "retry"
            elif job.attempt >= self.s.max_job_attempts:
                await fail_abandoned_job(self.rt, job_id)
                outcome = "abandoned"
            else:
                # crashed, OOM-killed or never started: its heartbeat is still fresh, wait the guard out
                delay = ALIVE_WITHIN.total_seconds() + float(self.s.task_stopped_resend_delay_seconds)
                await self.queue.enqueue_analysis(job_id, job.attempt + 1, defer_s=delay)
                outcome = "crashed"
            log.warning(
                "task.stopped",
                job_id=job_id,
                exit_code=exit_code,
                stop_code=detail.get("stopCode"),
                reason=str(detail.get("stoppedReason", ""))[:200],
                outcome=outcome,
            )
        await self._requeue_waiting(exclude={job_id})
        return outcome

    async def _requeue_waiting(self, exclude: set[str] = frozenset()) -> int:  # type: ignore[assignment]
        """A slot just opened: hand it to the oldest jobs that are waiting (dropped earlier for capacity).
        `exclude` holds the job whose retry was just scheduled with its own backoff."""
        running = await self.runner.running()
        free = max(1, self.s.max_concurrent_jobs) - len(running)
        if free <= 0:
            return 0
        now = datetime.now(UTC)
        jobs = await self.rt.control.recoverable_jobs(
            queued_before=now, heartbeat_before=now - STALE_AFTER, limit=free + len(exclude)
        )
        busy = {t.job_id for t in running} | exclude
        sent = 0
        for job in jobs:
            if job.id not in busy and sent < free:
                await self.queue.enqueue_analysis(job.id, job.attempt + 1)
                sent += 1
        return sent

    # --- scheduled sweeper ------------------------------------------------------------------------------

    async def sweep(self) -> SweepReport:
        report = SweepReport()
        now = datetime.now(UTC)
        running = await self.runner.running()
        busy = {t.job_id for t in running}
        # 1. outbox reconciliation and stale-heartbeat recovery
        for job in await self.rt.control.recoverable_jobs(
            queued_before=now - OUTBOX_AGE, heartbeat_before=now - STALE_AFTER, limit=100
        ):
            if job.id in busy:
                continue
            await self.queue.enqueue_analysis(job.id, job.attempt + 1)
            report.resent += 1
        # 2. tasks past their deadline (a hung worker outlives its own watchdog only if the process is wedged)
        limit = timedelta(seconds=self.s.job_timeout_seconds) + OVERAGE_SLACK
        for task in running:
            if now - task.started_at > limit:
                await self.runner.stop(task.arn, "exceeded job timeout")
                report.stopped += 1
        # 3. jobs whose message died in the DLQ
        report.dead_lettered = await self._drain_dead_letters(busy)
        # 4. expiry, by explicit delete (TTL is only a backstop and may lag by days)
        for project in await self.rt.control.expired_projects(now, EXPIRY_BATCH):
            await cleanup_project(self.rt, project.id)
            report.cleaned_projects += 1
        for session in await self.rt.control.expired_sessions(now, EXPIRY_BATCH):
            if not await self.rt.control.projects_of_session(session.id):
                await self.rt.control.delete_session(session.token_hash)
                report.deleted_sessions += 1
        # 5. counters healed from the job records
        report.queued, report.running = await self.rt.control.active_job_counts()
        log.info("sweep.done", **report.__dict__)
        return report

    async def _drain_dead_letters(self, busy: set[str]) -> int:
        if self._dlq is None:
            return 0
        client, url = self._dlq
        handled = 0
        for _ in range(5):  # at most 50 messages per run
            try:
                resp = await asyncio.to_thread(
                    lambda: client.receive_message(QueueUrl=url, MaxNumberOfMessages=10, WaitTimeSeconds=0)
                )
            except (ClientError, BotoCoreError) as exc:
                raise queue_error(exc) from exc
            messages = resp.get("Messages", [])
            if not messages:
                break
            for m in messages:
                try:
                    body = json.loads(m["Body"])
                    ref = str(body.get("ref", ""))
                    # a task is already running for it (started by a resend): failing the job would kill it
                    if body.get("type") == "analysis" and ref not in busy:
                        await fail_abandoned_job(
                            self.rt,
                            ref,
                            "The analysis could not be started. Please submit the project again.",
                        )
                    elif body.get("type") == "cleanup":
                        await cleanup_project(self.rt, ref)
                except (ValueError, AttributeError):  # unreadable body or id: drop it, never wedge the sweep
                    log.warning("dlq.unreadable_message", message_id=m.get("MessageId"))
                await asyncio.to_thread(
                    lambda h=m["ReceiptHandle"]: client.delete_message(QueueUrl=url, ReceiptHandle=h)  # type: ignore[misc]
                )
                handled += 1
        return handled


def _command(detail: dict[str, Any]) -> list[str]:
    overrides = (detail.get("overrides") or {}).get("containerOverrides") or []
    return [str(c) for c in (overrides[0].get("command") or [])] if overrides else []


def _exit_code(detail: dict[str, Any]) -> int:
    """The worker container's exit code; -1 when it never ran (image pull failure, OOM before start)."""
    for c in detail.get("containers") or []:
        if c.get("name") == "worker" and c.get("exitCode") is not None:
            return int(c["exitCode"])
    return -1


def heartbeat_line(namespace: str = HEARTBEAT_NAMESPACE) -> str:
    """CloudWatch Embedded Metric Format line: the alarm on its absence is the only dead-scheduler detector."""
    return json.dumps(
        {
            "_aws": {
                "Timestamp": int(time.time() * 1000),
                "CloudWatchMetrics": [
                    {
                        "Namespace": namespace,
                        "Dimensions": [[]],
                        "Metrics": [{"Name": "sweeper_heartbeat", "Unit": "Count"}],
                    }
                ],
            },
            "sweeper_heartbeat": 1,
        }
    )


# --- Lambda entry points ----------------------------------------------------------------------------------

_cache: dict[str, Any] = {}


def _dispatcher() -> Dispatcher:
    if "d" not in _cache:
        settings = get_settings()
        configure_logging(settings)
        rt = Runtime.create(settings)
        dlq = (sqs_client(settings), settings.jobs_queue_url + "-dlq") if settings.jobs_queue_url else None
        _cache["d"] = Dispatcher(rt, SqsJobQueue(settings), FargateRunner(settings), dlq=dlq)
    d: Dispatcher = _cache["d"]
    return d


def dispatcher_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """SQS event source mapping, batch size 1. An exception returns the message to the queue (bounded redrive)."""
    d = _dispatcher()

    async def run() -> None:
        for record in event.get("Records", []):
            await d.handle_message(str(record["messageId"]), json.loads(record["body"]))

    asyncio.run(run())
    return {"batchItemFailures": []}


def task_stopped_handler(event: dict[str, Any], context: Any = None) -> dict[str, str]:
    outcome = asyncio.run(_dispatcher().handle_task_stopped(event.get("detail", {})))
    return {"outcome": outcome}


def sweeper_handler(event: dict[str, Any] | None = None, context: Any = None) -> dict[str, int]:
    report = asyncio.run(_dispatcher().sweep())
    print(heartbeat_line())  # noqa: T201 - EMF is read from stdout by CloudWatch Logs
    return report.__dict__.copy()


__all__ = [
    "Dispatcher",
    "FargateRunner",
    "RunningTask",
    "SweepReport",
    "TaskRunner",
    "dispatcher_handler",
    "heartbeat_line",
    "sweeper_handler",
    "task_stopped_handler",
]
