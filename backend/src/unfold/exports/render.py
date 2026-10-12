"""Guide / chat → print HTML (LLD §19). Rendered to PDF by `unfold.exports.pdf.PdfRenderer`.

The templates render the same structured content as the web reader. All repository-derived text is untrusted:
Jinja autoescaping is on, and the two markup helpers (`inline_markup`, `markdown_lite`) HTML-escape their input
*first* and only then insert a fixed, closed set of tags around already-escaped text — so no `<script>`, attribute
or event handler from the repository can survive. The produced HTML contains no `<script>` at all; the PDF renderer
injects the vendored Mermaid itself.
"""

from __future__ import annotations

import re
from datetime import datetime
from functools import cache
from html import escape
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

from unfold.domain.enums import ClaimStatus
from unfold.exports.models import DiagramExport, ExportChatMessage, ExportProject
from unfold.guide.models import GuideSection

TEMPLATES_DIR = Path(__file__).parent / "templates"

_INIT_DIRECTIVE = re.compile(r"%%\{.*?\}%%", re.S)  # an init directive can sit mid-line
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_EM = re.compile(r"(?<![\w*])\*(?=[^\s*])([^<>]+?)(?<=[^\s*])\*(?![\w*])")
_CITE = re.compile(r"\[(E\d+(?:\s*,\s*E\d+)*)\]")
_PLACEHOLDER = re.compile(r"\x00(\d+)\x00")

STATUS_LABELS: dict[str, str] = {
    ClaimStatus.VERIFIED: "Verified",
    ClaimStatus.STRONGLY_INFERRED: "Strongly inferred",
    ClaimStatus.INFERRED: "Inferred",
    ClaimStatus.UNSUPPORTED: "Unsupported",
    ClaimStatus.PROPOSED: "Proposed",
    ClaimStatus.UNKNOWN: "Unchecked",
}
STATUS_HELP: dict[str, str] = {
    ClaimStatus.VERIFIED: "Directly supported by the cited repository text.",
    ClaimStatus.STRONGLY_INFERRED: "Not stated verbatim, but the cited evidence strongly implies it.",
    ClaimStatus.INFERRED: "A reasonable reading of the code; treat as interpretation.",
    ClaimStatus.UNSUPPORTED: "Could not be supported by evidence; verify before relying on it.",
    ClaimStatus.PROPOSED: "An improvement suggestion, not a statement about the current code.",
}


def _clean(text: str | None) -> str:
    return _CONTROL.sub("", text or "")


def inline_markup(text: str | None) -> Markup:
    """Escape, then apply the guide's inline markup: **bold**, *em*, `code`, citations [E3] / [E3, E4]."""
    raw = _clean(text)
    spans: list[str] = []

    def stash(m: re.Match[str]) -> str:
        spans.append(f"<code>{escape(m.group(1), quote=True)}</code>")
        return f"\x00{len(spans) - 1}\x00"

    work = _CODE_SPAN.sub(stash, raw)
    work = escape(work, quote=True)
    work = _BOLD.sub(r"<strong>\1</strong>", work)
    work = _EM.sub(r"<em>\1</em>", work)

    def cite(m: re.Match[str]) -> str:
        labels = [x.strip() for x in m.group(1).split(",")]
        return "".join(f'<sup class="cite">{label}</sup>' for label in labels)

    work = _CITE.sub(cite, work)
    work = _PLACEHOLDER.sub(lambda m: spans[int(m.group(1))], work)
    return Markup(work)  # noqa: S704 - built only from escaped text and fixed tags


_FENCE = re.compile(r"^\s*(`{3,}|~{3,})\s*([\w+-]*)\s*$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_ORDERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_HEADING = re.compile(r"^\s*(#{1,6})\s+(.*)$")


def sanitize_mermaid(source: str) -> str:
    """Drop init/config directives and front-matter from untrusted Mermaid so nothing can reconfigure the renderer."""
    text = _clean(source).lstrip()[:20_000]  # the output is cut to this size anyway: bound the work first
    for _ in range(32):  # removing one thing can join the text around it; each pass shrinks it
        cleaned = _strip_directives(text)
        if cleaned == text:
            return text.strip()
        text = cleaned
    return ""  # still changing after 32 passes: not a diagram worth rendering


def _strip_directives(text: str) -> str:
    lines = text.lstrip().splitlines()
    if lines and lines[0].strip() == "---":
        end = next((i for i, ln in enumerate(lines[1:], start=1) if ln.strip() == "---"), None)
        lines = lines[end + 1 :] if end is not None else lines[1:]
    kept = "\n".join(ln for ln in lines if not ln.lstrip().startswith("%%"))
    return _INIT_DIRECTIVE.sub("", kept).replace("%%{", "")  # an unclosed opener goes too


def _mermaid_figure(source: str, caption: str | None = None) -> str:
    cap = f"<figcaption>{inline_markup(caption)}</figcaption>" if caption else ""
    return f'<figure class="diagram"><pre class="mermaid">{escape(source, quote=True)}</pre>{cap}</figure>'


def markdown_lite(text: str | None) -> Markup:
    """Chat answers: paragraphs, bullet/ordered lists, small headings, code fences (```mermaid → diagram), inline."""
    lines = _clean(text).replace("\r\n", "\n").split("\n")
    out: list[str] = []
    para: list[str] = []
    items: list[str] = []
    list_tag = ""

    def flush_para() -> None:
        if para:
            out.append(f"<p>{inline_markup(' '.join(p.strip() for p in para))}</p>")
            para.clear()

    def flush_list() -> None:
        nonlocal list_tag
        if items:
            lis = "".join(f"<li>{inline_markup(i)}</li>" for i in items)
            out.append(f"<{list_tag}>{lis}</{list_tag}>")
            items.clear()
        list_tag = ""

    i = 0
    while i < len(lines):
        line = lines[i]
        fence = _FENCE.match(line)
        if fence:
            flush_para()
            flush_list()
            marker, lang = fence.group(1), fence.group(2).lower()
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(marker):
                body.append(lines[i])
                i += 1
            i += 1  # closing fence (or EOF)
            code = "\n".join(body)
            if lang == "mermaid":
                out.append(_mermaid_figure(sanitize_mermaid(code)))
            else:
                cls = f' data-lang="{escape(lang, quote=True)}"' if lang else ""
                out.append(f'<pre class="code"{cls}><code>{escape(code, quote=True)}</code></pre>')
            continue
        bullet, ordered, heading = _BULLET.match(line), _ORDERED.match(line), _HEADING.match(line)
        if not line.strip():
            flush_para()
            flush_list()
        elif heading:
            flush_para()
            flush_list()
            out.append(f"<h4>{inline_markup(heading.group(2))}</h4>")
        elif bullet or ordered:
            flush_para()
            tag = "ul" if bullet else "ol"
            if list_tag and list_tag != tag:
                flush_list()
            list_tag = tag
            items.append((bullet or ordered).group(1))  # type: ignore[union-attr]
        elif items:
            items[-1] += " " + line.strip()
        else:
            para.append(line)
        i += 1
    flush_para()
    flush_list()
    return Markup("\n".join(out))  # noqa: S704 - built only from escaped text and fixed tags


def _short_sha(sha: str | None) -> str:
    return (sha or "")[:12]


def _date(value: datetime | None) -> str:
    return value.strftime("%d %b %Y") if value else ""


def _datetime(value: datetime | None) -> str:
    return value.strftime("%d %b %Y, %H:%M") if value else ""


@cache
def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["inline"] = inline_markup
    env.filters["md"] = markdown_lite
    env.filters["short_sha"] = _short_sha
    env.filters["date"] = _date
    env.filters["datetime"] = _datetime
    env.globals["status_label"] = lambda s: STATUS_LABELS.get(str(s), str(s))
    return env


def _label_key(label: str) -> tuple[int, str]:
    digits = re.sub(r"\D", "", label)
    return (int(digits) if digits else 1_000_000, label)


def _evidence_rows(section: GuideSection) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for label in sorted(section.evidence, key=_label_key):
        ref = section.evidence[label]
        path = str(ref.get("path") or "")
        lines = ref.get("lines")
        if not lines and ref.get("start_line") is not None:
            start, end = ref.get("start_line"), ref.get("end_line")
            lines = f"{start}–{end}" if end is not None and end != start else f"{start}"
        rows.append(
            {
                "label": label,
                "path": path,
                "lines": str(lines or ""),
                "chunk_id": str(ref.get("chunk_id") or ""),
            }
        )
    return rows


def _claim_rows(section: GuideSection) -> list[dict[str, Any]]:
    by_chunk = {str(v.get("chunk_id")): k for k, v in section.evidence.items() if v.get("chunk_id")}
    rows = []
    for c in section.claims:
        labels = sorted({by_chunk[e] for e in c.evidence_ids if e in by_chunk}, key=_label_key)
        rows.append(
            {
                "text": c.text,
                "status": str(c.status),
                "status_class": str(c.status).lower(),
                "evidence": labels,
                "note": c.note,
            }
        )
    return rows


def _code_caption(path: str | None, start: int | None, code: str) -> str:
    if not path:
        return ""
    if start is None:
        return path
    end = start + max(code.count("\n"), 0)
    return f"{path}:{start}–{end}" if end > start else f"{path}:{start}"


def render_guide_html(
    project: ExportProject, sections: list[GuideSection], diagrams: dict[str, DiagramExport]
) -> str:
    ordered = sorted(sections, key=lambda s: (s.position, s.key))
    views = []
    for s in ordered:
        blocks: list[dict[str, Any]] = []
        for b in s.content.blocks if s.content else []:
            data: dict[str, Any] = {"block": b, "type": b.type}
            if b.type == "diagram":
                d = diagrams.get(b.diagram_id)
                data["diagram"] = d
                data["caption"] = b.caption or (d.caption if d else None)
            elif b.type == "code":
                data["code_caption"] = _code_caption(b.path, b.start_line, b.code)
            blocks.append(data)
        views.append(
            {
                "section": s,
                "anchor": f"sec-{s.position}",
                "blocks": blocks,
                "claims": _claim_rows(s),
                "evidence": _evidence_rows(s),
            }
        )
    legend = [
        {"status": str(k), "status_class": str(k).lower(), "label": STATUS_LABELS[k], "help": v}
        for k, v in STATUS_HELP.items()
    ]
    return _env().get_template("guide.html.j2").render(project=project, sections=views, legend=legend)


def render_chat_html(
    project: ExportProject,
    chat_title: str | None,
    messages: list[ExportChatMessage],
    learning_notes: list[str] | None = None,
) -> str:
    ordered = sorted(enumerate(messages), key=lambda im: (im[1].created_at, im[0]))
    return (
        _env()
        .get_template("chat.html.j2")
        .render(
            project=project,
            chat_title=chat_title or "Project chat",
            messages=[m for _, m in ordered],
            learning_notes=learning_notes or [],
        )
    )
