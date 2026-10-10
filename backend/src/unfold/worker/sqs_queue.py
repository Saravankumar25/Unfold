"""`JobQueue` on SQS (ADR 0011).

Sends `{"type": "analysis"|"cleanup", "ref": <id>, "attempt": n}` to the jobs queue; the dispatcher Lambda turns
analysis messages into Fargate tasks. Delivery is at-least-once and a duplicate is harmless: the job claim is a
conditional update and RunTask carries the message id as its client token. A failed send raises a typed error;
the caller leaves the job `queued` and the sweeper re-sends it (the outbox reconciliation, ADR 0011).
"""

from __future__ import annotations

import asyncio
from typing import Any

import boto3
import orjson
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from unfold.core.config import Settings
from unfold.core.errors import ErrorCode, UnfoldError
from unfold.ports import JobQueue

MAX_DELAY_SECONDS = 900  # SQS DelaySeconds ceiling


def queue_error(exc: Exception) -> UnfoldError:
    return UnfoldError(
        f"The job queue is unavailable ({type(exc).__name__}).",
        code=ErrorCode.QUEUE_UNAVAILABLE,
        retryable=True,
        http_status=503,
    )


def sqs_client(settings: Settings) -> Any:
    kwargs: dict[str, Any] = {}
    if settings.aws_region_name:
        kwargs["region_name"] = settings.aws_region_name
    if settings.aws_endpoint_url:
        kwargs["endpoint_url"] = settings.aws_endpoint_url
    return boto3.client(
        "sqs",
        config=Config(retries={"max_attempts": 4, "mode": "standard"}, connect_timeout=3, read_timeout=10),
        **kwargs,
    )


class SqsJobQueue(JobQueue):
    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self._url = settings.jobs_queue_url
        self._client = client or sqs_client(settings)

    async def enqueue_analysis(self, job_id: str, attempt: int, *, defer_s: float | None = None) -> None:
        await self._send({"type": "analysis", "ref": job_id, "attempt": attempt}, defer_s)

    async def enqueue_project_cleanup(self, project_id: str) -> None:
        await self._send({"type": "cleanup", "ref": project_id}, None)

    async def _send(self, body: dict[str, Any], defer_s: float | None) -> None:
        delay = max(0, min(MAX_DELAY_SECONDS, int(defer_s or 0)))
        try:
            await asyncio.to_thread(
                lambda: self._client.send_message(
                    QueueUrl=self._url, MessageBody=orjson.dumps(body).decode(), DelaySeconds=delay
                )
            )
        except (ClientError, BotoCoreError) as exc:
            raise queue_error(exc) from exc

    async def aclose(self) -> None:
        return None
