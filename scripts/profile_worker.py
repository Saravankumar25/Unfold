"""Profile one analysis run on the AWS profile (moto DynamoDB + S3, embedded dense, in-memory graph).

Records per-stage wall time and peak RSS, LLM calls, tokens and seconds per purpose and per chapter, retrieval and
flow-trace latency percentiles. The model is the deterministic `ScriptedLLM` (prompts are real, answers are canned:
tokens are not meaningful, prompt characters are) or the real Gemini gateway (`--llm gemini`, keys from `.env`).
Writes the report to <out>.json. Measurement only: nothing under backend/src is changed (functions are wrapped here).

    cd backend && uv run --with psutil python ../scripts/profile_worker.py fastapi_rag ../.profile/fastapi_rag.json
    cd backend && uv run --with psutil python ../scripts/profile_worker.py src/unfold out.json --cpus 4
    cd backend && uv run --with psutil python ../scripts/profile_worker.py cli_tool out.json --llm gemini
"""

# ruff: noqa: T201, E402, PT018, ASYNC240, S101
from __future__ import annotations

import argparse
import asyncio
import contextvars
import json
import math
import statistics
import sys
import threading
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aws_profile_guide_run as ap  # sets the moto environment and imports the pipeline
import boto3
from moto import mock_aws
from opentelemetry import trace
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider

import unfold.guide.research as research_mod
import unfold.orchestration.pipeline as pipeline_mod
import unfold.understand.architecture as architecture_mod
from unfold.core.config import Settings
from unfold.core.ids import new_session_token, token_hash
from unfold.domain.records import JobRec, ProjectRec
from unfold.evaluation.scripted import ScriptedLLM
from unfold.llm.factory import build_gateway
from unfold.llm.gateway import LLMGateway
from unfold.retrieval.hybrid import HybridRetriever
from unfold.runtime import Runtime

ROOT = Path(__file__).resolve().parents[1]
CHAPTER: contextvars.ContextVar[str] = contextvars.ContextVar("chapter", default="-")
STAGE = ["startup"]  # the pipeline stage running now (read by the RSS sampler)


def pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, round(q * (len(s) - 1)))]


def dist(xs: list[float]) -> dict[str, float]:
    return {
        "n": len(xs),
        "p50": round(pct(xs, 0.5), 4),
        "p95": round(pct(xs, 0.95), 4),
        "max": round(max(xs, default=0.0), 4),
        "mean": round(statistics.fmean(xs), 4) if xs else 0.0,
    }


class CountingDynamo:
    """Counts DynamoDB requests and estimates capacity units from payload size (same estimate as the AWS e2e test:
    writes 1 WRU per KB, x2 in a transaction; reads 0.5 RRU per 4 KB eventual, 1 consistent). Not a billing figure."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: dict[str, int] = {}
        self.wru = 0.0
        self.rru = 0.0

    @staticmethod
    def _kb(item: Any) -> float:
        return max(1.0, len(json.dumps(item, default=str)) / 1024)

    def __getattr__(self, name: str) -> Any:
        fn = getattr(self._inner, name)

        def call(**kw: Any) -> Any:
            self.calls[name] = self.calls.get(name, 0) + 1
            out = fn(**kw)
            if name == "put_item":
                self.wru += math.ceil(self._kb(kw["Item"]))
            elif name == "update_item":
                self.wru += math.ceil(self._kb(out.get("Attributes", {})))
            elif name == "transact_write_items":
                for t in kw["TransactItems"]:
                    self.wru += 2 * math.ceil(self._kb(next(iter(t.values())).get("Item", {})))
            elif name == "batch_write_item":
                for reqs in kw["RequestItems"].values():
                    for r in reqs:
                        self.wru += math.ceil(self._kb(r.get("PutRequest", {}).get("Item", {})))
            elif name in ("get_item", "query"):
                items = out.get("Item", {}) if name == "get_item" else out.get("Items", [])
                self.rru += math.ceil(self._kb(items) / 4) * (1.0 if kw.get("ConsistentRead") else 0.5)
            return out

        return call

    def summary(self) -> dict[str, Any]:
        return {
            "requests": dict(self.calls),
            "total": sum(self.calls.values()),
            "wru": round(self.wru),
            "rru": round(self.rru),
        }


class Spans(SpanProcessor):  # type: ignore[misc]
    """Stage durations and retrieval durations from the spans the code already emits."""

    def __init__(self) -> None:
        self.stages: dict[str, float] = {}
        self.retrieval: list[float] = []

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        if span.name.startswith("pipeline."):
            STAGE[0] = span.name.removeprefix("pipeline.")

    def on_end(self, span: Any) -> None:
        secs = (span.end_time - span.start_time) / 1e9
        if span.name.startswith("pipeline."):
            self.stages[span.name.removeprefix("pipeline.")] = round(secs, 3)
        elif span.name == "retrieval.retrieve":
            self.retrieval.append(secs)

    def shutdown(self) -> None: ...
    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def sampler(stop: threading.Event, peaks: dict[str, int], cpu: dict[str, float]) -> None:
    proc = psutil.Process()
    last = sum(proc.cpu_times()[:2])
    while not stop.is_set():
        rss = proc.memory_info().rss
        peaks[STAGE[0]] = max(peaks.get(STAGE[0], 0), rss)
        now = sum(proc.cpu_times()[:2])  # user + system seconds of this process, all threads
        cpu[STAGE[0]] = cpu.get(STAGE[0], 0.0) + now - last
        last = now
        stop.wait(0.02)


class Recorder:
    """Wraps the pipeline's own functions to attribute time and model calls to a chapter and a role."""

    def __init__(self) -> None:
        self.llm_calls: list[dict[str, Any]] = []
        self.tokens: list[dict[str, Any]] = []
        self.phase: list[tuple[str, str, float]] = []  # (role, chapter, seconds)
        self.trace_flow: list[float] = []
        self.retrieval_parts: list[dict[str, float]] = []

    def install(self) -> None:
        rec = self

        orig_research = research_mod.Investigator.research

        async def research(self_: Any, plan: Any, *a: Any, **k: Any) -> Any:
            tok = CHAPTER.set(plan.key)
            t = time.perf_counter()
            try:
                return await orig_research(self_, plan, *a, **k)
            finally:
                rec.phase.append(("investigator", plan.key, time.perf_counter() - t))
                CHAPTER.reset(tok)

        research_mod.Investigator.research = research  # type: ignore[method-assign]

        orig_write = pipeline_mod.write_chapter

        async def write(llm: Any, chunks: Any, spec: Any, *a: Any, **k: Any) -> Any:
            tok = CHAPTER.set(spec.key)
            t = time.perf_counter()
            try:
                return await orig_write(llm, chunks, spec, *a, **k)
            finally:
                rec.phase.append(("writer", spec.key, time.perf_counter() - t))
                CHAPTER.reset(tok)

        pipeline_mod.write_chapter = write  # type: ignore[assignment]

        orig_verify = pipeline_mod._Run._verify_one

        async def verify(self_: Any, spec: Any, *a: Any, **k: Any) -> Any:
            tok = CHAPTER.set(spec.key)
            t = time.perf_counter()
            try:
                return await orig_verify(self_, spec, *a, **k)
            finally:
                rec.phase.append(("reviser", spec.key, time.perf_counter() - t))
                CHAPTER.reset(tok)

        pipeline_mod._Run._verify_one = verify  # type: ignore[method-assign]

        orig_trace = research_mod.trace_flow

        def traced(*a: Any, **k: Any) -> Any:
            t = time.perf_counter()
            try:
                return orig_trace(*a, **k)
            finally:
                rec.trace_flow.append(time.perf_counter() - t)

        research_mod.trace_flow = traced  # type: ignore[assignment]
        architecture_mod.trace_flow = traced  # type: ignore[assignment]

        orig_retrieve = HybridRetriever.retrieve

        async def retrieve(self_: Any, plan: Any, **k: Any) -> Any:
            result = await orig_retrieve(self_, plan, **k)
            rec.retrieval_parts.append(dict(result.trace.latency_ms))
            return result

        HybridRetriever.retrieve = retrieve  # type: ignore[method-assign]

        orig_record = LLMGateway._record

        async def record(self_: Any, req: Any, **kw: Any) -> Any:
            u = kw["usage"]
            rec.tokens.append(
                {
                    "purpose": req.purpose,
                    "chapter": CHAPTER.get(),
                    "model": kw["model"],
                    "in": u.input_tokens,
                    "out": u.output_tokens,
                    "outcome": kw["outcome"],
                    "cached": kw.get("cached", False),
                    "retries": kw["st"].retries,
                    "rate_limited": kw["st"].rate_limited,
                }
            )
            return await orig_record(self_, req, **kw)

        LLMGateway._record = record  # type: ignore[method-assign]

    def wrap(self, inner: Any) -> Any:
        rec = self

        class Proxy:
            async def generate_structured(self, req: Any, schema: Any) -> Any:
                t = time.perf_counter()
                try:
                    return await inner.generate_structured(req, schema)
                finally:
                    chars = len(req.system) + sum(len(m.content) for m in req.messages)
                    rec.llm_calls.append(
                        {
                            "purpose": req.purpose,
                            "chapter": CHAPTER.get(),
                            "seconds": time.perf_counter() - t,
                            "prompt_chars": chars,
                        }
                    )

            async def generate(self, req: Any) -> Any:
                return await inner.generate(req)

            def stream(self, req: Any) -> Any:
                return inner.stream(req)

        return Proxy()

    def report(self) -> dict[str, Any]:
        by_purpose: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"calls": 0, "seconds": 0.0, "prompt_chars": 0, "in": 0, "out": 0, "lat": []}
        )
        for c in self.llm_calls:
            p = by_purpose[c["purpose"]]
            p["calls"] += 1
            p["seconds"] += c["seconds"]
            p["prompt_chars"] += c["prompt_chars"]
            p["lat"].append(c["seconds"])
        for t in self.tokens:
            p = by_purpose[t["purpose"]]
            p["in"] += t["in"]
            p["out"] += t["out"]
        purposes = {}
        for k, v in sorted(by_purpose.items()):
            lat = v.pop("lat")
            v["seconds"] = round(v["seconds"], 2)
            v["latency"] = dist(lat)
            purposes[k] = v
        chapters: dict[str, dict[str, Any]] = defaultdict(lambda: defaultdict(float))
        for role, ch, s in self.phase:
            chapters[ch][f"{role}_s"] += s
        for c in self.llm_calls:
            chapters[c["chapter"]]["llm_calls"] += 1
            chapters[c["chapter"]]["prompt_chars"] += c["prompt_chars"]
        for t in self.tokens:
            chapters[t["chapter"]]["tokens_in"] += t["in"]
            chapters[t["chapter"]]["tokens_out"] += t["out"]
        roles = {}
        for role in ("investigator", "writer", "reviser"):
            xs = [s for r, _, s in self.phase if r == role]
            roles[role] = {**dist(xs), "total_s": round(sum(xs), 2)}
        return {
            "by_purpose": purposes,
            "by_chapter": {k: {a: round(b, 2) for a, b in v.items()} for k, v in sorted(chapters.items())},
            "chapter_phase_seconds": roles,
            "trace_flow_seconds": dist(self.trace_flow),
            "retrieval_components_ms": {
                k: {a: round(b, 1) for a, b in dist([p[k] for p in self.retrieval_parts if k in p]).items()}
                for k in sorted({k for p in self.retrieval_parts for k in p})
            },
            "retried_calls": sum(1 for t in self.tokens if t["retries"]),
            "rate_limited_events": sum(t["rate_limited"] for t in self.tokens),
        }


async def main(target: str, out: Path, llm_kind: str, cpus: int) -> int:
    src = Path(target)
    fixture = src if src.is_absolute() or src.exists() else ROOT / "evals" / "fixtures" / "repos" / target
    fixture = fixture.resolve()
    if cpus:
        psutil.Process().cpu_affinity(list(range(cpus)))
    spans = Spans()
    provider = TracerProvider()
    provider.add_span_processor(spans)
    trace.set_tracer_provider(provider)
    rec = Recorder()
    rec.install()
    proc = psutil.Process()
    t_start = time.monotonic()
    with mock_aws():
        table, bucket = "unfold-prof", "unfold-prof-bucket"
        ap._create_table(boto3.client("dynamodb", region_name="us-east-1"), table)
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=bucket)
        queue = boto3.client("sqs", region_name="us-east-1").create_queue(QueueName="q")["QueueUrl"]
        settings = Settings(
            app_env="test",
            persistence_backend="dynamodb",
            dense_backend="artifact",
            graph_backend="memory",
            aws_region_name="us-east-1",
            dynamodb_table=table,
            object_store_bucket=bucket,
            jobs_queue_url=queue,
            model_cache_dir=str(ROOT / "backend" / ".model-cache"),
        )
        rt = Runtime.create(settings)
        counting = CountingDynamo(rt.control._t._client)  # type: ignore[attr-defined]
        rt.control._t._client = counting  # type: ignore[attr-defined]
        if llm_kind == "gemini":
            assert settings.gemini_keys, "no GEMINI_API_KEY_* in the environment or .env"
            inner: Any = build_gateway(settings, None, counters=rt.counters)
            rt.llm = inner
        else:
            inner = ScriptedLLM()
        llm = rec.wrap(inner)
        now = datetime.now(UTC)
        pid, jid = str(uuid.uuid4()), str(uuid.uuid4())
        key = f"projects/{pid}/upload/source.zip"
        archive = ap._zip(fixture)
        await rt.objects.put_bytes(key, archive, "application/zip")
        session = await rt.control.create_session(
            token_hash(new_session_token()), now, now + timedelta(hours=4)
        )
        project = ProjectRec(
            id=pid,
            session_id=session.id,
            input_type="zip",
            technical_level="intermediate",
            analysis_version="profile",
            created_at=now,
            expires_at=now + timedelta(hours=4),
            source_locator=f"{fixture.name}.zip",
            detected_name=fixture.name,
            upload_object_key=key,
        )
        await rt.control.create_project(project, JobRec(id=jid, project_id=pid, created_at=now))
        rss_before = proc.memory_info().rss
        startup_s = time.monotonic() - t_start
        peaks: dict[str, int] = {}
        cpu: dict[str, float] = {}
        psutil.cpu_percent()
        stop = threading.Event()
        th = threading.Thread(target=sampler, args=(stop, peaks, cpu), daemon=True)
        th.start()
        started = time.monotonic()
        outcome = await pipeline_mod.run_analysis(rt, llm, jid)
        wall = time.monotonic() - started
        stop.set()
        th.join()
        system_cpu = psutil.cpu_percent()
        job = await rt.control.get_job(jid)
        assert job is not None
        chunks_n = len(await rt.chunks.all_ids(pid))
        mi = proc.memory_info()
        report: dict[str, Any] = {
            "target": fixture.name,
            "files": sum(1 for p in fixture.rglob("*") if p.is_file() and "__pycache__" not in p.parts),
            "zip_bytes": len(archive),
            "chunks": chunks_n,
            "llm": llm_kind,
            "cpus_pinned": cpus or "all",
            "succeeded": outcome.succeeded,
            "job_status": job.status,
            "job_error": job.error_code,
            "degraded": (job.stage_progress or {}).get("degraded", []),
            "wall_s": round(wall, 2),
            "startup_s": round(startup_s, 2),
            "rss_mb_before_run": round(rss_before / 2**20, 0),
            "peak_rss_mb": round(max(peaks.values(), default=0) / 2**20, 0),
            "peak_wset_mb": round(getattr(mi, "peak_wset", 0) / 2**20, 0),
            "peak_commit_mb": round(getattr(mi, "peak_pagefile", 0) / 2**20, 0),
            "process_cpu_s": round(sum(cpu.values()), 1),
            "process_cpu_s_by_stage": {k: round(v, 1) for k, v in cpu.items()},
            "system_cpu_percent_during_run": system_cpu,
            "peak_rss_mb_by_stage": {k: round(v / 2**20) for k, v in peaks.items()},
            "dynamodb": counting.summary(),
            "stage_s": spans.stages,
            "retrieval_s": dist(spans.retrieval),
            **rec.report(),
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1), encoding="utf-8")
        print(
            json.dumps(
                {
                    k: report[k]
                    for k in ("target", "llm", "succeeded", "wall_s", "peak_rss_mb", "peak_wset_mb")
                }
            )
        )
        print(json.dumps(report["stage_s"]))
        await rt.aclose()
        return 0 if outcome.succeeded else 1


if __name__ == "__main__":
    ap_ = argparse.ArgumentParser()
    ap_.add_argument("target", help="fixture name under evals/fixtures/repos, or a directory path")
    ap_.add_argument("out", type=Path)
    ap_.add_argument("--llm", choices=["scripted", "gemini"], default="scripted")
    ap_.add_argument("--cpus", type=int, default=0, help="pin the process to the first N logical CPUs")
    a = ap_.parse_args()
    sys.exit(asyncio.run(main(a.target, a.out, a.llm, a.cpus)))
