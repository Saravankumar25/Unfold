"""Source acquisition for a project (LLD §6): GitHub tarball or uploaded ZIP → hardened extraction to a temp dir.

The caller owns `workdir` and must delete it (the orchestration `ingest` node does, in a finally block).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from unfold.core.config import Settings
from unfold.core.errors import ErrorCode, InputError
from unfold.ingestion.archive import ExtractionLimits, ExtractionReport, safe_extract
from unfold.ingestion.github import GitHubClient, parse_github_url
from unfold.ports import BlobStore


@dataclass(frozen=True)
class AcquiredSource:
    root: Path
    name: str
    commit_sha: str | None
    default_branch: str | None
    description: str | None
    report: ExtractionReport


async def acquire_github(settings: Settings, locator: str, workdir: Path) -> AcquiredSource:
    ref = parse_github_url(locator)
    client = GitHubClient(settings)
    try:
        fetched = await client.fetch(ref, workdir)
    finally:
        await client.aclose()
    report = safe_extract(fetched.archive_path, workdir / "src", ExtractionLimits.from_settings(settings))
    fetched.archive_path.unlink(missing_ok=True)
    return AcquiredSource(
        root=report.root,
        name=fetched.repo,
        commit_sha=fetched.commit_sha,
        default_branch=fetched.default_branch,
        description=fetched.description,
        report=report,
    )


async def acquire_zip(
    settings: Settings, objects: BlobStore, upload_key: str | None, name: str, workdir: Path
) -> AcquiredSource:
    if not upload_key:
        raise InputError("The uploaded archive is missing.", code=ErrorCode.ZIP_INVALID)
    archive = await objects.get_file(upload_key, workdir / "upload.zip")
    report = safe_extract(archive, workdir / "src", ExtractionLimits.from_settings(settings))
    archive.unlink(missing_ok=True)
    return AcquiredSource(
        root=report.root, name=name, commit_sha=None, default_branch=None, description=None, report=report
    )
