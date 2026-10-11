"""Post-upload inspection of a browser-direct ZIP (ADR 0014).

The presigned policy already bounds the size and pins the key and type at S3. This is the second look before a
job exists: the object is there, its real size is within the limit, and it starts like a ZIP. A body that fails
is deleted. The extraction limits (bombs, traversal, file counts) stay in the worker (`ingestion.archive`).
"""

from __future__ import annotations

from unfold.core.errors import ErrorCode, InputError
from unfold.ingestion.archive import ZIP_MAGIC
from unfold.ports import UploadStore


async def inspect_upload(store: UploadStore, key: str, max_bytes: int) -> int:
    """Validate the uploaded object and return its size; delete and raise `InputError` when it is not a ZIP."""
    info = await store.stat(key)
    if info is None:
        raise InputError("The upload did not arrive; upload the ZIP again.", code=ErrorCode.ZIP_INVALID)
    if info.size > max_bytes:
        await store.delete(key)
        raise InputError(
            f"The ZIP exceeds the {max_bytes // (1024 * 1024)} MB limit.",
            code=ErrorCode.INPUT_TOO_LARGE,
            http_status=413,
        )
    head = await store.get_range(key, 0, 4) if info.size > 0 else b""
    if not any(head.startswith(m) for m in ZIP_MAGIC):
        await store.delete(key)
        raise InputError("The upload is not a ZIP archive.", code=ErrorCode.ZIP_INVALID)
    return info.size
