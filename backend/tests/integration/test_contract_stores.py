"""Port contracts run against the local adapter and the AWS adapter: `BlobStore`, `ProgressStore`, `RateLimiter`.

Parametrisations: `s3-moto` (in-process AWS, default run), `minio` and `s3-minio` (the MinIO SDK adapter and the
S3 adapter pointed at the compose MinIO; integration), `postgres`/`redis` (integration). The presigned-POST tests
at the end send real multipart requests to MinIO, which enforces the same policy conditions as S3.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from unfold.core.config import Settings
from unfold.core.errors import NotFound
from unfold.domain.records import ProjectRec
from unfold.persistence.s3_store import S3BlobStore
from unfold.ports import BlobStore, ProgressStore, RateLimiter
from unfold.runtime import Runtime

# --- BlobStore ----------------------------------------------------------------------------------------------------


@pytest.fixture(
    params=[
        "s3-moto",
        pytest.param("minio", marks=pytest.mark.integration),
        pytest.param("s3-minio", marks=pytest.mark.integration),
    ]
)
def blobs(request: pytest.FixtureRequest) -> BlobStore:
    if request.param == "s3-moto":
        return S3BlobStore(request.getfixturevalue("aws").settings)
    rt: Runtime = request.getfixturevalue("runtime")
    if request.param == "minio":
        return rt.objects
    return _minio_s3(rt.settings)


def _minio_s3(base: Settings) -> S3BlobStore:
    scheme = "https" if base.object_store_secure else "http"
    return S3BlobStore(
        base.model_copy(
            update={
                "aws_endpoint_url": f"{scheme}://{base.object_store_endpoint}",
                "aws_region_name": "us-east-1",
            }
        )
    )


async def test_blob_contract(blobs: BlobStore, tmp_path: Path) -> None:
    pid = str(uuid.uuid4())
    await blobs.ensure_bucket()
    await blobs.ping()
    await blobs.put_bytes(f"projects/{pid}/a/one.bin", b"one", "application/octet-stream")
    f = tmp_path / "two.bin"
    f.write_bytes(b"two" * 1000)
    await blobs.put_file(f"projects/{pid}/a/two.bin", f, "application/octet-stream")
    assert await blobs.get_bytes(f"projects/{pid}/a/one.bin") == b"one"
    got = await blobs.get_file(f"projects/{pid}/a/two.bin", tmp_path / "deep" / "dir" / "two.out")
    assert got.read_bytes() == b"two" * 1000
    with pytest.raises(NotFound):
        await blobs.get_bytes(f"projects/{pid}/missing")
    other = str(uuid.uuid4())
    await blobs.put_bytes(f"projects/{other}/keep", b"k")
    assert await blobs.delete_prefix(f"projects/{pid}/") == 2
    assert await blobs.delete_prefix(f"projects/{pid}/") == 0
    assert await blobs.get_bytes(f"projects/{other}/keep") == b"k"
    await blobs.delete_prefix(f"projects/{other}/")


# --- presigned POST against a real S3-compatible server ---------------------------------------------------------------


@pytest.fixture
def minio_s3(request: pytest.FixtureRequest) -> S3BlobStore:
    return _minio_s3(request.getfixturevalue("runtime").settings)


async def _post(url: str, fields: dict[str, str], body: bytes, *, name: str = "source.zip") -> httpx.Response:
    async with httpx.AsyncClient(timeout=20) as c:
        return await c.post(url, data=fields, files={"file": (name, body, "application/zip")})


@pytest.mark.integration
async def test_presigned_post_accepts_a_zip_within_the_policy(minio_s3: S3BlobStore) -> None:
    key = f"projects/{uuid.uuid4()}/upload/source.zip"
    post = await minio_s3.presign_upload(key, max_bytes=1024, content_type="application/zip", expires_s=60)
    resp = await _post(post.url, post.fields, b"PK\x03\x04" + b"0" * 100)
    assert resp.status_code in (200, 201, 204), resp.text
    info = await minio_s3.stat(key)
    assert info is not None and info.size == 104 and info.content_type == "application/zip"
    assert await minio_s3.get_range(key, 0, 4) == b"PK\x03\x04"
    await minio_s3.delete_prefix(key.rsplit("/upload/", 1)[0] + "/")


@pytest.mark.integration
async def test_presigned_post_rejects_oversize_wrong_type_and_other_keys(minio_s3: S3BlobStore) -> None:
    key = f"projects/{uuid.uuid4()}/upload/source.zip"
    post = await minio_s3.presign_upload(key, max_bytes=1024, content_type="application/zip", expires_s=60)
    too_big = await _post(post.url, post.fields, b"0" * 2048)
    assert too_big.status_code >= 400
    empty = await _post(post.url, post.fields, b"")
    assert empty.status_code >= 400  # content-length-range starts at 1
    wrong_type = await _post(post.url, {**post.fields, "Content-Type": "text/html"}, b"PK\x03\x04")
    assert wrong_type.status_code >= 400
    elsewhere = await _post(
        post.url, {**post.fields, "key": f"projects/{uuid.uuid4()}/upload/source.zip"}, b"PK\x03\x04"
    )
    assert elsewhere.status_code >= 400  # the signature covers the key: a ticket cannot write anywhere else
    assert await minio_s3.stat(key) is None


@pytest.mark.integration
async def test_presigned_post_expires(minio_s3: S3BlobStore) -> None:
    key = f"projects/{uuid.uuid4()}/upload/source.zip"
    post = await minio_s3.presign_upload(key, max_bytes=1024, content_type="application/zip", expires_s=1)
    await asyncio.sleep(2.5)
    assert (await _post(post.url, post.fields, b"PK\x03\x04")).status_code >= 400


# --- ProgressStore -----------------------------------------------------------------------------------------------------


@pytest.fixture(params=["dynamodb", pytest.param("postgres", marks=pytest.mark.integration)])
def progress_env(request: pytest.FixtureRequest) -> tuple[ProgressStore, Runtime]:
    rt = (
        Runtime.create(request.getfixturevalue("aws").settings)
        if request.param == "dynamodb"
        else request.getfixturevalue("runtime")
    )
    return rt.progress, rt


async def test_progress_contract(progress_env: tuple[ProgressStore, Runtime]) -> None:
    store, rt = progress_env
    now = datetime.now(UTC)
    s = await rt.control.create_session(uuid.uuid4().hex + uuid.uuid4().hex, now, now + timedelta(hours=1))
    p = ProjectRec(str(uuid.uuid4()), s.id, "github", "beginner", "t", now, now + timedelta(hours=1))
    await rt.control.create_project(p, None)
    seqs = [await store.publish(p.id, {"type": "stage", "n": i}) for i in range(4)]
    assert seqs == sorted(set(seqs)) and all(x > 0 for x in seqs)  # strictly increasing
    page = await store.read_after(p.id, seqs[1], limit=10)
    assert [e["n"] for _, e in page] == [2, 3]
    assert await store.read_after(p.id, seqs[-1]) == []
    assert await store.publish(str(uuid.uuid4()), {"type": "stage"}) == 0  # unknown project: best effort
    assert await store.delete_project(p.id) == 4
    assert await store.read_after(p.id, 0) == []
    await rt.control.delete_project_data(p.id)
    await rt.control.delete_session(s.token_hash)


# --- RateLimiter -------------------------------------------------------------------------------------------------------


@pytest.fixture(params=["dynamodb", pytest.param("redis", marks=pytest.mark.integration)])
def limiter(request: pytest.FixtureRequest) -> RateLimiter:
    rt = (
        Runtime.create(request.getfixturevalue("aws").settings)
        if request.param == "dynamodb"
        else request.getfixturevalue("runtime")
    )
    return rt.limiter


async def test_rate_limiter_contract(limiter: RateLimiter) -> None:
    key = f"contract:{uuid.uuid4()}"
    await limiter.ping()
    assert [await limiter.hit(key, 3, 3600) for _ in range(5)] == [True, True, True, False, False]
    assert await limiter.hit(f"{key}:other", 3, 3600)  # keys are independent
    results: list[Any] = await asyncio.gather(*(limiter.hit(f"{key}:c", 10, 3600) for _ in range(30)))
    assert results.count(True) == 10  # atomic under concurrency
