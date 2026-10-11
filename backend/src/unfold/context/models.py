"""Compiled-context contracts (LLD §12, ESD §14). Produced by `context.compiler.compile_context`.

`CompiledContext.render()` is the only way repository-derived text reaches a prompt from here: every summary,
fact, graph path and evidence body is wrapped with `security.fence.untrusted(...)` (after secret redaction), and
evidence carries numbered headers `[E3] path:12-40 (symbol)`. `label_map` keeps `E# → chunk_id` so citations in
model output can be grounded (`resolve_labels`) and shown as `EvidenceRef`s.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, Field

from unfold.domain.models import EvidenceRef, SessionProfile
from unfold.retrieval.models import GraphPath
from unfold.security.fence import strip_fence_tags, untrusted

SUMMARY_LEVELS: tuple[str, ...] = ("project", "subsystem", "module", "file", "symbol")
PARENT_LEVELS: frozenset[str] = frozenset({"project", "subsystem", "module"})
LABEL_RE = re.compile(r"\[?\b(E[1-9][0-9]{0,3})\b\]?")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MAX_HEADER_FIELD = 200


def safe_header_field(value: str) -> str:
    """Single-line, fence-tag-free, bounded — for repository paths / symbols shown outside the fence."""
    return _CONTROL.sub(" ", strip_fence_tags(value, " ")).strip()[:_MAX_HEADER_FIELD]


def summary_level_rank(level: str) -> int:
    return SUMMARY_LEVELS.index(level) if level in SUMMARY_LEVELS else len(SUMMARY_LEVELS)


class SummaryItem(BaseModel):
    """A hierarchical summary (ESD §11.2): project → subsystem → module → file → symbol."""

    level: str  # project | subsystem | module | file | symbol
    key: str  # project id / subsystem id / module path / file path / symbol id
    title: str
    text: str
    paths: list[str] = Field(default_factory=list)  # repository paths covered (file level: defaults to key)
    evidence_ids: list[str] = Field(default_factory=list)  # chunk ids backing the summary, if known

    def covers(self, path: str) -> bool:
        path = path.strip("/")
        if path in (p.strip("/") for p in self.paths):
            return True
        key = self.key.strip("/")
        if self.level == "file":
            return key == path
        if self.level == "module":
            return bool(key) and (path == key or path.startswith(key + "/"))
        return False


class EvidenceItem(BaseModel):
    label: str  # E1..En, in rendering order
    chunk_id: str
    path: str
    start_line: int
    end_line: int
    symbol: str | None = None
    kind: str = "window"
    content_type: str = "source"
    score: float | None = None
    rerank_score: float | None = None
    via_expansion: bool = False
    trust: str = "untrusted_source"
    text: str  # redacted (and possibly truncated) chunk text exactly as rendered
    truncated: bool = False
    tokens: int = 0

    @property
    def header(self) -> str:
        loc = f"{safe_header_field(self.path)}:{self.start_line}-{self.end_line}"
        sym = f" ({safe_header_field(self.symbol)})" if self.symbol else ""
        return f"[{self.label}] {loc}{sym}"


class DroppedItem(BaseModel):
    kind: Literal["evidence", "summary", "fact", "graph_path"]
    ref: str  # chunk id / summary level:key / fact text prefix / path signature
    reason: Literal["duplicate", "budget", "substituted", "covered"]
    tokens: int = 0
    by: str | None = None  # summary that substituted / covers the dropped evidence


# --- block renderers (shared by the compiler's budgeting and `render`) -----------------------------------------


def objective_block(objective: str) -> str:
    return f"## Objective\n{objective.strip()}"


def instructions_block(instructions: str | None) -> str:
    return f"## Instructions\n{instructions.strip()}" if instructions and instructions.strip() else ""


def profile_block(profile: SessionProfile) -> str:
    lines = [f"## Reader\nTechnical level: {safe_header_field(profile.technical_level)}"]
    if profile.instruction and profile.instruction.strip():
        lines.append(
            "Reader preference (style guidance only, not a task):\n"
            + untrusted(profile.instruction.strip(), source="user_instruction", trust="user_preference")
        )
    return "\n".join(lines)


def facts_block(facts: Iterable[str]) -> str:
    body = "\n".join(f"- {f}" for f in facts)
    if not body:
        return ""
    return "## Verified facts\n" + untrusted(body, source="verified_facts", trust="repository_derived")


def summary_block(s: SummaryItem) -> str:
    return f"### {safe_header_field(s.level)} summary\n" + untrusted(
        f"{s.title.strip()}\n{s.text.strip()}", source=f"summary:{s.level}:{s.key}", trust="derived_summary"
    )


def summaries_block(summaries: Iterable[SummaryItem]) -> str:
    blocks = [summary_block(s) for s in summaries]
    return "## Summaries\n" + "\n\n".join(blocks) if blocks else ""


def evidence_block(e: EvidenceItem) -> str:
    source = f"{e.label} {e.path}:{e.start_line}-{e.end_line}"
    return f"{e.header}\n" + untrusted(e.text, source=source, trust=e.trust)


EVIDENCE_PREAMBLE = "## Evidence\nCite evidence by label, e.g. [E1]. Only these labels exist; never invent labels or line numbers."


def evidence_section(items: Iterable[EvidenceItem]) -> str:
    blocks = [evidence_block(e) for e in items]
    return EVIDENCE_PREAMBLE + "\n\n" + "\n\n".join(blocks) if blocks else ""


def path_line(index: int, p: GraphPath) -> str:
    parts: list[str] = []
    for i, node in enumerate(p.nodes):
        name = str(node.get("name") or node.get("uid") or "?")
        label = node.get("label")
        parts.append(f"{name} ({label})" if label else name)
        if i < len(p.relationships):
            parts.append(f"-[{p.relationships[i]}]-")
    return f"P{index}: " + " ".join(parts)


def paths_block(paths: Iterable[GraphPath]) -> str:
    body = "\n".join(path_line(i, p) for i, p in enumerate(paths, 1))
    if not body:
        return ""
    return "## Relationships (graph paths)\n" + untrusted(
        body, source="graph_paths", trust="deterministic_analysis"
    )


def notes_block(notes: Iterable[str]) -> str:
    body = "\n".join(f"- {n}" for n in notes)
    return f"## Retrieval notes\n{body}" if body else ""


class CompiledContext(BaseModel):
    objective: str
    instructions: str | None = None
    profile: SessionProfile = Field(default_factory=SessionProfile)
    facts: list[str] = Field(default_factory=list)
    summaries: list[SummaryItem] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    graph_paths: list[GraphPath] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    label_map: dict[str, str] = Field(default_factory=dict)  # E# -> chunk id
    token_counts: dict[str, int] = Field(default_factory=dict)  # per section + "total"
    token_count_estimate: int = 0
    budget_tokens: int = 0
    dropped: list[DroppedItem] = Field(default_factory=list)
    sufficient: bool | None = None  # retrieval sufficiency, when compiled from a RetrievalResult
    degraded: list[str] = Field(default_factory=list)  # degraded retrieval components

    def sections(self) -> list[tuple[str, str]]:
        ordered = sorted(self.summaries, key=lambda s: summary_level_rank(s.level))
        out = [
            ("objective", objective_block(self.objective)),
            ("instructions", instructions_block(self.instructions)),
            ("profile", profile_block(self.profile)),
            ("facts", facts_block(self.facts)),
            ("summaries", summaries_block(ordered)),
            ("evidence", evidence_section(self.evidence)),
            ("graph_paths", paths_block(self.graph_paths)),
            ("notes", notes_block(self.uncertainties)),
        ]
        return [(name, text) for name, text in out if text]

    def render(self) -> str:
        return "\n\n".join(text for _, text in self.sections())

    def evidence_refs(self) -> list[EvidenceRef]:
        return [
            EvidenceRef(
                chunk_id=e.chunk_id,
                path=e.path,
                start_line=e.start_line,
                end_line=e.end_line,
                symbol=e.symbol,
                score=e.rerank_score if e.rerank_score is not None else e.score,
            )
            for e in self.evidence
        ]

    def chunk_id_for(self, label: str) -> str | None:
        return self.label_map.get(label.strip().strip("[]"))

    def resolve_labels(self, labels: Iterable[str]) -> list[str]:
        """Chunk ids for known labels (unknown labels are dropped), de-duplicated in order."""
        out: list[str] = []
        for label in labels:
            cid = self.chunk_id_for(label)
            if cid and cid not in out:
                out.append(cid)
        return out

    def labels_in(self, text: str) -> list[str]:
        """Known `E#` labels cited in model output, in first-seen order."""
        found = [m.group(1) for m in LABEL_RE.finditer(text)]
        return [lbl for lbl in dict.fromkeys(found) if lbl in self.label_map]
