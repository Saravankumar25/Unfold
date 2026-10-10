"""Archive extraction runs in a worker thread so a big ZIP cannot stall the event loop (audit P2-f)."""

from __future__ import annotations

import asyncio
import io
import threading
import zipfile
from pathlib import Path
from typing import Any

import pytest

from unfold.core.config import Settings
from unfold.ingestion import service
from unfold.ingestion.archive import safe_extract


class _Blobs:
    bucket = "t"

    async def get_file(self, key: str, path: Path) -> Path:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("proj/app.py", "print('hi')\n")
        await asyncio.to_thread(path.write_bytes, buf.getvalue())
        return path


async def test_acquire_zip_extracts_off_the_event_loop_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    threads: list[int] = []

    def spy(*args: Any, **kwargs: Any) -> Any:
        threads.append(threading.get_ident())
        return safe_extract(*args, **kwargs)

    monkeypatch.setattr(service, "safe_extract", spy)
    src = await service.acquire_zip(Settings(), _Blobs(), "k", "proj", tmp_path)  # type: ignore[arg-type]
    assert (src.root / "app.py").exists()
    assert threads and threads[0] != threading.get_ident()
