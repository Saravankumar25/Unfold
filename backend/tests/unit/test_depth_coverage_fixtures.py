"""Depth and coverage checks (phase 5 task 10) on committed fixture repositories, with the real analyzers, the real
`ArchitectureModel` and the real planner skeleton (no model): outlines differ by project shape in specific ways, and
inputs the analysis cannot understand produce stated uncertainty, never an invented flow."""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

import pytest

from unfold.core.errors import ErrorCode, GenerationError
from unfold.domain.enums import Proof
from unfold.evaluation.corpus import FixtureProject, load_fixture
from unfold.evaluation.scripted import ScriptedLLM
from unfold.guide.models import ChapterPlan
from unfold.guide.plan import build_skeleton, plan_chapters
from unfold.project_ir.builder import build_ir
from unfold.project_ir.schema import ProjectIdentity, ProjectIR
from unfold.repo_intel.service import analyze_texts, scan_repository
from unfold.understand.architecture import build_architecture
from unfold.understand.flows import trace_flow
from unfold.understand.models import ArchitectureModel
from unfold.understand.repomap import RepoMap

HTTP = re.compile(r"^(GET|POST|PUT|PATCH|DELETE) /")


@cache
def _fixture(name: str) -> FixtureProject:
    return load_fixture(name)


def _shape(ir: ProjectIR) -> tuple[RepoMap, ArchitectureModel, list[ChapterPlan]]:
    rm, arch = RepoMap(ir), build_architecture(ir)
    return rm, arch, build_skeleton(ir, rm, arch)


def _generated(tmp_path: Path, files: dict[str, str | bytes]) -> ProjectIR:
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data if isinstance(data, bytes) else data.encode())
    return load_fixture("repo", root=tmp_path).ir


async def _plan(ir: ProjectIR) -> tuple[list[str], bool]:
    rm, arch = RepoMap(ir), build_architecture(ir)
    plan, degraded = await plan_chapters(
        ScriptedLLM(),
        ir,
        rm,
        arch,
        [],
        technical_level="intermediate",
        instruction=None,
        job_id="j",
        project_id="p",
    )  # type: ignore[arg-type]
    return [c.key for c in plan.sections], degraded


def _assert_no_invented_flow(rm: RepoMap, arch: ArchitectureModel) -> None:
    """Every step is a located entity reached by an edge the analyzers saw, never one inferred from names alone."""
    for flow in arch.flows:
        assert flow.steps[0].via is None
        for step in flow.steps[1:]:
            assert rm.ref(step.symbol.id) is not None
            assert step.via is not None and step.via.proof is not Proof.INFERRED


# ---- project shapes: the outline differs in kind, count and targets ---------------------------------------


@pytest.fixture(scope="module")
def shapes() -> dict[str, tuple[RepoMap, ArchitectureModel, list[ChapterPlan]]]:
    return {
        "cli": _shape(_fixture("cli_tool").ir),
        "web": _shape(_fixture("fastapi_rag").ir),
        "multi": _shape(_fixture("multi_module").ir),
    }


def test_each_shape_gets_its_own_backbone(
    shapes: dict[str, tuple[RepoMap, ArchitectureModel, list[ChapterPlan]]],
) -> None:
    kinds = {k: arch.project_kind for k, (_, arch, _) in shapes.items()}
    assert kinds == {"cli": "cli", "web": "web_service", "multi": "monorepo"}
    keys = {k: [c.key for c in chapters] for k, (_, _, chapters) in shapes.items()}
    assert keys["cli"] == ["overview", "usage", "internals", "config-deploy"]
    assert keys["web"][:5] == [
        "overview",
        "request-lifecycle",
        "data-persistence",
        "background-jobs",
        "config-deploy",
    ]
    assert keys["multi"][:3] == ["overview", "packages", "config-deploy"]
    # a chapter that belongs to one shape appears in no other
    assert {"usage"} & set(keys["web"] + keys["multi"]) == set()
    assert {"request-lifecycle", "data-persistence", "background-jobs"} & set(
        keys["cli"] + keys["multi"]
    ) == set()
    assert {"packages"} & set(keys["cli"] + keys["web"]) == set()
    # the service with routes, a store and a worker is the largest outline
    assert len(keys["web"]) > len(keys["cli"]) == len(keys["multi"])


def test_chapters_target_what_the_shape_is_made_of(
    shapes: dict[str, tuple[RepoMap, ArchitectureModel, list[ChapterPlan]]],
) -> None:
    by_key = {k: {c.key: c for c in chapters} for k, (_, _, chapters) in shapes.items()}
    cli_rm, web_rm, multi_rm = shapes["cli"][0], shapes["web"][0], shapes["multi"][0]

    assert {cli_rm.entry_label(t) for t in by_key["cli"]["usage"].targets} == {"command"}
    lifecycle = by_key["web"]["request-lifecycle"]
    assert lifecycle.depth == "deep" and "route" in lifecycle.expected_evidence
    assert all(HTTP.match(web_rm.entry_label(t) or "") for t in lifecycle.targets)
    assert (
        by_key["cli"]["usage"].depth == "standard" and "route" not in by_key["cli"]["usage"].expected_evidence
    )

    manifests = {web_rm.ref(t).path for t in by_key["web"]["config-deploy"].targets}  # type: ignore[union-attr]
    assert {"Dockerfile", "docker-compose.yml", "pyproject.toml"} <= manifests
    assert [cli_rm.ref(t).path for t in by_key["cli"]["config-deploy"].targets] == ["requirements.txt"]  # type: ignore[union-attr]
    # a monorepo's packages chapter has one target per package, not one community that swallows them all
    titles = {c.id: c.title for c in shapes["multi"][1].components}
    assert sorted(titles[t] for t in by_key["multi"]["packages"].targets) == [
        f"packages/{p}" for p in ("index", "ingest", "report")
    ]
    assert {multi_rm.ref(t).path for t in by_key["multi"]["config-deploy"].targets} == {  # type: ignore[union-attr]
        f"packages/{p}/requirements.txt" for p in ("index", "ingest", "report")
    }


def test_architecture_evidence_matches_the_shape(
    shapes: dict[str, tuple[RepoMap, ArchitectureModel, list[ChapterPlan]]],
) -> None:
    cli, web, multi = (shapes[k][1] for k in ("cli", "web", "multi"))
    assert {b.store for b in web.persistence} >= {"PostgreSQL", "Qdrant"} and not cli.persistence
    assert len(web.flows) > len(cli.flows) > len(multi.flows) == 0
    assert multi.uncertainty == ["no entry points detected"]  # libraries side by side: said, not invented
    assert max(len(f.steps) for f in web.flows) > max(len(f.steps) for f in cli.flows)
    for rm, arch, _ in shapes.values():
        _assert_no_invented_flow(rm, arch)


async def test_the_scripted_planner_keeps_each_outline_and_does_not_degrade(
    shapes: dict[str, tuple[RepoMap, ArchitectureModel, list[ChapterPlan]]],
) -> None:
    for name, fixture in (("cli", "cli_tool"), ("web", "fastapi_rag"), ("multi", "multi_module")):
        keys, degraded = await _plan(_fixture(fixture).ir)
        assert keys == [c.key for c in shapes[name][2]] and not degraded


# ---- negative fixtures: stated uncertainty, not an invented flow ------------------------------------------


async def test_a_repository_in_an_unsupported_language_says_so() -> None:
    ir = _fixture("haskell_only").ir
    rm, arch, chapters = _shape(ir)
    assert ir.symbols == [] and arch.project_kind == "unknown"
    assert arch.uncertainty == [
        "unsupported language: haskell (no symbols extracted)",
        "no entry points detected",
    ]
    assert arch.entry_points == [] and arch.flows == [] and arch.persistence == []
    with pytest.raises(Exception, match="No unique entry point"):
        trace_flow(rm, "main")  # the `main` of Main.hs is not a symbol we know; there is nothing to trace
    # the outline is the generic one, and every chapter points at files that exist
    assert [c.key for c in chapters][:3] == ["overview", "how-it-runs", "internals"]
    known = {f.path for f in ir.files}
    assert all(
        (rm.ref(t) is not None and rm.ref(t).path in known) or t in {c.id for c in arch.components}  # type: ignore[union-attr]
        for c in chapters
        for t in c.targets
    )
    keys, degraded = await _plan(ir)
    assert keys and not degraded


def test_dynamic_dispatch_is_reported_as_unresolved_and_never_drawn() -> None:
    fx = _fixture("dynamic_dispatch")
    rm, arch, chapters = _shape(fx.ir)
    assert "dynamic dispatch in host.py" in arch.uncertainty
    assert sorted(u.name for u in fx.ir.unresolved_calls) == ["getattr", "getattr", "importlib.import_module"]

    run = trace_flow(rm, "run")  # getattr(csv_export, kind)(rows): which function runs is decided at run time
    assert [s.symbol.name for s in run.steps] == ["run"] and run.unresolved == ["getattr (host.py:15)"]
    load = trace_flow(rm, "load")
    assert [s.symbol.name for s in load.steps] == ["load"] and len(load.unresolved) == 2
    main = trace_flow(rm, "main")  # what it can resolve statically it follows, and still lists the rest
    assert [s.symbol.name for s in main.steps] == ["main", "dispatch", "load", "run"]
    assert len(main.unresolved) == 3

    exporters = {s.id for s in fx.ir.symbols if s.name == "export"}
    assert len(exporters) == 2
    assert not exporters & {r.target_id for r in fx.ir.relationships if r.type == "CALLS"}
    _assert_no_invented_flow(rm, arch)
    assert not {"request-lifecycle", "background-jobs"} & {c.key for c in chapters}


async def test_an_empty_repository_has_no_outline_and_the_planner_refuses_typed(tmp_path: Path) -> None:
    (tmp_path / "repo").mkdir()
    inventory, texts = scan_repository(tmp_path / "repo", max_single_file_bytes=1024 * 1024)
    assert inventory.files == [] and texts == {}  # the pipeline raises EMPTY_REPOSITORY on exactly this
    ir = load_fixture("repo", root=tmp_path).ir
    _, arch, chapters = _shape(ir)
    assert chapters == [] and arch.flows == [] and arch.uncertainty == ["no entry points detected"]
    with pytest.raises(GenerationError) as ei:
        await _plan(ir)
    assert ei.value.code is ErrorCode.GENERATION_FAILED and "no chapter targets" in ei.value.message


def test_an_oversized_file_is_not_read_and_nothing_is_drawn_from_it(tmp_path: Path) -> None:
    big = b"value = 1\n" * 120_000  # 1.2 MB, over the 1 MiB single-file cap
    ir = _generated(tmp_path, {"big.py": big, "README.md": "# big\n"})
    rm, arch, chapters = _shape(ir)
    assert [f.path for f in ir.files] == ["README.md"]  # the file is neither parsed nor chunked
    assert (
        ir.stats["files_total"] == 2 and ir.stats["files_indexed"] == 1
    )  # the skip is recorded in the snapshot
    assert ir.symbols == [] and arch.flows == [] and arch.entry_points == []
    assert "no entry points detected" in arch.uncertainty
    assert all(rm.ref(t) is None or rm.ref(t).path != "big.py" for c in chapters for t in c.targets)  # type: ignore[union-attr]


def test_an_oversized_file_is_named_in_the_uncertainty(tmp_path: Path) -> None:
    ir = _generated(tmp_path, {"big.py": b"value = 1\n" * 120_000, "README.md": "# big\n"})
    uncertainty = build_architecture(ir).uncertainty
    assert any("big.py" in u and "size cap" in u for u in uncertainty)
    assert "no entry points detected" in uncertainty


def test_a_code_file_dropped_at_extraction_is_named_too(tmp_path: Path) -> None:
    """The archive extractor never writes an over-cap member, so the inventory only learns of it from the report."""
    root = tmp_path / "repo" / "p"
    root.mkdir(parents=True)
    (root / "app.py").write_text("def main():\n    return 1\n")
    inventory, texts = scan_repository(root, max_single_file_bytes=1024 * 1024)
    inventory.skipped.update({"huge.py": "oversized", "data.bin": "oversized", "link": "symlink"})
    stats = build_ir(
        ProjectIdentity(project_id="p", name="p", input_type="zip"),
        inventory,
        analyze_texts("p", inventory, texts),
        analysis_version="t",
    ).stats
    assert stats["oversized_code_files"] == ["huge.py"]  # a binary and a symlink are not code
