"""The whole product on the AWS profile, in process: ZIP upload ticket -> job -> SQS -> dispatcher -> worker run
-> guide, diagrams and evidence over the API -> duplicate delivery -> crash and resume -> cleanup.

Real: the API app, DynamoDB/S3/SQS adapters (over moto), pipeline, BM25, embedded dense artifact, in-memory graph,
retrieval and verification. Doubles: ECS (the runner executes the job in this process, exactly what the Fargate
task does with the same `run_analysis`) and the LLM (scripted). Needs the embedding model cache, not docker.
What it cannot show (real RunTask, Lambda cold start, IAM, browser POST to S3) is listed as "requires AWS".
"""

from __future__ import annotations

import io
import json
import math
import os
import re
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import boto3
import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel, SecretStr

from unfold.api.main import create_app
from unfold.core.errors import ProviderCapacityError
from unfold.guide.models import (
    DraftClaim,
    GuidePlan,
    GuideSectionPlan,
    ParagraphBlock,
    SectionContent,
    SectionDraft,
)
from unfold.llm.schemas import LLMRequest
from unfold.orchestration.constants import RETRY_EXIT_CODE
from unfold.orchestration.pipeline import run_analysis
from unfold.runtime import Runtime
from unfold.worker.dispatch import Dispatcher, RunningTask
from unfold.worker.sqs_queue import SqsJobQueue

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).resolve().parents[3] / "evals" / "fixtures" / "repos" / "fastapi_rag"
_LABEL = re.compile(r"\[(E\d+)\] (\S+?):(\d+)-(\d+)")


class ScriptedLLM:
    def __init__(self, *, fail_on_section: int | None = None) -> None:
        self.calls: list[str] = []
        self.written = 0
        self.fail_on_section = fail_on_section

    async def generate(self, req: LLMRequest) -> Any:
        raise AssertionError("structured only")

    def stream(self, req: LLMRequest) -> Any:
        raise AssertionError("no streaming in the pipeline")

    async def generate_structured(self, req: LLMRequest, schema: type[BaseModel]) -> Any:
        self.calls.append(req.purpose)
        if schema is GuidePlan:
            keys = [
                ("overview", "Overview"),
                ("architecture", "Architecture"),
                ("data-flow", "Data flow"),
                ("running", "Running it"),
            ]
            return GuidePlan(
                estimated_read_minutes=12,
                sections=[
                    GuideSectionPlan(
                        key=k,
                        title=t,
                        kind="backbone" if i < 2 else "core",
                        purpose=f"Explain {t.lower()} of this project",
                        target_minutes=3,
                        evidence_objectives=["app", "api", "routes", "retrieval"],
                        visual_needs=["architecture"] if i == 1 else [],
                    )
                    for i, (k, t) in enumerate(keys)
                ],
            )
        if self.fail_on_section is not None and self.written >= self.fail_on_section:
            raise ProviderCapacityError("scripted capacity failure", retryable=True)
        self.written += 1
        label, path, *_ = _LABEL.findall(req.messages[0].content)[0]
        return SectionDraft(
            content=SectionContent(
                summary=f"How {Path(path).stem} fits in.",
                blocks=[ParagraphBlock(text=f"`{path}` is part of the project [{label}].")],
            ),
            claims=[
                DraftClaim(text=f"`{path}` is part of the project", kind="project_fact", evidence=[label])
            ],
        )


class CountingDynamo:
    """Wraps the DynamoDB client: counts requests and estimates capacity units from the item sizes sent/received.
    Writes: 1 WRU per KB (x2 in a transaction). Reads: 0.5 RRU per 4 KB eventual, 1 consistent (x2 transactional
    is not used by reads here). An estimate from payload size, not a billing figure."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: dict[str, int] = {}
        self.wru = 0.0
        self.rru = 0.0

    @staticmethod
    def _kb(item: Any) -> float:
        return max(1.0, len(json.dumps(item, default=str)) / 1024)

    def __getattr__(self, name: str) -> Any:
        fn = getattr(self._inner, name)

        def call(**kw: Any) -> Any:
            self.calls[name] = self.calls.get(name, 0) + 1
            out = fn(**kw)
            if name == "put_item":
                self.wru += math.ceil(self._kb(kw["Item"]))
            elif name == "update_item":
                self.wru += math.ceil(self._kb(out.get("Attributes", {})))
            elif name == "transact_write_items":
                for t in kw["TransactItems"]:
                    item = next(iter(t.values())).get("Item", {})
                    self.wru += 2 * math.ceil(self._kb(item))
            elif name == "batch_write_item":
                for reqs in kw["RequestItems"].values():
                    for r in reqs:
                        self.wru += math.ceil(self._kb(r.get("PutRequest", {}).get("Item", {})))
            elif name == "get_item":
                self.rru += math.ceil(self._kb(out.get("Item", {})) / 4) * (
                    1.0 if kw.get("ConsistentRead") else 0.5
                )
            elif name == "query":
                self.rru += math.ceil(self._kb(out.get("Items", [])) / 4) * (
                    1.0 if kw.get("ConsistentRead") else 0.5
                )
            return out

        return call


class InProcessRunner:
    """ECS stand-in: starting a task runs the same entry function the Fargate container runs."""

    def __init__(self, rt: Runtime, llm: ScriptedLLM) -> None:
        self.rt, self.llm = rt, llm
        self.runs: list[tuple[str, bool]] = []

    async def running(self) -> list[RunningTask]:
        return []

    async def start(self, job_id: str, client_token: str) -> str:
        outcome = await run_analysis(self.rt, self.llm, job_id)
        self.runs.append((job_id, outcome.succeeded))
        return f"arn:inproc/{job_id}"

    async def stop(self, task_arn: str, reason: str) -> None:
        return None


def _zip_of(root: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                zf.write(path, f"{root.name}/{path.relative_to(root).as_posix()}")
    return buf.getvalue()


@pytest.fixture
async def app(aws: Any) -> AsyncIterator[FastAPI]:
    application = create_app(aws.settings, queue=SqsJobQueue(aws.settings))
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _pop_messages(aws: Any) -> list[tuple[str, dict[str, Any]]]:
    sqs = boto3.client("sqs", region_name="us-east-1")
    got = sqs.receive_message(QueueUrl=aws.queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=0)
    out = [(m["MessageId"], json.loads(m["Body"])) for m in got.get("Messages", [])]
    for m in got.get("Messages", []):
        sqs.delete_message(QueueUrl=aws.queue_url, ReceiptHandle=m["ReceiptHandle"])
    return out


async def _upload(client: httpx.AsyncClient, aws: Any, archive: bytes) -> tuple[str, str]:
    """Steps 1-3 of the browser flow; the POST to S3 is replaced by the equivalent put (moto cannot receive it)."""
    ticket = await client.post(
        "/api/v1/projects/zip/upload-url", json={"filename": "fastapi_rag.zip", "size": len(archive)}
    )
    assert ticket.status_code == 201, ticket.text
    body = ticket.json()
    assert body["fields"]["key"] == f"projects/{body['project_id']}/upload/source.zip"
    boto3.client("s3", region_name="us-east-1").put_object(
        Bucket=aws.bucket, Key=body["fields"]["key"], Body=archive, ContentType="application/zip"
    )
    done = await client.post(f"/api/v1/projects/{body['project_id']}/zip/complete")
    assert done.status_code == 202, done.text
    return done.json()["project_id"], done.json()["job_id"]


async def test_zip_to_guide_duplicate_delivery_resume_and_cleanup(
    aws: Any, app: FastAPI, client: httpx.AsyncClient
) -> None:
    rt: Runtime = app.state.runtime
    queue: SqsJobQueue = app.state.queue
    counting = CountingDynamo(rt.control._t._client)  # type: ignore[attr-defined]
    rt.control._t._client = counting  # type: ignore[attr-defined]
    assert (await client.post("/api/v1/sessions")).status_code == 201
    assert (await client.get("/ready")).json()["status"] == "ok"

    project_id, job_id = await _upload(client, aws, _zip_of(FIXTURE))
    # completing twice returns the same job: no second job, no second message
    again = await client.post(f"/api/v1/projects/{project_id}/zip/complete")
    assert again.status_code == 202 and again.json()["job_id"] == job_id
    msgs = _pop_messages(aws)
    assert [b for _, b in msgs] == [{"type": "analysis", "ref": job_id, "attempt": 1}]
    first = (await client.get(f"/api/v1/projects/{project_id}")).json()
    assert first["job"]["status"] == "queued"

    # a worker run that hits a capacity failure after the plan: the job goes back to `queued`, nothing is lost
    flaky = ScriptedLLM(fail_on_section=1)
    runner = InProcessRunner(rt, flaky)
    disp = Dispatcher(rt, queue, runner)
    msg_id, body = msgs[0]
    assert await disp.handle_message(msg_id, body) == "started"
    assert runner.runs == [(job_id, False)]
    mid = (await client.get(f"/api/v1/projects/{project_id}")).json()["job"]
    assert mid["status"] == "queued" and mid["attempt"] == 1 and mid["retryable"] is True
    plan_calls = flaky.calls.count("guide.plan")

    # the stopped-task event schedules the retry (exit 75); the rerun resumes at the checkpoints
    detail = {
        "overrides": {"containerOverrides": [{"name": "worker", "command": ["analysis", job_id]}]},
        "containers": [{"name": "worker", "exitCode": RETRY_EXIT_CODE}],
    }
    assert await disp.handle_task_stopped(detail) == "retry"
    healthy = ScriptedLLM()
    runner.llm = healthy
    assert await disp.handle_message("again", {"type": "analysis", "ref": job_id, "attempt": 2}) == "started"
    assert runner.runs[-1] == (job_id, True)
    assert "guide.plan" not in healthy.calls and plan_calls >= 1  # the plan stage was not redone

    # duplicate delivery of the same message after success changes nothing and spends nothing
    before = len(healthy.calls)
    assert await disp.handle_message("again", {"type": "analysis", "ref": job_id, "attempt": 2}) == "skipped"
    assert len(healthy.calls) == before

    # everything the UI reads is served from DynamoDB + S3
    done = (await client.get(f"/api/v1/projects/{project_id}")).json()
    assert done["job"]["status"] == "succeeded" and done["stats"]["chunks"] > 0
    progress = (await client.get(f"/api/v1/projects/{project_id}/progress")).json()
    types = [e["type"] for e in progress["events"]]
    assert types[0] == "stage" and types[-1] == "ready"
    seqs = [e["seq"] for e in progress["events"]]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    guide = (await client.get(f"/api/v1/projects/{project_id}/guide")).json()
    keys = [s["key"] for s in guide["sections"]]
    assert keys == ["overview", "architecture", "data-flow", "running"]
    section = (await client.get(f"/api/v1/projects/{project_id}/guide/sections/architecture")).json()
    assert section["status"] in ("ready", "degraded") and section["evidence"]
    chunk_id = next(iter(section["evidence"].values()))["chunk_id"]
    evidence = await client.get(f"/api/v1/projects/{project_id}/evidence/{chunk_id}")
    assert evidence.status_code == 200 and evidence.json()["text"]
    arch = (await client.get(f"/api/v1/projects/{project_id}/architecture")).json()
    assert arch["counts"]["files"] > 0 and arch["diagram"] is not None
    listed = (await client.get("/api/v1/sessions/current")).json()["projects"]
    assert [p["id"] for p in listed] == [project_id]

    # delete: hidden at once, then the cleanup message empties every store (and is safe to repeat)
    assert (await client.delete(f"/api/v1/projects/{project_id}")).status_code == 202
    assert (await client.get(f"/api/v1/projects/{project_id}")).status_code == 404
    # (the retry message scheduled earlier may be visible by now: it is a duplicate of finished work)
    cleanup = [m for m in _pop_messages(aws) if m[1]["type"] == "cleanup"]
    assert [b for _, b in cleanup] == [{"type": "cleanup", "ref": project_id}]
    assert await disp.handle_message("c1", cleanup[0][1]) == "cleaned"
    assert await disp.handle_message("c1", cleanup[0][1]) == "cleaned"
    assert await rt.control.get_project(project_id) is None
    assert await rt.control.get_job(job_id) is None
    assert await rt.control.sections(project_id) == [] and await rt.control.diagrams(project_id) == []
    assert await rt.progress.read_after(project_id, 0) == []
    assert await rt.objects.delete_prefix(f"projects/{project_id}/") == 0
    if out := os.environ.get("UNFOLD_DDB_PROFILE_OUT"):
        profile = {
            "requests": counting.calls,
            "estimated_wru": counting.wru,
            "estimated_rru": counting.rru,
            "chunks": done["stats"]["chunks"],
            "note": "one ZIP analysis (33 files) incl. 2 attempts, API reads and cleanup; payload-size estimate",
        }
        Path(out).write_text(json.dumps(profile, indent=2), encoding="utf-8")  # noqa: ASYNC240


async def test_upload_ticket_rules_ownership_size_and_type(aws: Any, client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/v1/sessions")).status_code == 201
    too_big = aws.settings.max_zip_bytes + 1
    big = await client.post("/api/v1/projects/zip/upload-url", json={"filename": "a.zip", "size": too_big})
    assert big.status_code == 413
    assert (
        await client.post("/api/v1/projects/zip/upload-url", json={"filename": "a.zip", "size": 0})
    ).status_code == 422

    ok = await client.post(
        "/api/v1/projects/zip/upload-url", json={"filename": "../../etc/a.zip", "size": 100}
    )
    pid = ok.json()["project_id"]
    s3 = boto3.client("s3", region_name="us-east-1")
    key = f"projects/{pid}/upload/source.zip"

    # completing before the object arrives is a typed error, not a job
    assert (await client.post(f"/api/v1/projects/{pid}/zip/complete")).status_code == 400
    # an object that is not a ZIP is rejected and deleted
    s3.put_object(Bucket=aws.bucket, Key=key, Body=b"not a zip at all")
    bad = await client.post(f"/api/v1/projects/{pid}/zip/complete")
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "ZIP_INVALID"
    with pytest.raises(s3.exceptions.ClientError):
        s3.head_object(Bucket=aws.bucket, Key=key)
    # an oversized object is rejected and deleted even if the policy was somehow bypassed
    s3.put_object(Bucket=aws.bucket, Key=key, Body=b"PK\x03\x04" + b"0" * (aws.settings.max_zip_bytes + 1))
    huge = await client.post(f"/api/v1/projects/{pid}/zip/complete")
    assert huge.status_code == 413

    # another session cannot complete (or even see) this project
    async with httpx.AsyncClient(transport=client._transport, base_url="http://test") as other:
        assert (await other.post("/api/v1/sessions")).status_code == 201
        assert (await other.post(f"/api/v1/projects/{pid}/zip/complete")).status_code == 404


async def test_concurrent_complete_calls_create_one_job(aws: Any, client: httpx.AsyncClient) -> None:
    """A retried or double-clicked `complete` must not start the same project twice."""
    import asyncio

    assert (await client.post("/api/v1/sessions")).status_code == 201
    ticket = await client.post("/api/v1/projects/zip/upload-url", json={"filename": "a.zip", "size": 100})
    pid = ticket.json()["project_id"]
    boto3.client("s3", region_name="us-east-1").put_object(
        Bucket=aws.bucket, Key=f"projects/{pid}/upload/source.zip", Body=b"PK" + b"0" * 100
    )
    first, second = await asyncio.gather(
        client.post(f"/api/v1/projects/{pid}/zip/complete"),
        client.post(f"/api/v1/projects/{pid}/zip/complete"),
    )
    assert first.status_code == 202 and second.status_code == 202, (first.text, second.text)
    assert first.json()["job_id"] == second.json()["job_id"]
    assert len({b["ref"] for _, b in _pop_messages(aws)}) == 1


async def test_per_session_active_job_cap_is_enforced_and_typed(aws: Any, client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/v1/sessions")).status_code == 201
    urls = ["https://github.com/octo/one", "https://github.com/octo/two", "https://github.com/octo/three"]
    codes = [(await client.post("/api/v1/projects/github", json={"url": u})).status_code for u in urls]
    assert codes == [202, 202, 429]  # max_active_jobs_per_session is 2 in the fixture
    assert len([b for _, b in _pop_messages(aws)]) == 2


async def test_unverified_callers_share_one_rate_limit_bucket(aws: Any) -> None:
    settings = aws.settings.model_copy(
        update={"proxy_hmac_secret": SecretStr("k" * 32), "max_sessions_per_ip_per_hour": 2}
    )
    application = create_app(settings, queue=SqsJobQueue(settings))
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://t") as c,
    ):
        # no signature: every caller lands in the `unverified` bucket whatever it claims to be
        codes = [
            (await c.post("/api/v1/sessions", headers={"x-forwarded-for": f"198.51.100.{i}"})).status_code
            for i in range(4)
        ]
        assert codes == [201, 201, 429, 429]
        from unfold.api import proxy_auth

        signed = proxy_auth.sign(b"k" * 32, "203.0.113.50")
        assert (await c.post("/api/v1/sessions", headers={"x-unfold-client": signed})).status_code == 201


async def test_a_project_over_the_chunk_cap_fails_typed_before_any_embedding(aws: Any) -> None:
    settings = aws.settings.model_copy(update={"max_chunks_per_project": 5})
    application = create_app(settings, queue=SqsJobQueue(settings))
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://t") as c,
    ):
        assert (await c.post("/api/v1/sessions")).status_code == 201
        project_id, job_id = await _upload(c, aws, _zip_of(FIXTURE))
        llm = ScriptedLLM()
        outcome = await run_analysis(application.state.runtime, llm, job_id)  # type: ignore[arg-type]
        assert not outcome.succeeded and not outcome.retry and llm.calls == []
        job = (await c.get(f"/api/v1/projects/{project_id}")).json()["job"]
        assert job["status"] == "failed" and job["error_code"] == "INPUT_TOO_LARGE"
