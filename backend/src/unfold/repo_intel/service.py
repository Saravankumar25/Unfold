"""Repository analysis driver (LLD §7): inventory → texts → analyzers → detection → `RepositoryAnalysis`.

CPU-bound and synchronous; callers run it in a worker thread. Reads files as data only: no repository code runs.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from unfold.core.logging import get_logger
from unfold.repo_intel.analyzers.base import LanguageAnalyzer, build_context
from unfold.repo_intel.analyzers.config import ConfigAnalyzer
from unfold.repo_intel.analyzers.python import PythonAnalyzer
from unfold.repo_intel.analyzers.treesitter import TreeSitterAnalyzer
from unfold.repo_intel.detect import detect
from unfold.repo_intel.inventory import build_inventory, read_texts
from unfold.repo_intel.models import FileAnalysis, Inventory, RepositoryAnalysis
from unfold.security.injection import scan
from unfold.security.secrets import redact

log = get_logger(__name__)

_README = re.compile(r"^readme(\.\w+)?$", re.IGNORECASE)


@dataclass(frozen=True)
class AnalyzedRepository:
    inventory: Inventory
    texts: dict[str, str]
    analysis: RepositoryAnalysis


def default_analyzers() -> Sequence[LanguageAnalyzer]:
    return (PythonAnalyzer(), TreeSitterAnalyzer(), ConfigAnalyzer())


def scan_repository(root: Path, *, max_single_file_bytes: int) -> tuple[Inventory, dict[str, str]]:
    """Inventory every file and decode the analyzable ones (tier <= 3)."""
    inventory = build_inventory(root, max_single_file_bytes)
    texts = read_texts(root, inventory, max_tier=3, max_single_file_bytes=max_single_file_bytes)
    return inventory, texts


def analyze_texts(
    project_id: str,
    inventory: Inventory,
    texts: dict[str, str],
    analyzers: Sequence[LanguageAnalyzer] | None = None,
) -> RepositoryAnalysis:
    """Run the analyzers over the decoded files, detect technologies and flag secret / injection signals."""
    ctx = build_context(inventory, texts)
    chosen = analyzers if analyzers is not None else default_analyzers()
    by_path = inventory.by_path()

    files: dict[str, FileAnalysis] = {}
    errors: dict[str, list[str]] = {}
    flagged: dict[str, list[str]] = {}
    for path in sorted(texts):
        meta = by_path[path]
        text = texts[path]
        for analyzer in chosen:
            if not analyzer.supports(meta):
                continue
            fa = analyzer.analyze(meta, text, ctx)
            files[path] = fa
            if fa.errors:
                errors[path] = list(fa.errors)
            break
        signals = [
            *(["secret"] if redact(text).redacted else []),
            *(["injection"] if scan(text, max_signals=1) else []),
        ]
        if signals:
            flagged[path] = signals

    log.info(
        "repo_intel.analyzed",
        project_id=project_id,
        files=len(inventory.files),
        analyzed=len(files),
        errors=len(errors),
        flagged=len(flagged),
    )
    return RepositoryAnalysis(
        project_id=project_id,
        files=files,
        detection=detect(files),
        errors=errors,
        description=_description(texts),
        flagged=flagged,
    )


def analyze_repository(
    project_id: str,
    root: Path,
    *,
    max_single_file_bytes: int,
    analyzers: Sequence[LanguageAnalyzer] | None = None,
) -> AnalyzedRepository:
    inventory, texts = scan_repository(root, max_single_file_bytes=max_single_file_bytes)
    analysis = analyze_texts(project_id, inventory, texts, analyzers)
    return AnalyzedRepository(inventory=inventory, texts=texts, analysis=analysis)


def _description(texts: dict[str, str]) -> str | None:
    """First prose paragraph of the root README (headings, badges, images and HTML skipped)."""
    readme = next((p for p in sorted(texts) if "/" not in p and _README.match(p)), None)
    if readme is None:
        return None
    paragraph: list[str] = []
    for raw in texts[readme].split("\n"):
        line = raw.strip()
        prose = bool(line) and not line.startswith(("#", "![", "[![", "<", "|", "```", "---", "==="))
        if prose:
            paragraph.append(line)
        elif paragraph:
            break
    text = " ".join(paragraph).strip()
    return text[:500] or None
