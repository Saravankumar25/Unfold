"""API latency on the AWS profile: non-LLM endpoints (p50/p95 over many requests) and chat first token.

Runs one analysis with the scripted model (so there is a real guide, evidence and index to read), then pins the process
to `--cpus` logical CPUs (a 3 538 MB Lambda gets about 2 vCPU), serves the app on a real localhost socket and measures:

* non-LLM endpoints through the real HTTP stack (uvicorn + httpx), DynamoDB and S3 over moto (in process);
* chat turns with the scripted model (retrieval, context compile, extractive answer, grounding): server-side
  `timings` and the client-side time to the first NDJSON `response.delta` line and to the end of the stream;
* with `--gemini N`, N turns with the real Gemini gateway (first token = retrieval + the provider's first token).

Moto costs tens of milliseconds per call, a laptop is not a Lambda: read the numbers as a regression baseline.

    cd backend && uv run --with psutil python ../scripts/profile_api.py ../.profile/api.json --gemini 3
"""

# ruff: noqa: T201, E402, PT018, ASYNC240, S101
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aws_profile_guide_run as ap  # sets the moto environment and imports the pipeline
import boto3
import httpx
import uvicorn
from moto import mock_aws

from profile_worker import CountingDynamo, dist
from unfold.api.main import create_app
from unfold.core.config import Settings
from unfold.domain.records import JobRec, ProjectRec
from unfold.evaluation.scripted import ScriptedLLM
from unfold.llm.factory import build_gateway
from unfold.orchestration.pipeline import run_analysis
from unfold.worker.sqs_queue import SqsJobQueue

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = [
    "Where is split_text defined?",
    "How does the retrieval combine BM25 and vector search?",
    "What does the arq worker do?",
    "Which database tables exist?",
    "How is the application configured?",
    "What happens when a document is uploaded?",
    "Where are the API routes declared?",
    "How are embeddings stored in Qdrant?",
    "What does the Dockerfile run?",
    "How do the tests run in CI?",
    "Which environment variables are needed?",
    "How does reciprocal rank fusion work here?",
    "What migrations are there?",
    "Where is the health check?",
    "What does the ingest route return?",
    "How is the chunk size chosen?",
    "Which module talks to Postgres?",
    "How does the service answer a question end to end?",
    "What is in the README?",
    "Which libraries does the project depend on?",
    "How are errors handled in the API?",
    "Where are settings loaded?",
]


COUNTING: list[CountingDynamo] = []


async def timed_get(c: httpx.AsyncClient, path: str, n: int, method: str = "GET") -> dict[str, float]:
    xs: list[float] = []
    before, w0, r0 = sum(COUNTING[0].calls.values()), COUNTING[0].wru, COUNTING[0].rru
    for _ in range(n):
        t = time.perf_counter()
        r = await c.request(method, path)
        xs.append((time.perf_counter() - t) * 1000)
        assert r.status_code < 300, (path, r.status_code, r.text[:200])
    out = {k: round(v, 1) if k != "n" else v for k, v in dist(xs).items()}
    out["ddb_requests_per_call"] = round((sum(COUNTING[0].calls.values()) - before) / n, 2)
    out["ddb_wru_per_call"] = round((COUNTING[0].wru - w0) / n, 2)
    out["ddb_rru_per_call"] = round((COUNTING[0].rru - r0) / n, 2)
    return out


async def stream_turn(c: httpx.AsyncClient, chat_id: str, text: str) -> dict[str, float]:
    t = time.perf_counter()
    first = total = 0.0
    async with c.stream("POST", f"/api/v1/chats/{chat_id}/messages?stream=true", json={"text": text}) as r:
        assert r.status_code == 200, r.status_code
        async for line in r.aiter_lines():
            if '"response.delta"' in line and not first:
                first = (time.perf_counter() - t) * 1000
            if '"response.completed"' in line:
                total = (time.perf_counter() - t) * 1000
    return {"first_token_ms": first, "total_ms": total}


async def chat_series(c: httpx.AsyncClient, base: str, questions: list[str]) -> dict[str, Any]:
    first: list[float] = []
    total: list[float] = []
    retrieval: list[float] = []
    chat_id = ""
    for i, q in enumerate(questions):
        if i % 4 == 0:  # a fresh chat every few turns keeps history out of the comparison
            chat_id = (await c.post(f"{base}/chats")).json()["id"]
        m = await stream_turn(c, chat_id, q)
        first.append(m["first_token_ms"])
        total.append(m["total_ms"])
        reply = await c.post(f"/api/v1/chats/{chat_id}/messages", json={"text": q + " Be brief."})
        assert reply.status_code == 200, reply.text[:300]
        retrieval.append(float(reply.json()["timings"].get("retrieval_ms", 0)))
    return {
        "client_first_delta_ms": {k: round(v, 1) for k, v in dist(first).items()},
        "client_stream_total_ms": {k: round(v, 1) for k, v in dist(total).items()},
        "server_retrieval_ms_json": {k: round(v, 1) for k, v in dist(retrieval).items()},
        "first_request_first_delta_ms": round(first[0], 1),
    }


async def main(out: Path, gemini: int, cpus: int, target: str) -> int:
    fixture = ROOT / "evals" / "fixtures" / "repos" / target
    report: dict[str, Any] = {"target": target, "cpus_pinned_for_api": cpus}
    with mock_aws():
        table, bucket = "unfold-api", "unfold-api-bucket"
        ap._create_table(boto3.client("dynamodb", region_name="us-east-1"), table)
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=bucket)
        queue = boto3.client("sqs", region_name="us-east-1").create_queue(QueueName="q")["QueueUrl"]
        settings = Settings(
            app_env="test",
            persistence_backend="dynamodb",
            dense_backend="artifact",
            graph_backend="memory",
            aws_region_name="us-east-1",
            dynamodb_table=table,
            object_store_bucket=bucket,
            jobs_queue_url=queue,
            model_cache_dir=str(ROOT / "backend" / ".model-cache"),
            max_chat_messages_per_minute=10_000,
            max_chat_messages_per_session_per_hour=10_000,
            max_chat_messages_global_per_day=100_000,
            max_chats_per_project=1000,
            max_sessions_per_ip_per_hour=10_000,
            max_sessions_per_hour=10_000,
        )
        scripted = ScriptedLLM()
        app = create_app(settings, queue=SqsJobQueue(settings), llm=scripted)
        async with app.router.lifespan_context(app):
            rt = app.state.runtime
            COUNTING.append(CountingDynamo(rt.control._t._client))  # type: ignore[attr-defined]
            rt.control._t._client = COUNTING[0]  # type: ignore[attr-defined]
            psutil.Process().cpu_affinity(list(range(4)))  # the analysis ran on a 4 vCPU task
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as seed:
                session_id = (await seed.post("/api/v1/sessions")).json()["session_id"]
                token = seed.cookies[settings.session_cookie_name]
            now = datetime.now(UTC)
            pid, jid = str(uuid.uuid4()), str(uuid.uuid4())
            key = f"projects/{pid}/upload/source.zip"
            await rt.objects.put_bytes(key, ap._zip(fixture), "application/zip")
            await rt.control.create_project(
                ProjectRec(
                    id=pid,
                    session_id=session_id,
                    input_type="zip",
                    technical_level="intermediate",
                    analysis_version="profile",
                    created_at=now,
                    expires_at=now + timedelta(hours=4),
                    source_locator=f"{target}.zip",
                    detected_name=target,
                    upload_object_key=key,
                ),
                JobRec(id=jid, project_id=pid, created_at=now),
            )
            t = time.monotonic()
            outcome = await run_analysis(rt, scripted, jid)  # type: ignore[arg-type]
            assert outcome.succeeded
            report["analysis_wall_s_scripted"] = round(time.monotonic() - t, 1)

            psutil.Process().cpu_affinity(list(range(cpus)))
            port = 8765
            server = uvicorn.Server(
                uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
            )
            serving = asyncio.create_task(server.serve())
            while not server.started:
                await asyncio.sleep(0.05)
            base = f"/api/v1/projects/{pid}"
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}",
                cookies={settings.session_cookie_name: token},
                timeout=120,
            ) as c:
                guide = (await c.get(f"{base}/guide")).json()
                sec = guide["sections"][0]["key"]
                chunk = (await c.get(f"{base}/guide/sections/{sec}")).json()["evidence"]
                chunk_id = next(iter(chunk.values()))["chunk_id"] if chunk else ""
                report["http_ms"] = {
                    "GET /health": await timed_get(c, "/health", 100),
                    "GET /sessions/current": await timed_get(c, "/api/v1/sessions/current", 100),
                    "GET /projects/{id}": await timed_get(c, base, 100),
                    "GET /projects/{id}/progress": await timed_get(c, f"{base}/progress", 100),
                    "GET /projects/{id}/guide": await timed_get(c, f"{base}/guide", 100),
                    "GET /guide/sections/{key}": await timed_get(c, f"{base}/guide/sections/{sec}", 100),
                    "GET /evidence/{chunk}": await timed_get(c, f"{base}/evidence/{chunk_id}", 100),
                    "GET /architecture": await timed_get(c, f"{base}/architecture", 50),
                    "POST /sessions/current/touch": await timed_get(
                        c, "/api/v1/sessions/current/touch", 100, "POST"
                    ),
                    "POST /projects/{id}/chats": await timed_get(c, f"{base}/chats", 25, "POST"),
                }
                before, w0, r0 = sum(COUNTING[0].calls.values()), COUNTING[0].wru, COUNTING[0].rru
                report["chat_scripted"] = await chat_series(c, base, QUESTIONS)
                report["chat_scripted"]["ddb_requests_per_turn"] = round(
                    (sum(COUNTING[0].calls.values()) - before) / (2 * len(QUESTIONS)), 1
                )
                report["chat_scripted"]["ddb_wru_per_turn"] = round(
                    (COUNTING[0].wru - w0) / (2 * len(QUESTIONS)), 1
                )
                report["chat_scripted"]["ddb_rru_per_turn"] = round(
                    (COUNTING[0].rru - r0) / (2 * len(QUESTIONS)), 1
                )
                if gemini:
                    assert settings.gemini_keys, "no GEMINI_API_KEY_* in the environment or .env"
                    rt.llm = build_gateway(settings, None, counters=rt.counters)
                    report["chat_gemini"] = await chat_series(c, base, QUESTIONS[:gemini])
                    report["chat_gemini"]["note"] = "each turn is asked twice: once streamed, once as JSON"
            server.should_exit = True
            await serving
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1), encoding="utf-8")
        print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("out", type=Path)
    p.add_argument("--gemini", type=int, default=0, help="number of questions to ask the real gateway")
    p.add_argument("--cpus", type=int, default=2)
    p.add_argument("--target", default="fastapi_rag")
    a = p.parse_args()
    sys.exit(asyncio.run(main(a.out, a.gemini, a.cpus, a.target)))
