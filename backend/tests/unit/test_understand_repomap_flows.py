"""RepoMap, flow tracing and ArchitectureModel (phase 3B tasks 3.2/3.3), on real analyzer output.

Fixtures: the committed `fastapi_rag` / `express_ts_prisma` repositories and tiny repos written to tmp_path
(parsed only, never executed)."""

from __future__ import annotations

from pathlib import Path

import pytest

from unfold.core.errors import NotFound
from unfold.diagrams.builders import build_all
from unfold.domain.enums import Proof
from unfold.evaluation.corpus import load_fixture
from unfold.project_ir.schema import ProjectIR
from unfold.understand.architecture import build_architecture
from unfold.understand.flows import trace_flow
from unfold.understand.models import ArchitectureModel
from unfold.understand.repomap import RepoMap


def _repo(tmp_path: Path, files: dict[str, str]) -> ProjectIR:
    root = tmp_path / "repo"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return load_fixture("repo", root=tmp_path).ir


@pytest.fixture(scope="module")
def rag() -> ProjectIR:
    return load_fixture("fastapi_rag").ir


@pytest.fixture(scope="module")
def express() -> ProjectIR:
    return load_fixture("express_ts_prisma").ir


def _names(trace_steps: list) -> list[str]:  # type: ignore[type-arg]
    return [s.symbol.name for s in trace_steps]


# ---- RepoMap -----------------------------------------------------------------------------------------------


def test_symbol_lookup_returns_located_refs(rag: ProjectIR) -> None:
    rm = RepoMap(rag)
    ref = rm.symbol("IngestionService.ingest")
    assert ref is not None
    assert ref.path == "app/services/ingestion.py"
    assert 0 < ref.start_line <= ref.end_line
    assert rm.symbol(ref.id) == ref
    assert rm.symbol("no_such_symbol_anywhere") is None
    assert rm.symbol("get_document") is None  # ambiguous: a route handler and a service method
    assert {r.path for r in rm.find("get_document")} == {
        "app/api/routes/documents.py",
        "app/services/ingestion.py",
    }
    assert all(r.kind == "class" for r in rm.find("Document", kind="class"))
    assert all(r.start_line >= 1 and r.end_line >= r.start_line and r.path for r in rm.files())


def test_importers_are_static_edges(rag: ProjectIR) -> None:
    rm = RepoMap(rag)
    config = rm.symbol("app/core/config.py")
    assert config is not None
    edges = [e for e in rm.neighbours(config.id, rels=("IMPORTS",)) if e.dst == config.id]
    assert edges
    importers = {rm.ref(e.src).path for e in edges}  # type: ignore[union-attr]
    assert "app/main.py" in importers
    assert {e.proof for e in edges} == {Proof.STATIC}


def test_call_edges_are_heuristic_and_entry_points_cover_routes(rag: ProjectIR) -> None:
    rm = RepoMap(rag)
    ingest = rm.symbol("IngestionService.ingest")
    assert ingest is not None
    calls = [e for e in rm.neighbours(ingest.id, rels=("CALLS",)) if e.src == ingest.id]
    assert calls
    assert {e.proof for e in calls} == {Proof.HEURISTIC}
    labels = {rm.entry_label(r.id) for r in rm.entry_points()}
    assert {"GET /documents", "POST /documents", "POST /query", "GET /health"} <= labels


# ---- flows -------------------------------------------------------------------------------------------------


def test_call_chain_is_ordered(tmp_path: Path) -> None:
    ir = _repo(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/a.py": "from pkg.b import b\n\n\ndef a():\n    return b()\n",
            "pkg/b.py": "from pkg.c import c\n\n\ndef b():\n    return c()\n",
            "pkg/c.py": "def c():\n    return 1\n",
        },
    )
    rm = RepoMap(ir)
    trace = trace_flow(rm, "a")
    assert _names(trace.steps) == ["a", "b", "c"]
    assert [s.via.rel for s in trace.steps[1:] if s.via] == ["CALLS", "CALLS"]
    assert all(s.via and s.via.proof is Proof.HEURISTIC for s in trace.steps[1:])
    assert not trace.truncated and not trace.unresolved
    assert trace == trace_flow(rm, "a")  # deterministic


def test_depth_and_fan_out_caps_set_truncated(tmp_path: Path) -> None:
    ir = _repo(
        tmp_path,
        {
            "m.py": (
                "def a():\n    b()\n\n\ndef b():\n    c()\n\n\ndef c():\n    d()\n\n\ndef d():\n    pass\n\n\n"
                "def wide():\n    a()\n    b()\n    c()\n    d()\n"
            )
        },
    )
    rm = RepoMap(ir)
    shallow = trace_flow(rm, "a", max_depth=1)
    assert _names(shallow.steps) == ["a", "b"] and shallow.truncated
    narrow = trace_flow(rm, "wide", fan_out=2)  # picks a and b; c and d are reached below a
    assert _names(narrow.steps) == ["wide", "a", "b", "c", "d"] and narrow.truncated
    assert not trace_flow(rm, "a").truncated


def test_cycle_terminates(tmp_path: Path) -> None:
    ir = _repo(tmp_path, {"m.py": "def ping():\n    pong()\n\n\ndef pong():\n    ping()\n"})
    trace = trace_flow(RepoMap(ir), "ping")
    assert _names(trace.steps) == ["ping", "pong"]
    assert not trace.truncated


def test_route_handler_to_service_to_db_access(rag: ProjectIR) -> None:
    rm = RepoMap(rag)
    handler = next(r for r in rm.entry_points() if rm.entry_label(r.id) == "GET /documents")
    trace = trace_flow(rm, handler.id)
    assert trace.steps[0].note == "GET /documents"
    by_name = {s.symbol.name: s for s in trace.steps}
    assert "IngestionService.list_documents" in by_name
    document = by_name["Document"]
    assert document.symbol.kind == "schema"
    assert document.via is not None and document.via.rel == "READS_FROM"
    order = _names(trace.steps)
    assert order.index("IngestionService.list_documents") < order.index("Document")


def test_file_level_store_usage_is_a_step(rag: ProjectIR) -> None:
    rm = RepoMap(rag)
    trace = trace_flow(rm, "HybridRetriever.search", max_depth=6)
    stores = [s for s in trace.steps if s.symbol.kind == "datastore"]
    assert [s.symbol.name for s in stores] == ["Qdrant"]
    assert stores[0].via is not None and stores[0].via.rel == "USES"


def test_dynamic_call_is_unresolved_not_an_edge(tmp_path: Path) -> None:
    ir = _repo(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/handlers.py": "def on_save(x):\n    return x\n",
            "app/dispatch.py": (
                "from app import handlers\n\n\ndef run(kind, payload):\n"
                "    fn = getattr(handlers, kind)\n    return fn(payload)\n"
            ),
        },
    )
    rm = RepoMap(ir)
    trace = trace_flow(rm, "run")
    assert _names(trace.steps) == ["run"]
    assert any(u.startswith("getattr (app/dispatch.py:") for u in trace.unresolved)
    run = rm.symbol("run")
    assert run is not None
    assert not [r for r in ir.relationships if r.source_id == run.id and r.type == "CALLS"]
    arch = build_architecture(ir)
    assert "dynamic dispatch in app/dispatch.py" in arch.uncertainty


def test_unknown_or_ambiguous_entry_raises(rag: ProjectIR) -> None:
    rm = RepoMap(rag)
    with pytest.raises(NotFound):
        trace_flow(rm, "does_not_exist")
    with pytest.raises(NotFound):
        trace_flow(rm, "get_document")


# ---- architecture ------------------------------------------------------------------------------------------


def test_architecture_of_a_web_service(rag: ProjectIR) -> None:
    arch = build_architecture(rag)
    assert arch.project_kind == "web_service"
    assert arch.components and arch.entry_points and arch.flows
    assert {b.store for b in arch.persistence} >= {"PostgreSQL", "Qdrant"}
    postgres = next(b for b in arch.persistence if b.store == "PostgreSQL")
    assert any(a.name == "IngestionService.list_documents" for a in postgres.access)
    assert "fastapi" in {d.name for d in arch.external}
    refs = [s.symbol for f in arch.flows for s in f.steps] + arch.entry_points
    assert all(r.path and 0 < r.start_line <= r.end_line for r in refs)
    assert arch == build_architecture(rag)
    assert ArchitectureModel.model_validate_json(arch.model_dump_json()) == arch


def test_unresolved_calls_surface_as_uncertainty(express: ProjectIR) -> None:
    arch = build_architecture(express)
    assert any("not resolved in src/routes/posts.ts" in u for u in arch.uncertainty)


def test_unsupported_language_is_listed(tmp_path: Path) -> None:
    ir = _repo(
        tmp_path,
        {
            "main.py": "def main():\n    return 1\n",
            "lib/Core.hs": "module Core where\n\nanswer :: Int\nanswer = 42\n",
        },
    )
    arch = build_architecture(ir)
    assert any(u.startswith("unsupported language: haskell") for u in arch.uncertainty)
    assert not any("python" in u for u in arch.uncertainty)


# ---- proof labels ------------------------------------------------------------------------------------------


def test_builder_labels_proof(rag: ProjectIR) -> None:
    by_type: dict[str, set[Proof]] = {}
    for r in rag.relationships:
        by_type.setdefault(r.type, set()).add(r.proof)
    assert by_type["IMPORTS"] == {Proof.STATIC}
    assert by_type["HANDLES"] == {Proof.STATIC}
    assert by_type["CALLS"] == {Proof.HEURISTIC}
    assert Proof.INFERRED not in set().union(*by_type.values())


def test_snapshots_written_before_proof_still_load(rag: ProjectIR) -> None:
    payload = rag.model_dump(mode="json")
    payload.pop("unresolved_calls")
    for r in payload["relationships"]:
        r.pop("proof")
    old = ProjectIR.model_validate(payload)
    assert {r.proof for r in old.relationships} == {Proof.STATIC} and old.unresolved_calls == []


def test_diagrams_never_draw_inferred_edges(rag: ProjectIR) -> None:
    def kinds(ir: ProjectIR) -> set[str]:
        return {e.kind for spec in build_all(ir.project.project_id, ir, [], []) for e in spec.edges}

    inferred = rag.model_copy(
        update={"relationships": [r.model_copy(update={"proof": Proof.INFERRED}) for r in rag.relationships]}
    )
    assert {"IMPORTS", "CALLS"} <= kinds(rag)
    assert not {"IMPORTS", "CALLS"} & kinds(inferred)
