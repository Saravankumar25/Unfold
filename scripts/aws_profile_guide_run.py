"""One real run of the pipeline on the AWS profile with the real Gemini gateway, then the deterministic rubric.

The control plane is DynamoDB and S3 over moto (in process, no AWS call), dense retrieval is the embedded artifact,
the graph is in memory and LLM coordination uses the counter store: exactly the `PERSISTENCE_BACKEND=dynamodb`
profile. Only the model API is real (keys from `.env`; free tier is fine). Writes `guide.json`, `ir.json`,
`chunks.json` (what `unfold.evaluation.rubric` reads), `scores.json` and `run.json`.

    cd backend && uv run python ../scripts/aws_profile_guide_run.py fastapi_rag ../evals/baselines/pipeline-v2-aws/run1
"""

# ruff: noqa: T201, E402, PT018, ASYNC240
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import time
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

for k, v in {
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SESSION_TOKEN": "testing",
    "AWS_DEFAULT_REGION": "us-east-1",
}.items():
    os.environ[k] = v
for k in ("AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_DYNAMODB", "AWS_ENDPOINT_URL_S3", "AWS_ENDPOINT_URL_SQS"):
    os.environ.pop(k, None)

import boto3
from moto import mock_aws

from unfold.core.config import Settings
from unfold.core.ids import new_session_token, token_hash
from unfold.domain.records import JobRec, ProjectRec
from unfold.evaluation.rubric import score_guide, summarize_ir
from unfold.exports.service import load_guide
from unfold.llm.factory import build_gateway
from unfold.orchestration.pipeline import run_analysis
from unfold.project_ir.snapshot import load_ir
from unfold.runtime import Runtime

ROOT = Path(__file__).resolve().parents[1]


def _create_table(ddb: object, name: str) -> None:
    attrs = ["PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK", "GSI3PK", "GSI3SK"]
    ddb.create_table(  # type: ignore[attr-defined]
        TableName=name,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": a, "AttributeType": "S"} for a in attrs],
        KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
        GlobalSecondaryIndexes=[
            {
                "IndexName": f"GSI{i}",
                "KeySchema": [
                    {"AttributeName": f"GSI{i}PK", "KeyType": "HASH"},
                    {"AttributeName": f"GSI{i}SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL" if i < 3 else "KEYS_ONLY"},
            }
            for i in (1, 2, 3)
        ],
    )


def _zip(root: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(root.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                zf.write(p, f"{root.name}/{p.relative_to(root).as_posix()}")
    return buf.getvalue()


async def main(name: str, out: Path) -> int:
    repo = ROOT
    fixture = repo / "evals" / "fixtures" / "repos" / name
    with mock_aws():
        table, bucket = "unfold-live", "unfold-live-bucket"
        _create_table(boto3.client("dynamodb", region_name="us-east-1"), table)
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
            model_cache_dir=str(repo / "backend" / ".model-cache"),
        )
        assert settings.gemini_keys, "no GEMINI_API_KEY_* in the environment or .env"
        rt = Runtime.create(settings)
        gateway = build_gateway(settings, None, counters=rt.counters)
        rt.llm = gateway
        now = datetime.now(UTC)
        pid, jid = str(uuid.uuid4()), str(uuid.uuid4())
        key = f"projects/{pid}/upload/source.zip"
        await rt.objects.put_bytes(key, _zip(fixture), "application/zip")
        session = await rt.control.create_session(
            token_hash(new_session_token()), now, now + timedelta(hours=4)
        )
        project = ProjectRec(
            id=pid,
            session_id=session.id,
            input_type="zip",
            technical_level="intermediate",
            analysis_version="live",
            created_at=now,
            expires_at=now + timedelta(hours=4),
            source_locator=f"{name}.zip",
            detected_name=name,
            upload_object_key=key,
        )
        await rt.control.create_project(project, JobRec(id=jid, project_id=pid, created_at=now))

        started = time.monotonic()
        attempts = 0
        while True:
            attempts += 1
            outcome = await run_analysis(rt, gateway, jid)
            if outcome.succeeded or not outcome.retry or attempts >= 4:
                break
            await asyncio.sleep(20)
        wall = round(time.monotonic() - started, 1)
        job = await rt.control.get_job(jid)
        assert job is not None
        print(
            json.dumps(
                {
                    "status": job.status,
                    "stage": job.current_stage,
                    "error": job.error_code,
                    "attempts": attempts,
                    "wall_s": wall,
                }
            )
        )
        if job.status != "succeeded":
            return 1
        project = await rt.control.get_project(pid)
        assert project is not None and project.ir_object_key
        _, sections, _ = await load_guide(rt, project)
        wanted = {str(e["chunk_id"]) for s in sections for e in s.evidence.values()}
        chunks = {
            c.id: {"path": c.path, "start_line": c.start_line, "end_line": c.end_line, "text": c.text}
            for c in await rt.chunks.get_many(pid, sorted(wanted))
        }
        ir = summarize_ir(await load_ir(rt.objects, project.ir_object_key))
        report = score_guide(sections, ir, chunks)
        usage = await gateway.usage(jid)
        out.mkdir(parents=True, exist_ok=True)
        (out / "guide.json").write_text(
            json.dumps({"sections": [s.model_dump(mode="json") for s in sections]}, indent=1),
            encoding="utf-8",
        )
        (out / "ir.json").write_text(ir.model_dump_json(indent=1), encoding="utf-8")
        (out / "chunks.json").write_text(json.dumps(chunks, indent=1), encoding="utf-8")
        (out / "scores.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
        run = {
            "backend": "dynamodb (moto) + S3 (moto) + embedded dense + in-memory graph",
            "attempts": attempts,
            "wall_seconds": wall,
            "tokens_in": usage["input_tokens"],
            "tokens_out": usage["output_tokens"],
            "calls": usage["calls"],
            "by_model": usage["by_model"],
            "by_purpose": usage["by_purpose"],
            "retries": usage["retries"],
            "rate_limited": usage["rate_limited"],
            "stage_progress_degraded": (job.stage_progress or {}).get("degraded", []),
        }
        (out / "run.json").write_text(json.dumps(run, indent=1, default=str), encoding="utf-8")
        print(json.dumps(report["scores"]))
        print(json.dumps(run, default=str))
        await rt.aclose()
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1], Path(sys.argv[2]))))
