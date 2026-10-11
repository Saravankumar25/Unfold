"""Export bookkeeping that needs no renderer: the AWS control handlers record an abandoned export with it, and
their image carries neither Jinja nor Chromium."""

from __future__ import annotations

from unfold.core.errors import NotFound
from unfold.domain.enums import ArtifactStatus
from unfold.domain.records import ArtifactRec
from unfold.runtime import Runtime


async def mark_failed(rt: Runtime, export_id: str, message: str) -> None:
    def fail(art: ArtifactRec) -> bool:
        if art.status == ArtifactStatus.READY.value:
            return False
        art.status = ArtifactStatus.FAILED.value
        art.error = message[:500]
        return True

    try:
        await rt.control.update_artifact(export_id, fail)
    except NotFound:
        return  # the export is gone (expired or its project deleted)
