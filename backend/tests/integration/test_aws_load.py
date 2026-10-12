"""Overload and burst behaviour of the AWS profile, in process (moto): bounded acceptance, typed rejection, no
duplicate starts, and request latency percentiles of the API layer over the emulated stores.

The latency numbers are API + adapter overhead against an in-process emulator. They say nothing about network
round trips, Lambda cold starts or DynamoDB/S3 service latency (those are "requires AWS", docs/infra/performance).
Set UNFOLD_BENCH_OUT=<file> to write the percentiles as JSON.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3
import httpx
import pytest
from fastapi import FastAPI

from unfold.api.main import create_app
from unfold.domain.records import JobRec, ProjectRec
from unfold.worker.dispatch import Dispatcher, RunningTask
from unfold.worker.sqs_queue import SqsJobQueue

pytestmark = pytest.mark.integration


@pytest.fixture
async def app(aws: Any) -> AsyncIterator[FastAPI]:
    settings = aws.settings.model_copy(
        update={
            "max_sessions_per_ip_per_hour": 10_000,
            "max_sessions_per_hour": 10_000,
            "max_projects_per_hour": 10_000,
            "max_projects_per_ip_per_hour": 10_000,
            "max_projects_per_hour_global": 10_000,
        }
    )
    application = create_app(settings, queue=SqsJobQueue(settings))
    async with application.router.lifespan_context(application):
        yield application


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _queue_depth(aws: Any) -> int:
    attrs = boto3.client("sqs", region_name="us-east-1").get_queue_attributes(
        QueueUrl=aws.queue_url, AttributeNames=["ApproximateNumberOfMessages"]
    )
    return int(attrs["Attributes"]["ApproximateNumberOfMessages"])


async def test_burst_from_one_session_accepts_exactly_the_cap(aws: Any, app: FastAPI) -> None:
    async with _client(app) as c:
        assert (await c.post("/api/v1/sessions")).status_code == 201
        urls = [f"https://github.com/octo/repo{i}" for i in range(40)]
        resps = await asyncio.gather(*(c.post("/api/v1/projects/github", json={"url": u}) for u in urls))
    codes = [r.status_code for r in resps]
    assert codes.count(202) == aws.settings.max_active_jobs_per_session == 2
    assert codes.count(429) == 38 and not [x for x in codes if x >= 500]
    assert all(r.json()["error"]["code"] == "RATE_LIMITED" for r in resps if r.status_code == 429)
    assert _queue_depth(aws) == 2  # nothing accepted was dropped, nothing rejected was queued


async def test_burst_across_sessions_respects_the_global_queue_cap(aws: Any, app: FastAPI) -> None:
    async def one(i: int) -> int:
        async with _client(app) as c:
            assert (await c.post("/api/v1/sessions")).status_code == 201
            r = await c.post("/api/v1/projects/github", json={"url": f"https://github.com/octo/r{i}"})
            return r.status_code

    codes = await asyncio.gather(*(one(i) for i in range(30)))
    assert codes.count(202) == aws.settings.max_queued_jobs_global == 5
    assert codes.count(429) == 25 and not [x for x in codes if x >= 500]


async def test_duplicate_delivery_storm_starts_one_task(aws: Any, app: FastAPI) -> None:
    rt = app.state.runtime
    now = datetime.now(UTC)
    s = await rt.control.create_session(uuid.uuid4().hex, now, now + timedelta(hours=1))
    p = ProjectRec(str(uuid.uuid4()), s.id, "github", "beginner", "t", now, now + timedelta(hours=1))
    j = JobRec(str(uuid.uuid4()), p.id, now)
    await rt.control.create_project(p, j)

    class Runner:
        def __init__(self) -> None:
            self.tasks: dict[str, RunningTask] = {}
            self.starts = 0

        async def running(self) -> list[RunningTask]:
            await asyncio.sleep(0)
            return list(self.tasks.values())

        async def start(self, job_id: str, client_token: str) -> str:
            self.starts += 1
            self.tasks[job_id] = RunningTask(f"arn:{job_id}", job_id, datetime.now(UTC))
            return f"arn:{job_id}"

        async def stop(self, task_arn: str, reason: str) -> None:
            return None

    runner = Runner()
    d = Dispatcher(rt, app.state.queue, runner)
    # sequential redelivery (SQS delivers a message again after the visibility timeout): exactly one task
    first = await d.handle_message("m0", {"type": "analysis", "ref": j.id})
    rest = [await d.handle_message(f"m{i}", {"type": "analysis", "ref": j.id}) for i in range(1, 50)]
    assert first == "started" and set(rest) == {"duplicate"} and runner.starts == 1


async def test_latency_percentiles_of_the_api_layer(aws: Any, app: FastAPI) -> None:
    samples: dict[str, list[float]] = {}

    async def timed(name: str, call: Any) -> Any:
        t0 = time.perf_counter()
        r = await call
        samples.setdefault(name, []).append((time.perf_counter() - t0) * 1000)
        assert r.status_code < 400, r.text
        return r

    async with _client(app) as c:
        created = await timed("POST /sessions", c.post("/api/v1/sessions"))
        assert created.status_code == 201
        pid = (await c.post("/api/v1/projects/github", json={"url": "https://github.com/octo/bench"})).json()[
            "project_id"
        ]
        for _ in range(100):
            await timed("GET /sessions/current", c.get("/api/v1/sessions/current"))
            await timed("GET /projects/{id}", c.get(f"/api/v1/projects/{pid}"))
            await timed("GET /projects/{id}/progress", c.get(f"/api/v1/projects/{pid}/progress"))
            await timed("POST /sessions/current/touch", c.post("/api/v1/sessions/current/touch"))
    out = {}
    for name, v in samples.items():
        v.sort()
        q = statistics.quantiles(v, n=100, method="inclusive") if len(v) > 2 else [v[0]] * 99
        out[name] = {
            "n": len(v),
            "p50_ms": round(q[49], 1),
            "p95_ms": round(q[94], 1),
            "p99_ms": round(q[98], 1),
        }
        assert q[98] < 2000  # a regression guard, not a performance claim
    if path := os.environ.get("UNFOLD_BENCH_OUT"):
        Path(path).write_text(json.dumps(out, indent=2), encoding="utf-8")  # noqa: ASYNC240
    print(json.dumps(out, indent=2))
