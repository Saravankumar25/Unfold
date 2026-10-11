"""E2E fixture tool (test tooling): the two states the UI cannot reach by itself.

Run from `backend/`:  `uv run python ../frontend/e2e/tool.py pin|unpin <project_id>`  or  `... expire <session_token>`

* `pin`    makes a finished ZIP project look like a GitHub analysis pinned to a commit, so the guide's compact
           citations link to `github.com/<owner>/<repo>/blob/<sha>/<path>#L..` (analysis itself needs no network).
* `unpin` undoes `pin`.
* `expire` moves a session's expiry into the past, as the passage of time would.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import UTC, datetime, timedelta

from e2e_env import e2e_settings
from unfold.core.ids import token_hash
from unfold.persistence.postgres.models import ProjectRow, SessionRow
from unfold.runtime import Runtime
from sqlalchemy import select

SHA = "0123456789abcdef0123456789abcdef01234567"


async def main(command: str, ref: str) -> None:
    rt = Runtime.create(e2e_settings(), db_pool_size=1)
    try:
        async with rt.db.transaction() as s:
            if command == "pin":
                project = await s.get(ProjectRow, uuid.UUID(ref))
                assert project is not None, "unknown project"
                project.input_type = "github"
                project.source_locator = "https://github.com/unfold-e2e/fastapi_rag"
                project.commit_sha = SHA
                project.default_branch = "main"
            elif command == "unpin":  # back to the ZIP project the analysis journey created (re-runs without a new analysis)
                project = await s.get(ProjectRow, uuid.UUID(ref))
                assert project is not None, "unknown project"
                project.input_type = "zip"
                project.source_locator = "fastapi_rag.zip"
                project.commit_sha = None
                project.default_branch = None
            elif command == "expire":
                row = (
                    await s.execute(select(SessionRow).where(SessionRow.token_hash == token_hash(ref)))
                ).scalar_one()
                row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
            else:
                raise SystemExit(f"unknown command {command!r}")
    finally:
        await rt.aclose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
