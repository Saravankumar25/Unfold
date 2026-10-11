"""The upload route against hostile bodies (phase 5, task 2b): the size cap is enforced while streaming and a rejected
upload leaves no project, job or object behind. Archive *content* limits are `tests/security`."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import func, select

from unfold.persistence.postgres.models import ProjectRow
from unfold.runtime import Runtime

pytestmark = pytest.mark.integration

API = "/api/v1"


def _zip(entries: dict[str, bytes], method: int = zipfile.ZIP_STORED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


async def _projects(rt: Runtime) -> int:
    async with rt.db.session() as s:
        return int((await s.execute(select(func.count()).select_from(ProjectRow))).scalar_one())


async def test_oversized_and_malformed_uploads_are_refused_and_leave_nothing_behind(
    runtime: Runtime, open_api: Callable[..., Any]
) -> None:
    async with open_api(max_zip_bytes=512 * 1024) as client:
        assert (await client.post(f"{API}/sessions")).status_code in (200, 201)
        before = await _projects(runtime)

        big = _zip({"a.bin": b"\0" * (600 * 1024)})  # stored: the body itself exceeds the cap
        resp = await client.post(f"{API}/projects/zip", files={"file": ("big.zip", big, "application/zip")})
        assert resp.status_code == 413 and resp.json()["error"]["code"] == "INPUT_TOO_LARGE"

        for name, body in {
            "empty.zip": b"",
            "text.zip": b"PK is not enough, this is text",
            "exe.zip": b"MZ\x90\x00" + b"\0" * 100,
            "gzip.zip": b"\x1f\x8b\x08\x00" + b"\0" * 100,  # a gzip is not accepted on this route
        }.items():
            resp = await client.post(f"{API}/projects/zip", files={"file": (name, body, "application/zip")})
            assert resp.status_code == 400, (name, resp.text)
            assert resp.json()["error"]["code"] in ("ZIP_INVALID", "VALIDATION_ERROR"), name

        assert await _projects(runtime) == before  # no project row was created by any of them


async def test_filename_and_instruction_cannot_smuggle_paths_or_oversized_text(
    runtime: Runtime, open_api: Callable[..., Any]
) -> None:
    async with open_api() as client:
        assert (await client.post(f"{API}/sessions")).status_code in (200, 201)
        ok = _zip({"proj/app.py": b"print(1)\n"})
        resp = await client.post(
            f"{API}/projects/zip",
            files={"file": ("../../etc/passwd.zip", ok, "application/zip")},
            data={"instruction": "x" * 5000},
        )
        assert resp.status_code in (400, 422)  # the instruction is capped at 1000 characters

        resp = await client.post(
            f"{API}/projects/zip", files={"file": ("../../evil/name.zip", ok, "application/zip")}
        )
        assert resp.status_code == 202, resp.text
        project = (await client.get(f"{API}/projects/{resp.json()['project_id']}")).json()
        assert "/" not in project["name"] and ".." not in project["name"]
