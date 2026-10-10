"""`BlobStore` and `UploadStore` on S3 (ADR 0014). boto3 is synchronous, so calls run in worker threads.

The bucket is created by Terraform; the runtime roles hold object access under `projects/` only, so this adapter
never creates a bucket and `ping` lists a single key under that prefix (the one list the IAM condition allows).
Browser uploads are presigned POSTs whose policy pins the exact key, content type and a content-length range:
S3 itself rejects an oversized or mistyped body, the API never carries the bytes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from unfold.core.config import Settings
from unfold.core.errors import NotFound, StoreUnavailable
from unfold.ports import ObjectStat, PresignedPost, UploadStore

_MISSING = {"NoSuchKey", "NotFound", "404"}


class S3BlobStore(UploadStore):
    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self.bucket = settings.object_store_bucket
        if client is None:
            kwargs: dict[str, Any] = {}
            if settings.aws_region_name:
                kwargs["region_name"] = settings.aws_region_name
            if settings.aws_endpoint_url:
                kwargs["endpoint_url"] = settings.aws_endpoint_url
                kwargs["aws_access_key_id"] = settings.object_store_access_key
                kwargs["aws_secret_access_key"] = settings.object_store_secret_key.get_secret_value()
            client = boto3.client(
                "s3",
                config=Config(
                    signature_version="s3v4",
                    retries={"max_attempts": 4, "mode": "standard"},
                    connect_timeout=3,
                    read_timeout=30,
                    max_pool_connections=32,
                    s3={"addressing_style": "path" if settings.aws_endpoint_url else "virtual"},
                ),
                **kwargs,
            )
        self._client = client

    async def ensure_bucket(self) -> None:
        """Terraform owns the bucket; nothing to create at run time."""

    async def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        await self._call(
            lambda: self._client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        )

    async def put_file(self, key: str, path: Path, content_type: str = "application/octet-stream") -> None:
        await self._call(
            lambda: self._client.upload_file(
                str(path), self.bucket, key, ExtraArgs={"ContentType": content_type}
            )
        )

    async def get_bytes(self, key: str) -> bytes:
        def _get() -> bytes:
            body = self._client.get_object(Bucket=self.bucket, Key=key)["Body"]
            try:
                return bytes(body.read())
            finally:
                body.close()

        return await self._call(_get)

    async def get_file(self, key: str, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        await self._call(lambda: self._client.download_file(self.bucket, key, str(path)))
        return path

    async def delete_prefix(self, prefix: str) -> int:
        def _delete() -> int:
            n = 0
            for page in self._client.get_paginator("list_objects_v2").paginate(
                Bucket=self.bucket, Prefix=prefix
            ):
                keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
                if keys:
                    self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys, "Quiet": True})
                    n += len(keys)
            # an interrupted upload under the prefix would otherwise keep (and bill) its parts
            uploads = self._client.list_multipart_uploads(Bucket=self.bucket, Prefix=prefix)
            for up in uploads.get("Uploads", []):
                self._client.abort_multipart_upload(
                    Bucket=self.bucket, Key=up["Key"], UploadId=up["UploadId"]
                )
            return n

        return await self._call(_delete)

    async def ping(self) -> None:
        await self._call(
            lambda: self._client.list_objects_v2(Bucket=self.bucket, Prefix="projects/", MaxKeys=1)
        )

    # --- UploadStore ------------------------------------------------------------------------------------

    async def presign_upload(
        self, key: str, *, max_bytes: int, content_type: str, expires_s: int
    ) -> PresignedPost:
        def _sign() -> dict[str, Any]:
            signed: dict[str, Any] = self._client.generate_presigned_post(
                Bucket=self.bucket,
                Key=key,
                Fields={"Content-Type": content_type},
                Conditions=[["content-length-range", 1, max_bytes], {"Content-Type": content_type}],
                ExpiresIn=expires_s,
            )
            return signed

        signed = await self._call(_sign)
        return PresignedPost(
            url=signed["url"],
            fields=signed["fields"],
            key=key,
            max_bytes=max_bytes,
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_s),
        )

    async def stat(self, key: str) -> ObjectStat | None:
        try:
            head = await self._call(lambda: self._client.head_object(Bucket=self.bucket, Key=key))
        except NotFound:
            return None
        return ObjectStat(size=int(head["ContentLength"]), content_type=str(head.get("ContentType", "")))

    async def get_range(self, key: str, start: int, length: int) -> bytes:
        def _get() -> bytes:
            rng = f"bytes={start}-{start + length - 1}"
            body = self._client.get_object(Bucket=self.bucket, Key=key, Range=rng)["Body"]
            try:
                return bytes(body.read())
            finally:
                body.close()

        return await self._call(_get)

    async def delete(self, key: str) -> None:
        await self._call(lambda: self._client.delete_object(Bucket=self.bucket, Key=key))

    async def _call[T](self, fn: Callable[[], T]) -> T:
        try:
            return await asyncio.to_thread(fn)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in _MISSING:
                raise NotFound("Object not found.") from exc
            raise StoreUnavailable(f"Object store error: {code or type(exc).__name__}") from exc
        except (BotoCoreError, OSError) as exc:
            raise StoreUnavailable(f"Object store unreachable: {type(exc).__name__}") from exc
