"""Versioned Project IR snapshots in the object store (LLD §8): `projects/{id}/ir/{version}.json.gz`.

Serialization is canonical (orjson, sorted keys, gzip with a zero mtime) so the same IR yields byte-identical
objects. (De)compression and parsing are CPU work and run in a worker thread. A small in-process LRU keeps recently
loaded snapshots because the API / chat read the IR on many requests; snapshot keys are versioned and snapshots are
immutable, so cached entries never go stale. Cached `ProjectIR` instances are shared: callers must treat them as
read-only (use `model_copy(deep=True)` before mutating).
"""

from __future__ import annotations

import asyncio
import gzip
import threading
from collections import OrderedDict
from typing import TYPE_CHECKING

import orjson
from pydantic import ValidationError

from unfold.core.errors import ErrorCode, UnfoldError
from unfold.core.logging import get_logger
from unfold.project_ir.schema import IR_SCHEMA_VERSION, ProjectIR

if TYPE_CHECKING:
    from unfold.persistence.object_store import ObjectStore

__all__ = [
    "IRCache",
    "clear_ir_cache",
    "decode_ir",
    "encode_ir",
    "evict_project",
    "ir_object_key",
    "load_ir",
    "save_ir",
]

log = get_logger(__name__)

CONTENT_TYPE = "application/gzip"
DEFAULT_CACHE_ENTRIES = 8


def ir_object_key(project_id: str, version: str) -> str:
    if not project_id or not version or "/" in project_id or "/" in version or ".." in version:
        raise UnfoldError(
            "Invalid IR snapshot key.",
            code=ErrorCode.VALIDATION_ERROR,
            detail={"project_id": project_id, "version": version},
        )
    return f"projects/{project_id}/ir/{version}.json.gz"


def encode_ir(ir: ProjectIR) -> bytes:
    """Canonical gzip(JSON) bytes for an IR (deterministic)."""
    raw = orjson.dumps(ir.model_dump(mode="json"), option=orjson.OPT_SORT_KEYS)
    return gzip.compress(raw, compresslevel=6, mtime=0)


def decode_ir(data: bytes, *, key: str = "") -> ProjectIR:
    try:
        payload = orjson.loads(gzip.decompress(data))
    except (OSError, EOFError, orjson.JSONDecodeError) as exc:
        raise UnfoldError("IR snapshot is corrupt.", detail={"key": key}) from exc
    if not isinstance(payload, dict):
        raise UnfoldError("IR snapshot is corrupt.", detail={"key": key})
    version = payload.get("schema_version")
    if version != IR_SCHEMA_VERSION:
        raise UnfoldError(
            "IR snapshot has an unsupported schema version.",
            detail={"key": key, "schema_version": version, "expected": IR_SCHEMA_VERSION},
        )
    try:
        return ProjectIR.model_validate(payload)
    except ValidationError as exc:
        raise UnfoldError("IR snapshot does not match the IR schema.", detail={"key": key}) from exc


class IRCache:
    """Thread-safe LRU of decoded snapshots keyed by (bucket, object key)."""

    def __init__(self, max_entries: int = DEFAULT_CACHE_ENTRIES) -> None:
        self.max_entries = max(0, max_entries)
        self._items: OrderedDict[tuple[str, str], ProjectIR] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, bucket: str, key: str) -> ProjectIR | None:
        with self._lock:
            ir = self._items.get((bucket, key))
            if ir is not None:
                self._items.move_to_end((bucket, key))
            return ir

    def put(self, bucket: str, key: str, ir: ProjectIR) -> None:
        if self.max_entries == 0:
            return
        with self._lock:
            self._items[(bucket, key)] = ir
            self._items.move_to_end((bucket, key))
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)

    def discard_prefix(self, bucket: str, prefix: str) -> None:
        with self._lock:
            for k in [k for k in self._items if k[0] == bucket and k[1].startswith(prefix)]:
                del self._items[k]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


_CACHE = IRCache()


def clear_ir_cache() -> None:
    _CACHE.clear()


def evict_project(objects: ObjectStore, project_id: str, *, cache: IRCache | None = None) -> None:
    """Drop a project's cached snapshots (call when the project is deleted / expires)."""
    (cache if cache is not None else _CACHE).discard_prefix(objects.bucket, f"projects/{project_id}/")


async def save_ir(objects: ObjectStore, ir: ProjectIR) -> str:
    """Persist `ir` under its versioned key and return the key.

    The cache is filled on load only: the caller keeps (and may still mutate) the instance it saved.
    """
    key = ir_object_key(ir.project.project_id, ir.analysis_version)
    data = await asyncio.to_thread(encode_ir, ir)
    await objects.put_bytes(key, data, content_type=CONTENT_TYPE)
    log.info("project_ir.saved", project_id=ir.project.project_id, key=key, bytes=len(data))
    return key


async def load_ir(objects: ObjectStore, key: str, *, cache: IRCache | None = None) -> ProjectIR:
    """Load a snapshot (LRU-cached). Raises `NotFound` when absent, `UnfoldError` when corrupt."""
    lru = cache if cache is not None else _CACHE
    hit = lru.get(objects.bucket, key)
    if hit is not None:
        return hit
    data = await objects.get_bytes(key)
    ir = await asyncio.to_thread(decode_ir, data, key=key)
    lru.put(objects.bucket, key, ir)
    log.debug("project_ir.loaded", key=key, bytes=len(data))
    return ir
