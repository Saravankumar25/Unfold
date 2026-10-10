"""Deterministic project facts and claim verification (LLD §15, §16).

Planning lives in `guide.plan`, research in `guide.research`, writing in `guide.write`.
Verification is a separate, deterministic stage: a claim is `VERIFIED` only when it cites a real evidence label and
the identifiers it names occur in the cited text. The LLM never grades its own output.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from unfold.domain.enums import ClaimKind, ClaimStatus
from unfold.domain.models import ClaimRecord
from unfold.project_ir.schema import ProjectIR

_SLUG = re.compile(r"[^a-z0-9]+")
_IDENT = re.compile(r"`([^`\n]{2,80})`|\b([A-Za-z_][A-Za-z0-9_]{3,}(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\b")
_COMMON = frozenset(
    [
        "this",
        "that",
        "with",
        "from",
        "which",
        "their",
        "there",
        "where",
        "when",
        "what",
        "will",
        "been",
        "have",
        "has",
        "not",
        "are",
        "the",
        "and",
        "for",
        "its",
        "into",
        "uses",
        "used",
        "using",
        "also",
        "each",
        "only",
        "over",
        "then",
        "than",
        "these",
        "those",
        "them",
        "they",
        "such",
        "more",
        "most",
        "some",
        "any",
        "all",
        "can",
        "may",
        "should",
        "would",
        "could",
        "does",
        "done",
        "make",
        "made",
        "via",
        "per",
        "within",
        "without",
        "between",
        "across",
        "through",
        "after",
        "before",
        "while",
        "project",
        "code",
        "file",
        "files",
        "module",
        "service",
        "function",
        "class",
        "method",
        "request",
        "response",
        "data",
        "system",
    ]
)


def slugify(text: str) -> str:
    return _SLUG.sub("-", text.lower()).strip("-")[:60] or "section"


def _clip(text: str, limit: int = 160) -> str:
    """One line, bounded: repository-derived names must not smuggle newlines or paragraphs into a prompt."""
    return " ".join(text.split())[:limit]


def project_facts(ir: ProjectIR, subsystems: Sequence[tuple[str, list[str]]] = ()) -> list[str]:
    """Deterministic one-line facts (never LLM output) used in prompts and as the verifier's allow-list."""
    s = ir.stats
    facts = [
        f"Project name: {ir.project.name}",
        f"Files analysed: {s.get('files_indexed', len(ir.files))}; code files: {s.get('code_files', 0)}; "
        f"lines: {s.get('lines_total', 0)}",
    ]
    if ir.languages:
        facts.append(
            "Languages: " + ", ".join(f"{lang.name} ({lang.files} files)" for lang in ir.languages[:6])
        )
    if ir.project.categories:
        facts.append("Project type signals: " + ", ".join(ir.project.categories))
    if ir.frameworks:
        facts.append("Frameworks: " + ", ".join(sorted({f.name for f in ir.frameworks})[:12]))
    if ir.data_stores:
        facts.append(
            "Data stores: " + ", ".join(sorted({f"{d.name} ({d.category})" for d in ir.data_stores})[:10])
        )
    if ir.queues:
        facts.append("Queues: " + ", ".join(sorted({q.name for q in ir.queues})))
    if ir.endpoints:
        sample = ", ".join(f"{e.method} {e.route}" for e in ir.endpoints[:8])
        facts.append(f"HTTP endpoints: {len(ir.endpoints)} (e.g. {sample})")
    if ir.schemas:
        facts.append("Data models: " + ", ".join(sorted({sc.name for sc in ir.schemas})[:12]))
    if ir.services:
        facts.append("Runnable services: " + ", ".join(sorted({sv.name for sv in ir.services})[:10]))
    if ir.ai_components:
        facts.append(
            "AI components: "
            + ", ".join(sorted({f"{a.name} ({a.component_kind})" for a in ir.ai_components})[:10])
        )
    for title, members in subsystems[:8]:
        facts.append(f"Subsystem '{title}': {len(members)} files, e.g. {', '.join(members[:3])}")
    return [_clip(f) for f in facts]


# --- verification -------------------------------------------------------------------------------------------


def _identifiers(text: str) -> set[str]:
    out: set[str] = set()
    for m in _IDENT.finditer(text):
        token = (m.group(1) or m.group(2) or "").strip()
        for part in re.split(r"[\s(),:;/]+", token):
            part = part.strip("`'\".")
            if (
                len(part) >= 4
                and part.lower() not in _COMMON
                and ("_" in part or "." in part or any(c.isupper() for c in part[1:]) or m.group(1))
            ):
                out.add(part.lower())
    return out


def _contains_word(haystack: str, needle: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(needle)}(?![A-Za-z0-9_])", haystack) is not None


def grade_claims(
    claims: Sequence[ClaimRecord], chunk_texts: Mapping[str, str]
) -> tuple[list[ClaimRecord], dict[str, object]]:
    """Grade every claim against the stored text of the chunks it cites. Deterministic; no model call."""
    records: list[ClaimRecord] = []
    for claim in claims:
        chunk_ids = [c for c in claim.evidence_ids if c in chunk_texts]
        note: str | None = None
        if claim.kind is ClaimKind.IMPROVEMENT:
            status, confidence = ClaimStatus.PROPOSED, 0.0
            note = "A suggestion, not a statement about the project."
        elif not chunk_ids:
            if claim.kind is ClaimKind.INFERENCE:
                status, confidence, note = ClaimStatus.INFERRED, 0.2, "Reasoned, uncited."
            else:
                status, confidence, note = ClaimStatus.UNSUPPORTED, 0.0, "No evidence label cited."
        elif claim.kind is ClaimKind.INFERENCE:
            status, confidence = ClaimStatus.STRONGLY_INFERRED, 0.6
        else:
            haystack = " ".join(chunk_texts[c] for c in chunk_ids).lower()
            names = _identifiers(claim.text)
            grounded = {n for n in names if _contains_word(haystack, n)}
            ratio = len(grounded) / len(names) if names else 0.0
            if not names:
                # Nothing checkable (no code-like names): the citation alone does not make it verified.
                status, confidence = ClaimStatus.STRONGLY_INFERRED, 0.5
                note = "The claim names nothing that can be checked against the evidence."
            elif ratio >= 0.6:
                status, confidence = ClaimStatus.VERIFIED, round(0.6 + 0.4 * ratio, 2)
            else:
                status, confidence = ClaimStatus.INFERRED, round(0.3 * ratio + 0.1, 2)
                note = "Names in the claim were not found in the cited evidence: " + ", ".join(
                    sorted(names - grounded)[:4]
                )
        records.append(
            claim.model_copy(
                update={"status": status, "evidence_ids": chunk_ids, "confidence": confidence, "note": note}
            )
        )
    return records, claim_quality(records)


def claim_quality(records: Sequence[ClaimRecord]) -> dict[str, object]:
    """Counts by status of already graded claims (what `GuideSectionRow.quality_scores` stores)."""
    counts = {s: sum(1 for r in records if r.status is s) for s in ClaimStatus}
    total = len(records)
    checked = total - counts[ClaimStatus.PROPOSED]
    return {
        "claims": total,
        "verified": counts[ClaimStatus.VERIFIED],
        "strongly_inferred": counts[ClaimStatus.STRONGLY_INFERRED],
        "inferred": counts[ClaimStatus.INFERRED],
        "unsupported": counts[ClaimStatus.UNSUPPORTED],
        "proposed": counts[ClaimStatus.PROPOSED],
        "grounded_ratio": round(counts[ClaimStatus.VERIFIED] / checked, 2) if checked else None,
    }
