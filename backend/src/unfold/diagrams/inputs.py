"""Small input views for diagram builders (LLD §17). Kept separate from the shared `models.py` contract."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SubsystemView(BaseModel):
    """A subsystem as the architecture diagram needs it: a stable key, a title and its member files."""

    key: str
    title: str
    member_file_paths: list[str] = Field(default_factory=list)
