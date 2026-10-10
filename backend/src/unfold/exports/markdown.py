"""Guide / chat -> Markdown (LLD §19). The same structured content as the PDF, as one portable text file.

Repository-derived text is untrusted and a Markdown viewer may render inline HTML, so prose has `<` and `&`
neutralised and code is placed in fences longer than any backtick run inside it. Mermaid goes through the same
`sanitize_mermaid` as the PDF.
"""

from __future__ import annotations

import re

from unfold.exports.models import DiagramExport, ExportChatMessage, ExportProject
from unfold.exports.render import STATUS_LABELS, sanitize_mermaid
from unfold.guide.models import GuideSection

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BACKTICKS = re.compile(r"`+")


def _prose(text: str | None) -> str:
    return _CONTROL.sub("", text or "").replace("&", "&amp;").replace("<", "&lt;")


def _markdown_text(text: str) -> str:
    """Model-written Markdown: keep its fences intact, neutralise inline HTML everywhere else."""
    out: list[str] = []
    in_fence = False
    for line in _CONTROL.sub("", text).splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            out.append(line)
        else:
            out.append(line if in_fence else line.replace("&", "&amp;").replace("<", "&lt;"))
    return "\n".join(out)


def _fence(body: str, language: str = "") -> str:
    longest = max((len(m.group(0)) for m in _BACKTICKS.finditer(body)), default=0)
    ticks = "`" * max(3, longest + 1)
    lang = re.sub(r"[^\w+#.-]", "", language)[:20]
    return f"{ticks}{lang}\n{_CONTROL.sub('', body)}\n{ticks}"


def _lines(start: object, end: object) -> str:
    if start is None:
        return ""
    return f":{start}-{end}" if end is not None and end != start else f":{start}"


def _block(b: object, diagrams: dict[str, DiagramExport]) -> str:
    t = getattr(b, "type", "")
    if t == "heading":
        return f"{'#' * (int(getattr(b, 'level', 2)) + 1)} {_prose(getattr(b, 'text', ''))}"
    if t == "paragraph":
        return _prose(getattr(b, "text", ""))
    if t == "bullets":
        items = getattr(b, "items", [])
        ordered = bool(getattr(b, "ordered", False))
        return "\n".join(
            f"{i + 1 if ordered else '-'}{'.' if ordered else ''} {_prose(x)}" for i, x in enumerate(items)
        )
    if t == "callout":
        return f"> **{str(getattr(b, 'tone', 'note')).capitalize()}:** {_prose(getattr(b, 'text', ''))}"
    if t == "code":
        caption = getattr(b, "path", None)
        head = f"*{_prose(caption)}{_lines(getattr(b, 'start_line', None), None)}*\n\n" if caption else ""
        return head + _fence(getattr(b, "code", ""), getattr(b, "language", ""))
    if t == "table":
        headers = [_prose(h).replace("|", "\\|") for h in getattr(b, "headers", [])]
        rows = getattr(b, "rows", [])
        out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
        out += ["| " + " | ".join(_prose(c).replace("|", "\\|") for c in row) + " |" for row in rows]
        return "\n".join(out)
    if t == "qa":
        return f"**Q: {_prose(getattr(b, 'question', ''))}**\n\n{_prose(getattr(b, 'answer', ''))}"
    if t == "glossary":
        return "\n".join(f"- **{_prose(i.term)}**: {_prose(i.definition)}" for i in getattr(b, "items", []))
    if t == "diagram":
        d = diagrams.get(getattr(b, "diagram_id", ""))
        if d is None:
            return ""
        caption = getattr(b, "caption", None) or d.caption or d.title
        return f"*{_prose(caption)}*\n\n{_fence(sanitize_mermaid(d.mermaid), 'mermaid')}"
    return ""


def _header(project: ExportProject, title: str) -> list[str]:
    meta = [f"- Generated: {project.generated_at:%Y-%m-%d %H:%M UTC}"]
    if project.source:
        meta.append(f"- Source: {_prose(project.source)}")
    if project.commit_sha:
        meta.append(f"- Commit: `{_prose(project.commit_sha)[:12]}`")
    if project.technical_level:
        meta.append(f"- Level: {_prose(project.technical_level)}")
    return [f"# {_prose(title)}", "", *meta, ""]


def render_guide_markdown(
    project: ExportProject, sections: list[GuideSection], diagrams: dict[str, DiagramExport]
) -> str:
    out = _header(project, project.name)
    for s in sorted(sections, key=lambda s: (s.position, s.key)):
        out += [f"## {_prose(s.title)}", ""]
        if s.content is None:
            out += ["*This section could not be generated.*", ""]
            continue
        out += [f"*{_prose(s.content.summary)}*", ""] if s.content.summary else []
        for block in s.content.blocks:
            rendered = _block(block, diagrams)
            if rendered:
                out += [rendered, ""]
        if s.evidence:
            out += ["### Evidence", ""]
            for label, ref in sorted(s.evidence.items(), key=lambda kv: (len(kv[0]), kv[0])):
                out.append(
                    f"- **{_prose(label)}** `{_prose(str(ref.get('path', '')))}"
                    f"{_lines(ref.get('start_line'), ref.get('end_line'))}`"
                )
            out.append("")
        graded = [c for c in s.claims if str(c.status) in STATUS_LABELS]
        if graded:
            out += ["### Claim check", ""]
            out += [f"- {STATUS_LABELS[str(c.status)]}: {_prose(c.text)}" for c in graded]
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_chat_markdown(
    project: ExportProject, chat_title: str | None, messages: list[ExportChatMessage]
) -> str:
    out = _header(project, f"{project.name}: {chat_title or 'Project chat'}")
    for m in sorted(enumerate(messages), key=lambda im: (im[1].created_at, im[0])):
        msg = m[1]
        who = "You" if msg.role == "user" else "Unfold"
        out += [f"### {who} ({msg.created_at:%H:%M})", "", _markdown_text(msg.text), ""]
        if msg.evidence:
            out.append(
                "Evidence: "
                + ", ".join(f"`{_prose(e.path)}{_lines(e.start_line, e.end_line)}`" for e in msg.evidence)
            )
            out.append("")
    return "\n".join(out).rstrip() + "\n"
