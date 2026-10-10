"""`BlobStore` adapter over the MinIO SDK (ESD §4.6). Sync SDK → worker threads. The S3 adapter replaces it in phase 4."""

from __future__ import annotations

import asyncio
import io
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from minio import Minio
from minio.error import S3Error

from unfold.core.config import Settings
from unfold.core.errors import NotFound, StoreUnavailable
from unfold.ports import BlobStore, project_prefix

__all__ = ["MinioBlobStore", "project_prefix"]


class MinioBlobStore(BlobStore):
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.object_store_bucket
        self._client = Minio(
            settings.object_store_endpoint,
            access_key=settings.object_store_access_key,
            secret_key=settings.object_store_secret_key.get_secret_value(),
            secure=settings.object_store_secure,
        )

    async def ensure_bucket(self) -> None:
        def _ensure() -> None:
            if not self._client.bucket_exists(self.bucket):
                self._client.make_bucket(self.bucket)

        await self._call(_ensure)

    async def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        await self._call(
            lambda: self._client.put_object(
                self.bucket, key, io.BytesIO(data), len(data), content_type=content_type
            )
        )

    async def put_file(self, key: str, path: Path, content_type: str = "application/octet-stream") -> None:
        await self._call(
            lambda: self._client.fput_object(self.bucket, key, str(path), content_type=content_type)
        )

    async def get_bytes(self, key: str) -> bytes:
        def _get() -> bytes:
            resp = self._client.get_object(self.bucket, key)
            try:
                return resp.read()
            finally:
                resp.close()
                resp.release_conn()

        return await self._call(_get)

    async def get_file(self, key: str, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        await self._call(lambda: self._client.fget_object(self.bucket, key, str(path)))
        return path

    async def delete_prefix(self, prefix: str) -> int:
        def _delete() -> int:
            n = 0
            for obj in self._client.list_objects(self.bucket, prefix=prefix, recursive=True):
                if obj.object_name:
                    self._client.remove_object(self.bucket, obj.object_name)
                    n += 1
            return n

        return await self._call(_delete)

    async def presigned_get_url(self, key: str, expires_s: int, *, filename: str) -> str:
        disposition = f'attachment; filename="{filename}"'
        return await self._call(
            lambda: self._client.presigned_get_object(
                self.bucket,
                key,
                expires=timedelta(seconds=expires_s),
                response_headers={"response-content-disposition": disposition},
            )
        )

    async def ping(self) -> None:
        await self._call(lambda: self._client.bucket_exists(self.bucket))

    async def _call[T](self, fn: Callable[[], T]) -> T:
        try:
            return await asyncio.to_thread(fn)
        except S3Error as exc:
            if exc.code in ("NoSuchKey", "NoSuchObject"):
                raise NotFound("Object not found.") from exc
            raise StoreUnavailable(f"Object store error: {exc.code}") from exc
        except OSError as exc:
            raise StoreUnavailable(f"Object store unreachable: {type(exc).__name__}") from exc
