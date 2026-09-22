"""Application-owned BM25 retrieval over chunks (LLD §10, ESD §4.4, AI infra §13) using `bm25s`.

One index per project, serialized to a single blob (zip of the bm25s save directory + chunk ids) and stored at
`projects/{id}/lexical/{version}.bin`. Readers load blobs through `LexicalIndexCache` (in-process LRU of 8).
"""

from __future__ import annotations

import asyncio
import io
import json
import tempfile
import zipfile
from collections import OrderedDict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import bm25s
import numpy as np

from unfold.core.logging import get_logger
from unfold.indexing.errors import LexicalIndexError
from unfold.indexing.models import CHUNKER_VERSION, Chunk
from unfold.indexing.tokenize import tokenize, tokenize_query
from unfold.persistence.object_store import ObjectStore

log = get_logger(__name__)

FORMAT_VERSION = "1"
_IDS = "chunk_ids.json"
_META = "meta.json"
_BM25_DIR = "bm25/"


def lexical_key(project_id: str, version: str) -> str:
    return f"projects/{project_id}/lexical/{version}.bin"


class LexicalArtifact:
    """A built / loaded BM25 index plus the chunk id of every document (same order)."""

    def __init__(
        self, retriever: Any | None, chunk_ids: list[str], meta: dict[str, Any] | None = None
    ) -> None:
        self.retriever = retriever  # bm25s.BM25, or None for an empty corpus
        self.chunk_ids = chunk_ids
        self.meta = meta or {}

    def __len__(self) -> int:
        return len(self.chunk_ids)

    def search(self, query: str, k: int = 40) -> list[tuple[str, float]]:
        if self.retriever is None or not self.chunk_ids or k <= 0:
            return []
        tokens = [t for t in tokenize_query(query) if t in self.retriever.vocab_dict]
        if not tokens:
            return []
        scores = np.asarray(self.retriever.get_scores(tokens), dtype=np.float64)
        positive = np.flatnonzero(scores > 0)
        if positive.size == 0:
            return []
        # Highest score first; ties broken by chunk id for determinism.
        ranked = sorted(positive.tolist(), key=lambda i: (-scores[i], self.chunk_ids[i]))[:k]
        return [(self.chunk_ids[i], float(scores[i])) for i in ranked]

    # --- serialization ---------------------------------------------------------------------------------------

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(_IDS, json.dumps(self.chunk_ids))
            zf.writestr(_META, json.dumps({**self.meta, "format_version": FORMAT_VERSION}))
            if self.retriever is not None:
                with tempfile.TemporaryDirectory(prefix="unfold-bm25-") as tmp:
                    self.retriever.save(tmp, show_progress=False)
                    for p in sorted(Path(tmp).iterdir()):
                        if p.is_file():
                            zf.write(p, _BM25_DIR + p.name)
        return buf.getvalue()

    @classmethod
    def from_bytes(cls, data: bytes) -> LexicalArtifact:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = zf.namelist()
                chunk_ids = [str(x) for x in json.loads(zf.read(_IDS))]
                meta = json.loads(zf.read(_META)) if _META in names else {}
                bm25_files = [n for n in names if n.startswith(_BM25_DIR) and "/" not in n[len(_BM25_DIR) :]]
                if not bm25_files:
                    return cls(None, chunk_ids, meta)
                with tempfile.TemporaryDirectory(prefix="unfold-bm25-") as tmp:
                    for name in bm25_files:
                        (Path(tmp) / name[len(_BM25_DIR) :]).write_bytes(zf.read(name))
                    retriever = bm25s.BM25.load(tmp, mmap=False, load_corpus=False, show_progress=False)
        except (zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
            raise LexicalIndexError(f"Lexical index artifact is unreadable: {type(exc).__name__}") from exc
        return cls(retriever, chunk_ids, meta)

    async def save(self, objects: ObjectStore, project_id: str, version: str) -> str:
        key = lexical_key(project_id, version)
        data = await asyncio.to_thread(self.to_bytes)
        await objects.put_bytes(key, data, content_type="application/zip")
        log.info("lexical.saved", project_id=project_id, key=key, docs=len(self.chunk_ids), bytes=len(data))
        return key


class BM25Index:
    """Builder for `LexicalArtifact`s (tokenizer: `indexing.tokenize`)."""

    @staticmethod
    def build(chunks: Sequence[Chunk]) -> LexicalArtifact:
        chunk_ids = [c.id for c in chunks]
        meta = {"chunker_version": CHUNKER_VERSION, "docs": len(chunk_ids)}
        if not chunks:
            return LexicalArtifact(None, [], meta)
        corpus = [tokenize(c.text) for c in chunks]
        if not any(corpus):
            return LexicalArtifact(None, chunk_ids, meta)
        retriever = bm25s.BM25()
        retriever.index(corpus, show_progress=False)
        return LexicalArtifact(retriever, chunk_ids, meta)

    @staticmethod
    async def build_async(chunks: Sequence[Chunk]) -> LexicalArtifact:
        return await asyncio.to_thread(BM25Index.build, list(chunks))


class LexicalIndexCache:
    """In-process LRU of loaded artifacts keyed by object key (8 by default)."""

    def __init__(self, max_items: int = 8) -> None:
        self.max_items = max_items
        self._items: OrderedDict[str, LexicalArtifact] = OrderedDict()
        self._lock = asyncio.Lock()

    def __contains__(self, key: str) -> bool:
        return key in self._items

    def put(self, key: str, artifact: LexicalArtifact) -> None:
        self._items[key] = artifact
        self._items.move_to_end(key)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)

    def invalidate(self, key: str) -> None:
        self._items.pop(key, None)

    async def get(self, objects: ObjectStore, key: str) -> LexicalArtifact:
        hit = self._items.get(key)
        if hit is not None:
            self._items.move_to_end(key)
            return hit
        async with self._lock:
            hit = self._items.get(key)
            if hit is not None:
                return hit
            data = await objects.get_bytes(key)
            artifact = await asyncio.to_thread(LexicalArtifact.from_bytes, data)
            self.put(key, artifact)
            return artifact

    async def search(
        self, objects: ObjectStore, key: str, query: str, k: int = 40
    ) -> list[tuple[str, float]]:
        artifact = await self.get(objects, key)
        return artifact.search(query, k)
