"""AWS adapters against an in-process AWS (moto): blobs, chunks, counters, limiter, progress, queue.

Contracts the pipeline and API rely on, including concurrency (atomic counters) and failure typing. Needs no
services, so it runs in the default test run. What moto cannot prove (IAM, quotas, real latency) is listed in
`docs/infra/` as "requires AWS".
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from pathlib import Path
from typing import Any

import boto3
import pytest

from unfold.core.errors import NotFound, StoreUnavailable, UnfoldError
from unfold.indexing.models import Chunk
from unfold.indexing.store import ObjectChunkStore
from unfold.persistence.dynamodb.counters import DynamoCounters, DynamoRateLimiter
from unfold.persistence.dynamodb.table import DynamoTable
from unfold.persistence.s3_store import S3BlobStore
from unfold.ports import UploadStore
from unfold.runtime import Runtime
from unfold.worker.sqs_queue import SqsJobQueue

# --- S3 -------------------------------------------------------------------------------------------------------


async def test_s3_blob_roundtrip_prefix_delete_and_typed_errors(aws: Any, tmp_path: Path) -> None:
    store = S3BlobStore(aws.settings)
    pid = str(uuid.uuid4())
    await store.ensure_bucket()
    await store.ping()
    await store.put_bytes(f"projects/{pid}/ir/1.json.gz", b"hello", "application/gzip")
    src = tmp_path / "in.bin"
    src.write_bytes(b"x" * 1000)
    await store.put_file(f"projects/{pid}/upload/source.zip", src, "application/zip")
    assert await store.get_bytes(f"projects/{pid}/ir/1.json.gz") == b"hello"
    out = await store.get_file(f"projects/{pid}/upload/source.zip", tmp_path / "sub" / "out.bin")
    assert out.read_bytes() == b"x" * 1000
    with pytest.raises(NotFound):
        await store.get_bytes(f"projects/{pid}/missing")
    assert await store.delete_prefix(f"projects/{pid}/") == 2
    assert await store.delete_prefix(f"projects/{pid}/") == 0  # idempotent
    with pytest.raises(NotFound):
        await store.get_bytes(f"projects/{pid}/ir/1.json.gz")


async def test_s3_delete_prefix_leaves_other_projects_alone(aws: Any) -> None:
    store = S3BlobStore(aws.settings)
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    await store.put_bytes(f"projects/{a}/x", b"1")
    await store.put_bytes(f"projects/{b}/x", b"2")
    assert await store.delete_prefix(f"projects/{a}/") == 1
    assert await store.get_bytes(f"projects/{b}/x") == b"2"


async def test_s3_upload_policy_pins_key_type_and_size(aws: Any) -> None:
    store = S3BlobStore(aws.settings)
    assert isinstance(store, UploadStore)
    key = f"projects/{uuid.uuid4()}/upload/source.zip"
    post = await store.presign_upload(
        key, max_bytes=50 * 1024 * 1024, content_type="application/zip", expires_s=600
    )
    assert post.fields["key"] == key and post.max_bytes == 50 * 1024 * 1024
    policy = json.loads(base64.b64decode(post.fields["policy"]))
    conditions = policy["conditions"]
    assert ["content-length-range", 1, 50 * 1024 * 1024] in conditions
    assert {"key": key} in conditions and {"Content-Type": "application/zip"} in conditions
    assert {"bucket": aws.bucket} in conditions
    assert post.url.endswith(aws.bucket) or aws.bucket in post.url


async def test_s3_stat_range_and_delete(aws: Any) -> None:
    store = S3BlobStore(aws.settings)
    key = f"projects/{uuid.uuid4()}/upload/source.zip"
    assert await store.stat(key) is None
    await store.put_bytes(key, b"PK\x03\x04rest-of-archive", "application/zip")
    info = await store.stat(key)
    assert info is not None and info.size == 19 and info.content_type == "application/zip"
    assert await store.get_range(key, 0, 4) == b"PK\x03\x04"
    await store.delete(key)
    assert await store.stat(key) is None


async def test_s3_outage_is_a_typed_store_error(aws: Any) -> None:
    class Broken:
        def put_object(self, **_: Any) -> None:
            from botocore.exceptions import EndpointConnectionError

            raise EndpointConnectionError(endpoint_url="https://s3.invalid")

    store = S3BlobStore(aws.settings, client=Broken())
    with pytest.raises(StoreUnavailable):
        await store.put_bytes("projects/a/b", b"x")


# --- object chunk store -----------------------------------------------------------------------------------------


def _chunk(pid: str, path: str, i: int, **kw: Any) -> Chunk:
    return Chunk(
        id=f"ch:{path}:{i}", project_id=pid, path=path, start_line=i * 10, end_line=i * 10 + 9, kind="function",
        text=f"def f{i}(): pass", content_hash=f"h{i}", **kw,
    )  # fmt: skip


async def test_object_chunk_store_matches_the_repository_contract(aws: Any) -> None:
    pid = str(uuid.uuid4())
    objects = S3BlobStore(aws.settings)
    store = ObjectChunkStore(objects)
    chunks = [
        _chunk(pid, "b.py", 0, symbol_id="sym:b0"),
        _chunk(pid, "a.py", 1, symbol_id="sym:a1"),
        _chunk(pid, "a.py", 0, symbol_id="sym:a0"),
    ]
    assert await store.replace_project_chunks(pid, chunks) == 3
    assert await store.all_ids(pid) == ["ch:a.py:0", "ch:a.py:1", "ch:b.py:0"]  # path, line order
    got = await store.get_many(pid, ["ch:b.py:0", "nope", "ch:a.py:1", "ch:b.py:0"])
    assert [c.id for c in got] == ["ch:b.py:0", "ch:a.py:1"]  # requested order, unknown skipped, deduped
    assert [c.id for c in await store.by_path(pid, "a.py")] == ["ch:a.py:0", "ch:a.py:1"]
    assert [c.id for c in await store.for_symbols(pid, ["sym:b0", "sym:a0"])] == ["ch:a.py:0", "ch:b.py:0"]
    assert await store.set_subsystems(pid, {"ch:a.py:0": "s1", "ch:a.py:1": "s1", "ch:zzz": "s2"}) == 2
    assert await store.set_subsystems(pid, {"ch:a.py:0": "s1"}) == 0  # unchanged rows are not rewritten
    # a second process (fresh cache) sees what the first persisted
    other = ObjectChunkStore(objects)
    assert {c.id: c.subsystem_id for c in await other.list_project(pid)} == {
        "ch:a.py:0": "s1",
        "ch:a.py:1": "s1",
        "ch:b.py:0": None,
    }
    with pytest.raises(ValueError):
        await store.replace_project_chunks(pid, [_chunk(str(uuid.uuid4()), "x.py", 0)])
    await objects.delete_prefix(f"projects/{pid}/")
    with pytest.raises(NotFound):
        await ObjectChunkStore(objects).list_project(pid)


# --- counters and limiter -------------------------------------------------------------------------------------


async def test_counter_conditional_add_never_overshoots_under_concurrency(aws: Any) -> None:
    counters = DynamoCounters(DynamoTable(aws.settings))
    results = await asyncio.gather(
        *(counters.add("job1", {"calls": 1}, limits={"calls": 10}) for _ in range(40))
    )
    assert sum(r is not None for r in results) == 10  # exactly the limit, never 11
    assert (await counters.get("job1"))["calls"] == 10
    assert await counters.add("job1", {"calls": 1}, limits={"calls": 10}) is None
    # several attributes in one atomic step: both or neither
    assert await counters.add("m", {"rpm": 1, "tpm": 90}, limits={"rpm": 5, "tpm": 100}) == {
        "rpm": 1,
        "tpm": 90,
    }
    assert await counters.add("m", {"rpm": 1, "tpm": 20}, limits={"rpm": 5, "tpm": 100}) is None
    assert await counters.get("m") == {"rpm": 1, "tpm": 90}
    assert await counters.add("m", {"tpm": 500}, limits={"tpm": 100}) is None  # one amount above the limit


async def test_rate_limiter_counts_windows_and_denied_attempts(aws: Any) -> None:
    table = DynamoTable(aws.settings)
    limiter = DynamoRateLimiter(DynamoCounters(table), table)
    await limiter.ping()
    outcomes = await asyncio.gather(*(limiter.hit("sessions:ip:1.2.3.4", 5, 3600) for _ in range(20)))
    assert outcomes.count(True) == 5 and outcomes.count(False) == 15
    assert await limiter.hit("sessions:ip:5.6.7.8", 5, 3600)  # other keys are independent


async def test_table_outage_fails_closed_with_a_typed_error(aws: Any) -> None:
    class Down:
        def update_item(self, **_: Any) -> None:
            from botocore.exceptions import EndpointConnectionError

            raise EndpointConnectionError(endpoint_url="https://dynamodb.invalid")

    table = DynamoTable(aws.settings, client=Down())
    with pytest.raises(StoreUnavailable):
        await DynamoRateLimiter(DynamoCounters(table), table).hit("k", 1, 60)


# --- progress ---------------------------------------------------------------------------------------------------


async def test_progress_events_are_ordered_replayable_and_deleted(aws: Any) -> None:
    rt = Runtime.create(aws.settings)
    pid, sid = str(uuid.uuid4()), None
    from datetime import UTC, datetime, timedelta

    from unfold.domain.records import ProjectRec

    now = datetime.now(UTC)
    sid = (await rt.control.create_session(uuid.uuid4().hex, now, now + timedelta(hours=1))).id
    await rt.control.create_project(
        ProjectRec(pid, sid, "github", "beginner", "t", now, now + timedelta(hours=1)), None
    )
    seqs = [await rt.progress.publish(pid, {"type": "stage", "n": i}) for i in range(5)]
    assert seqs == [1, 2, 3, 4, 5]
    page = await rt.progress.read_after(pid, 2, limit=2)
    assert [(s, e["n"]) for s, e in page] == [(3, 2), (4, 3)]
    assert [s for s, _ in await rt.progress.read_after(pid, 5)] == []
    assert await rt.progress.publish(str(uuid.uuid4()), {"type": "x"}) == 0  # unknown project: best effort
    assert await rt.progress.delete_project(pid) == 5
    assert await rt.progress.read_after(pid, 0) == []


# --- SQS --------------------------------------------------------------------------------------------------------


def _messages(aws: Any, url: str) -> list[dict[str, Any]]:
    sqs = boto3.client("sqs", region_name="us-east-1")
    got = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10, VisibilityTimeout=0)
    return [json.loads(m["Body"]) for m in got.get("Messages", [])]


async def test_sqs_queue_sends_analysis_and_cleanup_messages(aws: Any) -> None:
    queue = SqsJobQueue(aws.settings)
    await queue.enqueue_analysis("job-1", 2)
    await queue.enqueue_project_cleanup("proj-1")
    bodies = _messages(aws, aws.queue_url)
    assert {"type": "analysis", "ref": "job-1", "attempt": 2} in bodies
    assert {"type": "cleanup", "ref": "proj-1"} in bodies
    await queue.aclose()


async def test_sqs_deferred_message_is_not_visible_yet(aws: Any) -> None:
    queue = SqsJobQueue(aws.settings)
    await queue.enqueue_analysis("job-2", 1, defer_s=30)
    assert _messages(aws, aws.queue_url) == []


async def test_sqs_failure_is_a_typed_retryable_error(aws: Any) -> None:
    class Down:
        def send_message(self, **_: Any) -> None:
            from botocore.exceptions import EndpointConnectionError

            raise EndpointConnectionError(endpoint_url="https://sqs.invalid")

    queue = SqsJobQueue(aws.settings, client=Down())
    with pytest.raises(UnfoldError) as exc:
        await queue.enqueue_analysis("job-3", 1)
    assert exc.value.retryable and exc.value.code.value == "QUEUE_UNAVAILABLE"


# --- SSM secrets ------------------------------------------------------------------------------------------------


def test_ssm_secrets_export_only_known_names_and_never_override(aws: Any) -> None:
    from unfold.persistence.ssm_secrets import load_ssm_env

    ssm = boto3.client("ssm", region_name="us-east-1")
    for name, value in {
        "gemini_api_key_1": "g1",
        "groq_api_key_1": "q1",
        "proxy_hmac_secret": "hmac",
        "not_a_setting": "x",
        "path_traversal": "y",
    }.items():
        ssm.put_parameter(Name=f"/unfold/dev/{name}", Value=value, Type="SecureString")
    ssm.put_parameter(Name="/unfold/other/gemini_api_key_2", Value="elsewhere", Type="SecureString")
    env: dict[str, str] = {"GROQ_API_KEY_1": "explicit"}
    exported = load_ssm_env("/unfold/dev", environ=env)
    assert sorted(exported) == ["GEMINI_API_KEY_1", "PROXY_HMAC_SECRET"]
    assert env == {"GROQ_API_KEY_1": "explicit", "GEMINI_API_KEY_1": "g1", "PROXY_HMAC_SECRET": "hmac"}
    assert load_ssm_env("", environ=env) == []


def test_ssm_outage_is_a_typed_error() -> None:
    from botocore.exceptions import EndpointConnectionError

    from unfold.persistence.ssm_secrets import load_ssm_env

    class Down:
        def get_parameters_by_path(self, **_: Any) -> None:
            raise EndpointConnectionError(endpoint_url="https://ssm.invalid")

    with pytest.raises(StoreUnavailable):
        load_ssm_env("/unfold/dev/", environ={}, client=Down())
