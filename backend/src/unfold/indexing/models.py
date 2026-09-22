"""Chunk = the unit of retrieval and of evidence (LLD §10). Persisted in `chunks`, indexed in BM25 + Qdrant."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from unfold.domain.enums import ContentType

CHUNKER_VERSION = "1"


class Chunk(BaseModel):
    id: str  # unfold.core.ids.chunk_id(project_id, path, start, end, kind)
    project_id: str
    path: str
    start_line: int
    end_line: int
    kind: str  # function | method | class | endpoint | schema | config | docs_section | window | component
    symbol_id: str | None = None
    symbol_name: str | None = None
    language: str | None = None
    content_type: ContentType = ContentType.SOURCE
    importance: float = 0.0  # 0..1
    subsystem_id: str | None = None
    graph_node_ids: list[str] = Field(default_factory=list)
    trust: dict[str, Any] = Field(
        default_factory=dict
    )  # {"label": "untrusted_source"|"suspicious", "signals": [...]}
    text: str  # REDACTED text; first line is the indexed header `path :: qualname`
    content_hash: str

    @property
    def header(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}" + (
            f" ({self.symbol_name})" if self.symbol_name else ""
        )
