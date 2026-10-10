"""Markdown export: structure, claim badges, and neutralisation of repository-derived markup."""

from __future__ import annotations

from datetime import UTC, datetime

from unfold.domain.enums import ClaimKind, ClaimStatus
from unfold.domain.models import ClaimRecord, EvidenceRef
from unfold.exports.markdown import render_chat_markdown, render_guide_markdown
from unfold.exports.models import DiagramExport, ExportChatMessage, ExportProject
from unfold.guide.models import (
    BulletsBlock,
    CodeBlock,
    DiagramBlock,
    GuideSection,
    HeadingBlock,
    ParagraphBlock,
    SectionContent,
    TableBlock,
)

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
PROJECT = ExportProject(name="demo <b>app</b>", source="https://github.com/o/r", generated_at=NOW)


def _section(**kw: object) -> GuideSection:
    base: dict[str, object] = {
        "key": "overview",
        "title": "Overview",
        "position": 0,
        "status": "ready",
        "content": SectionContent(
            summary="What it does.",
            blocks=[
                HeadingBlock(text="Entry <img src=x onerror=alert(1)>"),
                ParagraphBlock(text="It serves `GET /health` <script>alert(1)</script> [E1]."),
                BulletsBlock(items=["one", "two"], ordered=True),
                CodeBlock(language="py", code="x = '''\n```\nboom\n```'''", path="app/main.py", start_line=3),
                TableBlock(headers=["a|b", "c"], rows=[["1", "2|3"]]),
                DiagramBlock(diagram_id="d1", caption="Flow"),
            ],
        ),
        "claims": [
            ClaimRecord(
                text="`health` is served",
                kind=ClaimKind.PROJECT_FACT,
                status=ClaimStatus.VERIFIED,
                evidence_ids=["c1"],
            )
        ],
        "evidence": {"E1": {"chunk_id": "c1", "path": "app/main.py", "start_line": 3, "end_line": 9}},
    }
    base.update(kw)
    return GuideSection(**base)  # type: ignore[arg-type]


def test_guide_markdown_structure_and_escaping() -> None:
    mermaid = "%%{init: {'securityLevel':'loose'}}%%\nflowchart LR\n  A --> B"
    md = render_guide_markdown(
        PROJECT, [_section()], {"d1": DiagramExport(id="d1", title="Flow", mermaid=mermaid)}
    )
    assert md.startswith("# demo &lt;b>app&lt;/b>\n")
    assert "<script>" not in md and "<img" not in md  # inline HTML cannot survive in prose
    assert "1. one" in md and "2. two" in md
    assert "a\\|b" in md and "2\\|3" in md
    assert "securityLevel" not in md and "flowchart LR" in md  # mermaid directives stripped, body kept
    assert "- **E1** `app/main.py:3-9`" in md
    assert "- Verified: `health` is served" in md
    # the inner ``` of the code block must not close its fence: the fence is longer than any inner run
    fence_lines = [ln for ln in md.splitlines() if ln.startswith("````")]
    assert len(fence_lines) >= 2


def test_section_without_content_is_marked_not_dropped() -> None:
    md = render_guide_markdown(PROJECT, [_section(content=None, claims=[], evidence={})], {})
    assert "## Overview" in md and "could not be generated" in md


def test_chat_markdown_keeps_fences_and_neutralises_html() -> None:
    msgs = [
        ExportChatMessage(role="user", text="where? <script>x</script>", created_at=NOW),
        ExportChatMessage(
            role="assistant",
            text="See `a.py` <b>bold</b>\n\n```html\n<div>kept</div>\n```",
            created_at=NOW,
            evidence=[EvidenceRef(chunk_id="c", path="a.py", start_line=1, end_line=4)],
        ),
    ]
    md = render_chat_markdown(PROJECT, "Q&A", msgs)
    assert "<script>" not in md and "&lt;b>bold" in md
    assert (
        "<div>" not in md and "&lt;div>kept" in md
    )  # escaped everywhere: a fence tracker can be desynchronised
    assert "Evidence: `a.py:1-4`" in md


def test_crafted_fences_and_links_cannot_smuggle_markup() -> None:
    hostile = "~~~\n```\n~~~\n```\n<img src=x onerror=alert(1)>\n[click](javascript:alert(1))\n![t](http://evil/p.png)"
    md = render_chat_markdown(
        PROJECT, "t", [ExportChatMessage(role="assistant", text=hostile, created_at=NOW)]
    )
    assert "<img" not in md and "](javascript" not in md and "](http" not in md
    guide = render_guide_markdown(
        ExportProject(name="a\n# injected heading", generated_at=NOW),
        [_section(title="x\n## second heading")],
        {},
    )
    assert "\n# injected" not in guide and "\n## second heading" not in guide
