"""Per-chapter input hash (investigation engine §3.8): equal hash means the stored chapter is what a rerun would
produce, so research, writing and verification are skipped. Chapter `depends_on` is deliberately not an input:
it orders chapters and links readers, it never triggers regeneration."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from unfold.guide.critique import CRITIQUE_PROMPT_VERSION
from unfold.guide.models import PLANNER_VERSION, ChapterPlan, ResearchBrief
from unfold.guide.rewrite import REWRITE_PROMPT_VERSION
from unfold.guide.write import WRITER_PROMPT_VERSION


def evidence_ids(brief: ResearchBrief) -> list[str]:
    return sorted({i for f in brief.findings for i in f.evidence})


def chapter_input_hash(
    spec: ChapterPlan,
    brief: ResearchBrief,
    content_hashes: Mapping[str, str],
    *,
    technical_level: str,
    instruction: str | None,
    revision_rounds: int,
) -> str:
    """`content_hashes`: chunk id -> `Chunk.content_hash`, for at least `evidence_ids(brief)`."""
    payload = {
        "plan": spec.model_dump(mode="json", exclude={"depends_on"}),  # ordering only, never an input
        "evidence": [[i, content_hashes.get(i, "")] for i in evidence_ids(brief)],
        "versions": [PLANNER_VERSION, WRITER_PROMPT_VERSION, CRITIQUE_PROMPT_VERSION, REWRITE_PROMPT_VERSION],
        "level": technical_level,
        "instruction": instruction or "",
        "revision_rounds": revision_rounds,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
