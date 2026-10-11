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
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import boto3
import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from unfold.api.main import create_app
from unfold.evaluation.scripted import ScriptedLLM
from unfold.exports.service import run_export
from unfold.orchestration.constants import RETRY_EXIT_CODE
from unfold.orchestration.pipeline import run_analysis
from unfold.runtime import Runtime
from unfold.worker.dispatch import Dispatcher, RunningTask
from unfold.worker.sqs_queue import SqsJobQueue

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).resolve().parents[3] / "evals" / "fixtures" / "repos" / "fastapi_rag"


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
        self.exports: list[str] = []

    async def running(self) -> list[RunningTask]:
        return []

    async def start(self, job_id: str, client_token: str) -> str:
        outcome = await run_analysis(self.rt, self.llm, job_id)
        self.runs.append((job_id, outcome.succeeded))
        return f"arn:inproc/{job_id}"

    async def start_export(self, export_id: str, client_token: str) -> str:
        await run_export(self.rt, export_id, None)  # type: ignore[arg-type]  # Markdown needs no Chromium
        self.exports.append(export_id)
        return f"arn:inproc/export-{export_id}"

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
    assert keys[0] == "overview" and len(keys) >= 4  # the adaptive plan, not a fixed outline
    section = (await client.get(f"/api/v1/projects/{project_id}/guide/sections/{keys[1]}")).json()
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


async def test_chat_export_and_regeneration_run_on_the_aws_profile(aws: Any) -> None:
    """Phase 3 on DynamoDB + S3 + SQS: a chat turn in the API process, a Markdown export as its own task, and a
    single-chapter regeneration as a worker job (the investigator, verifier and reviser never run in the API)."""
    llm = ScriptedLLM()
    application = create_app(aws.settings, queue=SqsJobQueue(aws.settings), llm=llm)
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://t") as client,
    ):
        rt: Runtime = application.state.runtime
        runner = InProcessRunner(rt, llm)
        disp = Dispatcher(rt, application.state.queue, runner)
        assert (await client.post("/api/v1/sessions")).status_code == 201
        project_id, job_id = await _upload(client, aws, _zip_of(FIXTURE))
        (msg_id, body), *_ = _pop_messages(aws)
        assert await disp.handle_message(msg_id, body) == "started"
        assert runner.runs == [(job_id, True)]
        base = f"/api/v1/projects/{project_id}"
        calls_after_analysis = len(llm.calls)

        # --- chat: created, answered (JSON and NDJSON), persisted in the control store -------------------------
        chat = (await client.post(f"{base}/chats")).json()
        reply = await client.post(
            f"/api/v1/chats/{chat['id']}/messages", json={"text": "Where is split_text?"}
        )
        assert reply.status_code == 200, reply.text
        turn = reply.json()
        assert turn["message"]["role"] == "assistant" and turn["message"]["evidence"]
        assert turn["user_message"]["seq"] == 1 and turn["message"]["seq"] == 2
        streamed = await client.post(
            f"/api/v1/chats/{chat['id']}/messages?stream=true", json={"text": "And the routes?"}
        )
        kinds = [json.loads(line)["type"] for line in streamed.text.splitlines() if line]
        assert kinds[0] == "chat.started" and kinds[-1] == "response.completed"
        detail = (await client.get(f"/api/v1/chats/{chat['id']}")).json()
        assert [m["seq"] for m in detail["messages"]] == [1, 2, 3, 4] and detail["chat"]["message_count"] == 4
        listed = (await client.get(f"{base}/chats")).json()
        assert [c["id"] for c in listed] == [chat["id"]]
        # another session cannot read it: ownership is enforced through the port
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://t"
        ) as other:
            assert (await other.post("/api/v1/sessions")).status_code == 201
            assert (await other.get(f"/api/v1/chats/{chat['id']}")).status_code == 404
            assert (
                await other.post(f"/api/v1/chats/{chat['id']}/messages", json={"text": "x"})
            ).status_code == 404

        # --- export: API records it and queues it; the dispatcher starts an export task; download redirects ----
        created = await client.post(f"{base}/exports/guide?format=md")
        assert created.status_code == 202, created.text
        export_id = created.json()["artifact_id"]
        assert (await client.post(f"{base}/exports/guide?format=md")).json()["artifact_id"] == export_id
        sent = [m for m in _pop_messages(aws) if m[1]["type"] == "export"]
        assert [b for _, b in sent] == [{"type": "export", "ref": export_id}]
        assert await disp.handle_message(sent[0][0], sent[0][1]) == "started"
        assert runner.exports == [export_id]
        art = (await client.get(f"/api/v1/exports/{export_id}")).json()
        assert art["status"] == "ready" and art["download_url"]
        dl = await client.get(art["download_url"], follow_redirects=False)
        assert dl.status_code == 302 and "X-Amz-Signature" in dl.headers["location"]  # presigned on AWS
        stored = await rt.objects.get_bytes(f"projects/{project_id}/exports/{export_id}.md")
        assert stored.startswith(b"# fastapi_rag")
        chat_export = await client.post(f"/api/v1/chats/{chat['id']}/exports?format=md")
        assert chat_export.status_code == 202
        assert (
            await disp.handle_message("x", {"type": "export", "ref": chat_export.json()["artifact_id"]})
            == "started"
        )
        assert len((await client.get(f"{base}/artifacts")).json()) == 2

        # --- regeneration: a typed job, queued like an analysis, run by the worker ---------------------------
        section = (await client.get(f"{base}/guide")).json()["sections"][1]["key"]
        before = (await client.get(f"{base}/guide/sections/{section}")).json()
        assert len(llm.calls) > calls_after_analysis  # chat spent model calls in the API process
        regen = await client.post(f"{base}/sections/{section}/regenerate")
        assert regen.status_code == 202, regen.text
        regen_job = regen.json()["job_id"]
        same = await client.post(f"{base}/sections/{section}/regenerate")
        assert same.json()["job_id"] == regen_job  # idempotent while it is queued
        queued = [m for m in _pop_messages(aws) if m[1]["type"] == "analysis"]
        assert [b["ref"] for _, b in queued] == [regen_job]
        progress = (await client.get(f"{base}/progress")).json()
        assert progress["job"]["status"] == "succeeded" and progress["job"]["kind"] == "analysis"
        assert (
            progress["regeneration"]["status"] == "queued"
            and progress["regeneration"]["section_key"] == section
        )

        assert await disp.handle_message(queued[0][0], queued[0][1]) == "started"
        assert runner.runs[-1] == (regen_job, True)
        progress = (await client.get(f"{base}/progress")).json()
        assert progress["regeneration"]["status"] == "succeeded"
        assert progress["job"]["kind"] == "analysis" and progress["job"]["status"] == "succeeded"
        after = (await client.get(f"{base}/guide/sections/{section}")).json()
        assert after["status"] in ("ready", "degraded") and after["content"] is not None
        row = await rt.control.get_section(project_id, section)
        assert row is not None and row.research is not None and row.verification is not None
        assert row.version == 3  # written, verified, regenerated
        assert before["key"] == after["key"]

        # --- cleanup removes chats, messages and exports together with the project ---------------------------
        assert rt.counters is not None
        await rt.counters.add(
            f"U#{job_id}", {"calls": 1}
        )  # what the gateway leaves per job (7 days otherwise)
        await rt.counters.add(f"U#chat:{chat['id']}", {"calls": 1})
        assert (await client.delete(base)).status_code == 202
        for url in (f"{base}/chats", f"/api/v1/chats/{chat['id']}", f"/api/v1/exports/{export_id}"):
            assert (await client.get(url)).status_code == 404, url
        assert await disp.handle_message("c", {"type": "cleanup", "ref": project_id}) == "cleaned"
        assert await rt.control.get_chat(chat["id"]) is None
        assert await rt.control.get_artifact(export_id) is None
        assert await rt.control.chat_messages(chat["id"]) == []
        assert await rt.objects.delete_prefix(f"projects/{project_id}/") == 0
        assert await rt.counters.get(f"U#{job_id}") == {}  # usage counters go with the project
        assert await rt.counters.get(f"U#chat:{chat['id']}") == {}
