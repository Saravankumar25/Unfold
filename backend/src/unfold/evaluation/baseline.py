"""Run the real pipeline once and preserve its guide for scoring (task 3.1 baseline, reused by 3.9 for v2).

    python -m unfold.evaluation.baseline <name> --dir evals/fixtures/repos/fastapi_rag --out evals/baselines/pipeline-v1/<name>
    python -m unfold.evaluation.baseline <name> --github https://github.com/psf/requests/tree/<sha> --out ...

Needs the data stores (`docker compose up -d`) and model keys in `.env`. The analysis runs in a real worker process
(`python -m unfold.worker.run analysis <job>`, re-run on exit 75 like the queue does); nothing is scripted. Writes
`guide.json`, `ir.json`, `chunks.json` (the inputs `unfold.evaluation.rubric` scores) and `run.json` (tokens per
purpose and model from the gateway usage log, wall seconds, attempts). The project is deleted afterwards.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import time
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from unfold.cleanup.service import cleanup_project
from unfold.core.config import get_settings
from unfold.core.ids import token_hash
from unfold.evaluation.rubric import summarize_ir
from unfold.guide.models import GuidePlan, GuideSection, SectionContent
from unfold.llm.factory import build_gateway
from unfold.orchestration.pipeline import RETRY_EXIT_CODE
from unfold.persistence.postgres.models import (
    AnalysisJobRow,
    ChunkRow,
    GuidePlanRow,
    GuideSectionRow,
    ProjectRow,
    SessionRow,
)
from unfold.project_ir.snapshot import load_ir
from unfold.runtime import Runtime

MAX_ATTEMPTS = 6
BACKEND = Path(__file__).resolve().parents[3]


def _zip_dir(root: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(root.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                zf.write(p, f"{root.name}/{p.relative_to(root).as_posix()}")
    return buf.getvalue()


async def _seed(rt: Runtime, name: str, github: str | None, folder: Path | None) -> tuple[str, str]:
    now = datetime.now(UTC)
    pid = uuid.uuid4()
    key = None
    if folder is not None:
        key = f"projects/{pid}/upload/source.zip"
        await rt.objects.put_bytes(key, _zip_dir(folder), "application/zip")
    async with rt.db.transaction() as s:
        session = SessionRow(
            token_hash=token_hash(uuid.uuid4().hex),
            created_at=now,
            last_active_at=now,
            expires_at=now + timedelta(hours=6),
            status="active",
        )
        s.add(session)
        await s.flush()
        s.add(
            ProjectRow(
                id=pid,
                session_id=session.id,
                input_type="github" if github else "zip",
                source_locator=github or f"{name}.zip",
                detected_name=name,
                technical_level="intermediate",
                analysis_version="baseline",
                upload_object_key=key,
                expires_at=now + timedelta(hours=6),
            )
        )
        await s.flush()
        job = AnalysisJobRow(
            project_id=pid, status="queued", current_stage="RECEIVED", stage_progress={}, attempt=0
        )
        s.add(job)
        await s.flush()
        return str(pid), str(job.id)


def _write(out: Path, files: dict[str, str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")


async def _run_worker(job_id: str) -> tuple[int, float]:
    """Run the worker like the queue would; returns (attempts, wall seconds across attempts)."""
    started = time.monotonic()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "unfold.worker.run", "analysis", job_id, cwd=BACKEND
        )
        if await proc.wait() != RETRY_EXIT_CODE:
            return attempt, round(time.monotonic() - started, 1)
        await asyncio.sleep(30)  # the queue defers a retry; give per-minute quotas time to recover
    return MAX_ATTEMPTS, round(time.monotonic() - started, 1)


async def _export(rt: Runtime, pid: str, job_id: str, out: Path, args: argparse.Namespace) -> dict[str, Any]:
    puid = uuid.UUID(pid)
    async with rt.db.session() as s:
        job = await s.get(AnalysisJobRow, uuid.UUID(job_id))
        project = await s.get(ProjectRow, puid)
        plan_row = await s.get(GuidePlanRow, puid)
        rows = (
            (
                await s.execute(
                    select(GuideSectionRow)
                    .where(GuideSectionRow.project_id == puid)
                    .order_by(GuideSectionRow.position)
                )
            )
            .scalars()
            .all()
        )
    assert job is not None
    assert project is not None
    kinds = {x.key: x for x in GuidePlan.model_validate(plan_row.plan).sections} if plan_row else {}
    sections = [
        GuideSection(
            key=r.section_key,
            title=r.title,
            position=r.position,
            status=r.status,
            kind=kinds[r.section_key].kind if r.section_key in kinds else "core",
            target_minutes=kinds[r.section_key].target_minutes if r.section_key in kinds else 0.0,
            content=SectionContent.model_validate(r.content) if r.content else None,
            claims=r.claims or [],
            evidence=r.evidence_summary or {},
            quality=r.quality_scores or {},
        )
        for r in rows
    ]
    wanted = sorted({str(e["chunk_id"]) for s in sections for e in s.evidence.values()})
    async with rt.db.session() as s:
        chunk_rows = (
            (await s.execute(select(ChunkRow).where(ChunkRow.id.in_(wanted)))).scalars().all()
            if wanted
            else []
        )
    chunks = {
        c.id: {"path": c.path, "start_line": c.start_line, "end_line": c.end_line, "text": c.text}
        for c in chunk_rows
    }
    if not project.ir_object_key:
        raise RuntimeError(
            f"job ended at {job.current_stage} ({job.error_code}); no IR snapshot to summarise"
        )
    ir = summarize_ir(await load_ir(rt.objects, project.ir_object_key))
    usage = await build_gateway(rt.settings, rt.redis).usage(job_id)

    files = {
        "guide.json": json.dumps({"sections": [s.model_dump(mode="json") for s in sections]}, indent=1)
        + "\n",
        "ir.json": ir.model_dump_json(indent=1) + "\n",
        "chunks.json": json.dumps(chunks, indent=1) + "\n",
    }
    cost = None
    if args.usd_per_mtok_in is not None and args.usd_per_mtok_out is not None:
        cost = round(
            (usage["input_tokens"] * args.usd_per_mtok_in + usage["output_tokens"] * args.usd_per_mtok_out)
            / 1e6,
            4,
        )
    run = {
        "name": args.name,
        "source": args.github or f"evals/fixtures/repos/{args.name}",
        "commit_sha": project.commit_sha,
        "date": datetime.now(UTC).date().isoformat(),
        "job": {"status": job.status, "stage": job.current_stage, "error_code": job.error_code},
        "attempts": args.attempts,
        "wall_seconds": args.wall_seconds,
        "tokens": {
            "input": usage["input_tokens"],
            "output": usage["output_tokens"],
            "calls": usage["calls"],
            "by_purpose": usage["by_purpose"],
            "by_model": usage["by_model"],
        },
        "retries": usage["retries"],
        "rate_limited": usage["rate_limited"],
        "cost_usd_estimate": cost,
        "cost_note": None
        if cost is not None
        else "not measured: no price table in the repo; free-tier keys bill $0",
    }
    files["run.json"] = json.dumps(run, indent=1, default=str) + "\n"
    await asyncio.to_thread(_write, out, files)
    return run


async def main_async(args: argparse.Namespace) -> int:
    settings = get_settings()
    rt = Runtime.create(settings, db_pool_size=4)
    pid = ""
    try:
        await rt.objects.ensure_bucket()
        folder = Path(args.dir) if args.dir else None
        pid, job_id = await _seed(rt, args.name, args.github, folder)
        args.attempts, args.wall_seconds = await _run_worker(job_id)
        run = await _export(rt, pid, job_id, Path(args.out), args)
        print(json.dumps({k: run[k] for k in ("name", "commit_sha", "job", "attempts", "wall_seconds")}))  # noqa: T201
        return 0 if run["job"]["status"] == "succeeded" else 1
    finally:
        if pid:
            await cleanup_project(rt, pid)
        await rt.aclose()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="unfold.evaluation.baseline")
    p.add_argument("name")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--dir", help="fixture repository directory (zipped like an upload)")
    src.add_argument("--github", help="https://github.com/<owner>/<repo>/tree/<commit-sha>")
    p.add_argument("--out", required=True)
    p.add_argument("--usd-per-mtok-in", type=float)
    p.add_argument("--usd-per-mtok-out", type=float)
    return asyncio.run(main_async(p.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
