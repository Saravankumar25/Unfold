"""Differential tests for the AWS retrieval backends against the oracles (decision 4.5, ADR 0013).

Each committed fixture repo is analysed once by the real pipeline stages (analyze -> graph -> lexical -> dense ->
subsystems; no LLM), so Qdrant and Neo4j hold exactly what production would write. The S3 dense artifact and the
in-memory graph are then built from the same chunks / IR and compared with the oracles. Measured numbers are
printed (run with `-s`) and asserted against the quality gate.
"""

from __future__ import annotations

import io
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from unfold.cleanup.service import cleanup_project
from unfold.core.ids import token_hash
from unfold.graph.builder import ir_graph
from unfold.graph.memory import MemoryGraph
from unfold.graph.queries import Neo4jGraphQueries
from unfold.indexing.dense import DenseIndex
from unfold.indexing.dense_artifact import ArtifactDenseStore, DenseArtifactCache
from unfold.indexing.embeddings import embedder_for
from unfold.indexing.models import Chunk
from unfold.indexing.store import ChunkRepository
from unfold.orchestration.pipeline import _Run
from unfold.orchestration.progress import ProgressReporter
from unfold.persistence.postgres.models import AnalysisJobRow, ProjectRow, SessionRow
from unfold.project_ir.schema import ProjectIR
from unfold.project_ir.snapshot import load_ir
from unfold.retrieval.config import GRAPH_RETRIEVAL_REL_TYPES
from unfold.runtime import Runtime

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[3] / "evals" / "fixtures" / "repos"
NAMES = ["fastapi_rag", "express_ts_prisma", "terraform_k8s"]


@dataclass
class Analyzed:
    name: str
    project_id: str
    ir: ProjectIR
    chunks: list[Chunk]


def _zip_of(root: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and "node_modules" not in path.parts:
                zf.write(path, f"{root.name}/{path.relative_to(root).as_posix()}")
    return buf.getvalue()


async def _analyze(rt: Runtime, name: str) -> Analyzed:
    """Seed rows exactly as `POST /projects/zip` does, then run the retrieval-index stages of the pipeline."""
    now = datetime.now(UTC)
    project_id = uuid.uuid4()
    key = f"projects/{project_id}/upload/source.zip"
    await rt.objects.put_bytes(key, _zip_of(FIXTURES / name), "application/zip")
    async with rt.db.transaction() as s:
        session = SessionRow(
            token_hash=token_hash(uuid.uuid4().hex),
            created_at=now,
            last_active_at=now,
            expires_at=now + timedelta(hours=1),
            status="active",
        )
        s.add(session)
        await s.flush()
        project = ProjectRow(
            id=project_id,
            session_id=session.id,
            input_type="zip",
            source_locator=f"{name}.zip",
            detected_name=name,
            technical_level="intermediate",
            analysis_version="test",
            upload_object_key=key,
            expires_at=now + timedelta(hours=1),
        )
        s.add(project)
        await s.flush()
        job = AnalysisJobRow(
            project_id=project_id, status="queued", current_stage="RECEIVED", stage_progress={}, attempt=0
        )
        s.add(job)
        await s.flush()
        job_id = str(job.id)
    reporter = ProgressReporter(rt.db, rt.progress, str(project_id), job_id)
    assert await reporter.start(alive_within=timedelta(minutes=2)) == 1
    run = _Run(rt, None, project, job_id, reporter)  # type: ignore[arg-type]  # stages below never call the LLM
    for step in (run._analyze, run._graph, run._lexical, run._dense, run._subsystems):
        await step()
    async with rt.db.session() as s:
        job_row = await s.get(AnalysisJobRow, uuid.UUID(job_id))
        project_row = await s.get(ProjectRow, project_id)
    assert job_row is not None and project_row is not None and project_row.ir_object_key
    assert not job_row.stage_progress.get("degraded"), job_row.stage_progress
    ir = await load_ir(rt.objects, project_row.ir_object_key)
    chunks = await ChunkRepository(rt.db).list_project(str(project_id))
    return Analyzed(name, str(project_id), ir, chunks)


@pytest.fixture(scope="module")
async def analyzed(runtime: Runtime) -> Any:
    out: list[Analyzed] = []
    try:
        for name in NAMES:
            out.append(await _analyze(runtime, name))
        yield out
    finally:
        for a in out:
            await cleanup_project(runtime, a.project_id)


# --- dense: S3 artifact vs Qdrant -----------------------------------------------------------------------------


def _queries(chunks: list[Chunk], limit: int = 60) -> list[str]:
    """Chunk-text queries (first 240 chars), evenly sampled, plus the bare symbol names."""
    step = max(1, len(chunks) // limit)
    sample = chunks[::step][:limit]
    texts = [c.text[:240] for c in sample]
    names = [c.symbol_name for c in sample if c.symbol_name]
    return [*texts, *names[: limit // 2]]


async def test_dense_artifact_matches_qdrant(runtime: Runtime, analyzed: list[Analyzed]) -> None:
    embedder = embedder_for(runtime)
    qdrant = DenseIndex(runtime.qdrant, embedder, runtime.redis)
    artifact = ArtifactDenseStore(runtime.objects, embedder, DenseArtifactCache())
    total = recall_sum = top1 = 0
    filtered_total = filtered_recall = 0
    for a in analyzed:
        assert await artifact.upsert(a.chunks) == len(a.chunks)
        qs = _queries(a.chunks)
        for q in qs:
            want = await qdrant.search(a.project_id, q, k=40)
            got = await artifact.search(a.project_id, q, k=40)
            assert want, f"{a.name}: Qdrant returned nothing"
            total += 1
            recall_sum += len({c for c, _ in want} & {c for c, _ in got}) / len(want)
            top1 += int(want[0][0] == got[0][0])
        # filters keep the same semantics (content type, subsystem)
        types = sorted({c.content_type.value for c in a.chunks})
        subs = sorted({c.subsystem_id for c in a.chunks if c.subsystem_id})
        for q in qs[:20]:
            for kwargs in (
                {"content_types": types[:1]},
                {"subsystem_ids": subs[:2]} if subs else {"content_types": types[-1:]},
                {"content_types": types[:1], "subsystem_ids": subs[:1]} if subs else {},
            ):
                want = await qdrant.search(a.project_id, q, k=40, **kwargs)
                got = await artifact.search(a.project_id, q, k=40, **kwargs)
                if not want:
                    assert not got
                    continue
                filtered_total += 1
                filtered_recall += len({c for c, _ in want} & {c for c, _ in got}) / len(want)
    recall = recall_sum / total
    top1_rate = top1 / total
    print(
        f"\nDENSE PARITY queries={total} recall@40={recall:.4f} top1_equal={top1_rate:.4f} "
        f"filtered_queries={filtered_total} filtered_recall@40={filtered_recall / max(1, filtered_total):.4f}"
    )
    assert recall >= 0.95 and top1_rate >= 0.95
    assert filtered_total and filtered_recall / filtered_total >= 0.95


# --- graph: in-memory vs Neo4j --------------------------------------------------------------------------------

_WORD = re.compile(r"[A-Za-z][a-z0-9]+|[A-Z]+(?![a-z])")
GraphPair = tuple[Analyzed, Neo4jGraphQueries, MemoryGraph]


def _uids(a: Analyzed, limit: int = 120) -> list[str]:
    g = ir_graph(a.ir)
    uids = sorted({row["uid"] for rows in g.by_label.values() for row in rows})
    step = max(1, len(uids) // limit)
    return uids[::step][:limit]


def _name_queries(a: Analyzed) -> list[list[str]]:
    """Exact names, case variants, single words taken from names, and multi-name seed lists."""
    names = sorted({s.name for s in a.ir.symbols} | {f.path.rsplit("/", 1)[-1] for f in a.ir.files})
    step = max(1, len(names) // 60)
    picked = names[::step][:60]
    words = sorted({w.lower() for n in picked for w in _WORD.findall(n) if len(w) > 3})[:40]
    singles = [[n] for n in picked] + [[n.upper()] for n in picked[:15]] + [[w] for w in words]
    multi = [picked[i : i + 4] for i in range(0, min(len(picked), 40), 4)]
    return [*singles, *multi, ["no_such_entity_zzz"]]


@pytest.fixture
def graphs(runtime: Runtime, analyzed: list[Analyzed]) -> list[GraphPair]:
    return [(a, Neo4jGraphQueries(runtime.neo4j), MemoryGraph.from_ir(a.ir)) for a in analyzed]


def _ids(nodes: list[Any]) -> list[str]:
    return [n.uid for n in nodes]


async def test_graph_memory_matches_neo4j_where_results_are_fully_determined(graphs: list[GraphPair]) -> None:
    """get_node, neighbors, callers, callees, subsystem_members, overview, module_dependencies, endpoint_flows."""
    tally: dict[str, list[int]] = {}

    def note(method: str, ok: bool) -> None:
        t = tally.setdefault(method, [0, 0])
        t[0] += int(ok)
        t[1] += 1

    problems: list[str] = []
    for a, neo, mem in graphs:
        pid = a.project_id
        for uid in _uids(a):
            note("get_node", await neo.get_node(pid, uid) == await mem.get_node(pid, uid))
            for depth in (1, 2, 3):
                for rels in (None, list(GRAPH_RETRIEVAL_REL_TYPES)):
                    for limit in (5, 50):
                        want = await neo.neighbors(pid, uid, depth=depth, rel_types=rels, limit=limit)
                        got = await mem.neighbors(pid, uid, depth=depth, rel_types=rels, limit=limit)
                        note("neighbors", want == got)
                        if want != got and len(problems) < 8:
                            problems.append(
                                f"neighbors {a.name} {uid} d={depth} l={limit}: "
                                f"{_ids(want.nodes)[:6]} vs {_ids(got.nodes)[:6]}"
                            )
            for depth in (1, 2, 3):
                note(
                    "callers",
                    await neo.callers(pid, uid, depth=depth) == await mem.callers(pid, uid, depth=depth),
                )
                note(
                    "callees",
                    await neo.callees(pid, uid, depth=depth) == await mem.callees(pid, uid, depth=depth),
                )
        subsystems = sorted(
            {n.props["subsystem_id"] for n in mem.nodes.values() if n.props.get("subsystem_id")}
        )
        for sid in subsystems:
            want_m = await neo.subsystem_members(pid, sid)
            got_m = await mem.subsystem_members(pid, sid)
            note("subsystem_members", want_m == got_m)
            note("subsystem_members(set)", {n.uid for n in want_m} == {n.uid for n in got_m})
        note("overview", await neo.overview(pid) == await mem.overview(pid))
        note("module_dependencies", await neo.module_dependencies(pid) == await mem.module_dependencies(pid))
        want_f, got_f = await neo.endpoint_flows(pid, limit=50), await mem.endpoint_flows(pid, limit=50)
        note("endpoint_flows", want_f == got_f)
        if want_f != got_f and len(problems) < 12:
            for w, g in zip(want_f, got_f, strict=False):
                if w != g:
                    problems.append(f"endpoint_flows {a.name}: {w.model_dump()} VS {g.model_dump()}")
                    break
    print("\nGRAPH PARITY (identical / total):")
    for method, (ok, total) in sorted(tally.items()):
        print(f"  {method:26s} {ok}/{total}")
    for line in problems:
        print("  MISMATCH", line)
    for method, (ok, total) in tally.items():
        assert ok == total, f"{method}: {ok}/{total}"


async def test_graph_memory_find_entities_and_paths(graphs: list[GraphPair]) -> None:
    overlap_sum = exact_same = queries = 0
    path_total = path_same_len = path_valid = path_none_agree = 0
    problems: list[str] = []
    for a, neo, mem in graphs:
        pid = a.project_id
        for names in _name_queries(a):
            want = await neo.find_entities(pid, names, limit=20)
            got = await mem.find_entities(pid, names, limit=20)
            queries += 1
            overlap = (
                len({n.uid for n in want} & {n.uid for n in got}) / len(want) if want else float(not got)
            )
            overlap_sum += overlap
            exact_same += int(_ids(want) == _ids(got))
            if overlap < 0.9 and len(problems) < 6:
                problems.append(
                    f"find_entities {a.name} {names[:3]}: {[n.name for n in want][:5]} vs {[n.name for n in got][:5]}"
                )
        uids = _uids(a, 40)
        for i, x in enumerate(uids):
            for y in uids[i + 1 : i + 4]:
                for hops in (4, 6):
                    want_p = await neo.path_between(pid, x, y, max_hops=hops)
                    got_p = await mem.path_between(pid, x, y, max_hops=hops)
                    path_total += 1
                    if want_p is None or got_p is None:
                        agree = want_p is None and got_p is None
                        path_none_agree += int(agree)
                        if not agree and len(problems) < 10:
                            problems.append(f"path {a.name} {x}->{y} hops={hops}: neo={want_p is not None}")
                        continue
                    path_same_len += int(len(want_p.nodes) == len(got_p.nodes))
                    ok = got_p.nodes[0].uid == x and got_p.nodes[-1].uid == y
                    ok = ok and len(got_p.relationships) == len(got_p.nodes) - 1
                    hops_ = zip(got_p.nodes, got_p.nodes[1:], got_p.relationships, strict=False)
                    for u, v, t in hops_:
                        ok = ok and ((t, u.uid, v.uid) in mem.edges or (t, v.uid, u.uid) in mem.edges)
                    path_valid += int(ok)
    print(
        f"\nFIND_ENTITIES queries={queries} mean_top20_overlap={overlap_sum / queries:.4f} "
        f"identical_order={exact_same / queries:.4f}\n"
        f"PATHS pairs={path_total} both_none={path_none_agree} same_length={path_same_len} "
        f"valid_memory_paths={path_valid}"
    )
    for line in problems:
        print("  MISMATCH", line)
    assert overlap_sum / queries >= 0.90
    assert path_total == path_none_agree + path_same_len
    assert path_same_len == path_valid


# --- the backends end to end: pipeline stages + hybrid retrieval with DENSE_BACKEND=artifact, GRAPH_BACKEND=memory ---


async def test_pipeline_and_retrieval_run_on_the_aws_backends(runtime: Runtime) -> None:
    from unfold.indexing.dense_artifact import dense_key
    from unfold.retrieval.hybrid import HybridRetriever

    rt = Runtime.create(
        runtime.settings.model_copy(update={"dense_backend": "artifact", "graph_backend": "memory"}),
        db_pool_size=3,
    )
    a: Analyzed | None = None
    try:
        a = await _analyze(rt, "fastapi_rag")  # asserts no component degraded
        embedder = embedder_for(rt)
        # the artifact is written, and nothing went to Qdrant or Neo4j
        assert await rt.objects.get_bytes(dense_key(a.project_id, embedder.model_name))
        assert await DenseIndex(rt.qdrant, embedder, rt.redis).search(a.project_id, "retrieval") == []
        assert (await Neo4jGraphQueries(rt.neo4j).overview(a.project_id)).node_counts == {}

        async with rt.db.session() as s:
            project = await s.get(ProjectRow, uuid.UUID(a.project_id))
        assert project is not None
        retriever = HybridRetriever.for_project(rt, a.project_id, a.ir, project.lexical_object_key)
        result = await retriever.search("How does the HybridRetriever combine lexical and dense search?")
        counts = result.trace.per_source_counts
        print(f"\nBACKENDS E2E per_source_counts={counts} degraded={result.trace.degraded}")
        assert result.items
        assert not {"dense", "graph"} & set(result.trace.degraded), result.trace.errors
        assert counts.get("dense", 0) > 0 and counts.get("graph", 0) > 0
    finally:
        if a is not None:
            await cleanup_project(rt, a.project_id)
        await rt.aclose()
