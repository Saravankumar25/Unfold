"""Adaptive planner v2 (task 3.4): skeleton per project kind, validation, typed fallback, v1 plans still load."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from unfold.core.errors import ErrorCode, GenerationError, UnfoldError
from unfold.evaluation.corpus import load_fixture
from unfold.guide.models import PLANNER_VERSION, ChapterPlan, GuidePlan
from unfold.guide.plan import (
    MAX_CHAPTERS,
    MIN_CHAPTERS,
    ChapterRefinement,
    PlanRefinement,
    build_skeleton,
    chapter_budget,
    plan_chapters,
    validate_chapters,
)
from unfold.llm.schemas import LLMRequest
from unfold.project_ir.schema import ProjectIR
from unfold.security.secrets import REDACTED
from unfold.understand.architecture import build_architecture
from unfold.understand.models import ArchitectureModel
from unfold.understand.repomap import RepoMap

TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2

CLI = {
    "requirements.txt": "click==8.1.7\n",
    "mycli/__init__.py": "",
    "mycli/main.py": (
        "import click\n\nfrom mycli.core import run\n\n\n@click.group()\ndef cli():\n    pass\n\n\n"
        "@cli.command()\ndef build():\n    run()\n"
    ),
    "mycli/core.py": "def run():\n    return helper()\n\n\ndef helper():\n    return 1\n",
    "tests/test_core.py": "from mycli.core import run\n\n\ndef test_run():\n    assert run() == 1\n",
}
MONOREPO = {
    **{
        f"packages/{p}/{f}": text
        for p in ("alpha", "beta", "gamma")
        for f, text in (
            ("requirements.txt", "requests==2.31.0\n"),
            (f"{p}/__init__.py", ""),
            (f"{p}/core.py", f"def {p}_run():\n    return 1\n"),
        )
    },
}


def _ir(tmp_path: Path, files: dict[str, str]) -> ProjectIR:
    root = tmp_path / "repo"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return load_fixture("repo", root=tmp_path).ir


def _parts(ir: ProjectIR) -> tuple[RepoMap, ArchitectureModel]:
    return RepoMap(ir), build_architecture(ir)


def _valid(rm: RepoMap, arch: ArchitectureModel):  # type: ignore[no-untyped-def]
    ids = {c.id for c in arch.components}
    return lambda t: t in ids or rm.ref(t) is not None


class _LLM:
    """Gateway double: answers the planner with a fixed refinement or raises."""

    def __init__(self, answer: PlanRefinement | Exception) -> None:
        self.answer = answer
        self.requests: list[LLMRequest] = []

    async def generate_structured(self, req: LLMRequest, schema: type[Any]) -> Any:
        self.requests.append(req)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


async def _plan(llm: _LLM, ir: ProjectIR, facts: list[str] | None = None) -> tuple[GuidePlan, bool]:
    rm, arch = _parts(ir)
    return await plan_chapters(
        cast(Any, llm),
        ir,
        rm,
        arch,
        facts or [],
        technical_level="intermediate",
        instruction=None,
        job_id="j",
        project_id="p",
    )


def _echo(skeleton: list[ChapterPlan], **override: Any) -> PlanRefinement:
    return PlanRefinement(
        chapters=[
            ChapterRefinement(
                key=c.key, title=c.title, objective=c.objective, questions=list(c.questions), **override
            )
            for c in skeleton
        ]
    )


# ---- skeleton -----------------------------------------------------------------------------------------------


def test_skeleton_differs_by_project_kind_and_every_chapter_is_grounded(tmp_path: Path) -> None:
    cases = {
        "cli": _ir(tmp_path / "cli", CLI),
        "web_service": load_fixture("fastapi_rag").ir,
        "monorepo": _ir(tmp_path / "mono", MONOREPO),
    }
    keys: dict[str, list[str]] = {}
    for kind, ir in cases.items():
        rm, arch = _parts(ir)
        assert arch.project_kind == kind
        chapters = build_skeleton(ir, rm, arch)
        keys[kind] = [c.key for c in chapters]
        assert chapters[0].key == "overview" and 1 <= len(chapters) <= MAX_CHAPTERS
        assert validate_chapters(chapters, _valid(rm, arch)) == []
        for c in chapters:
            assert c.objective and 2 <= len(c.questions) <= 6 and c.targets
            assert c.purpose == c.objective  # v1 readers
        assert all(c.depends_on in ([], ["overview"]) for c in chapters)
    assert "usage" in keys["cli"] and "request-lifecycle" not in keys["cli"]
    assert "request-lifecycle" in keys["web_service"] and "usage" not in keys["web_service"]
    assert "packages" in keys["monorepo"]
    assert len({tuple(k) for k in keys.values()}) == 3


def test_a_service_with_a_frontend_and_docs_manifests_is_not_a_monorepo(tmp_path: Path) -> None:
    service = {
        "requirements.txt": "fastapi==0.110.0\n",
        "backend/requirements.txt": "fastapi==0.110.0\n",
        "frontend/package.json": '{"name": "web", "dependencies": {"react": "19.0.0"}}\n',
        "docs/requirements.txt": "mkdocs==1.5.0\n",
        "backend/app/main.py": (
            "from fastapi import FastAPI\n\napp = FastAPI()\n\n\n@app.get('/ping')\ndef ping():\n    return 1\n"
        ),
    }
    ir = _ir(tmp_path / "svc", service)
    assert len({d.manifest_path.rpartition("/")[0] for d in ir.dependencies}) >= 3  # the old rule fired here
    assert build_architecture(ir).project_kind == "web_service"


def test_a_workspace_with_a_marker_is_a_monorepo_and_two_loose_packages_are_not(tmp_path: Path) -> None:
    marked = {
        "pnpm-workspace.yaml": "packages:\n  - 'packages/*'\n",
        "package.json": '{"name": "root", "private": true}\n',
        "packages/a/package.json": '{"name": "a", "dependencies": {"react": "19.0.0"}}\n',
        "packages/b/package.json": '{"name": "b", "dependencies": {"react": "19.0.0"}}\n',
    }
    assert build_architecture(_ir(tmp_path / "marked", marked)).project_kind == "monorepo"
    two = {k: v for k, v in marked.items() if k != "pnpm-workspace.yaml"}
    assert build_architecture(_ir(tmp_path / "two", two)).project_kind != "monorepo"


def test_chapter_budget_follows_complexity_and_is_clamped() -> None:
    small = load_fixture("terraform_k8s").ir
    big = small.model_copy(update={"stats": {**small.stats, "code_files": 4000, "code_lines": 400_000}})
    assert MIN_CHAPTERS <= chapter_budget(small) <= chapter_budget(big) <= MAX_CHAPTERS


def test_skeleton_is_deterministic() -> None:
    ir = load_fixture("fastapi_rag").ir
    rm, arch = _parts(ir)
    assert build_skeleton(ir, rm, arch) == build_skeleton(ir, rm, arch)


# ---- validation ---------------------------------------------------------------------------------------------


def test_validation_names_unknown_targets_cycles_and_question_counts() -> None:
    def ch(key: str, **kw: Any) -> ChapterPlan:
        base: dict[str, Any] = {
            "key": key,
            "title": key,
            "kind": "core",
            "purpose": "p",
            "objective": "p",
            "target_minutes": 3,
            "questions": ["a?", "b?"],
            "targets": ["real"],
        }
        return ChapterPlan(**{**base, **kw})

    ok = lambda t: t == "real"  # noqa: E731
    assert validate_chapters([ch("a"), ch("b", depends_on=["a"])], ok) == []
    assert any("unknown target" in p for p in validate_chapters([ch("a", targets=["ghost"])], ok))
    assert any(
        "cycle" in p for p in validate_chapters([ch("a", depends_on=["b"]), ch("b", depends_on=["a"])], ok)
    )
    assert any("questions" in p for p in validate_chapters([ch("a", questions=["only one?"])], ok))
    assert any("no target" in p for p in validate_chapters([ch("a", targets=[])], ok))
    assert any("unknown dependency" in p for p in validate_chapters([ch("a", depends_on=["zzz"])], ok))


# ---- refinement and fallback --------------------------------------------------------------------------------


async def test_valid_refinement_is_applied() -> None:
    ir = load_fixture("fastapi_rag").ir
    rm, arch = _parts(ir)
    skeleton = build_skeleton(ir, rm, arch)
    refined = _echo(skeleton)
    refined.chapters[1] = refined.chapters[1].model_copy(
        update={"title": "From POST to answer", "targets": ["POST /query"]}
    )
    plan, degraded = await _plan(_LLM(refined), ir)
    assert not degraded
    assert plan.sections[1].title == "From POST to answer"
    handler = next(e for e in arch.entry_points if rm.entry_label(e.id) == "POST /query")
    assert plan.sections[1].targets == [handler.id]
    assert plan.estimated_read_minutes >= 1


@pytest.mark.parametrize(
    "bad",
    ["unknown_target", "cycle", "missing_key", "one_question"],
)
async def test_invalid_refinement_falls_back_to_the_skeleton_and_is_degraded(bad: str) -> None:
    ir = load_fixture("fastapi_rag").ir
    rm, arch = _parts(ir)
    skeleton = build_skeleton(ir, rm, arch)
    r = _echo(skeleton)
    if bad == "unknown_target":
        r.chapters[1] = r.chapters[1].model_copy(update={"targets": ["NoSuchSymbolAnywhere"]})
    elif bad == "cycle":
        r.chapters[1] = r.chapters[1].model_copy(update={"depends_on": [skeleton[2].key]})
        r.chapters[2] = r.chapters[2].model_copy(update={"depends_on": [skeleton[1].key]})
    elif bad == "missing_key":
        r = PlanRefinement(chapters=r.chapters[:-1])
    else:
        r.chapters[0] = r.chapters[0].model_copy(update={"questions": ["just one?"]})
    plan, degraded = await _plan(_LLM(r), ir)
    assert degraded
    assert plan.sections == skeleton  # nothing from the invalid answer survives


async def test_invalid_model_output_degrades_but_provider_errors_propagate() -> None:
    ir = load_fixture("fastapi_rag").ir
    invalid = GenerationError("bad json", code=ErrorCode.INVALID_MODEL_OUTPUT, retryable=True)
    plan, degraded = await _plan(_LLM(invalid), ir)
    assert degraded and plan.sections

    outage = GenerationError("down", code=ErrorCode.GENERATION_FAILED, retryable=True)
    with pytest.raises(GenerationError):
        await _plan(_LLM(outage), ir)


async def test_a_budget_refusal_degrades_to_the_skeleton() -> None:
    ir = load_fixture("fastapi_rag").ir
    refused = UnfoldError("budget", code=ErrorCode.LLM_BUDGET_EXHAUSTED, retryable=False)
    plan, degraded = await _plan(_LLM(refused), ir)
    rm, arch = _parts(ir)
    assert degraded and plan.sections == build_skeleton(ir, rm, arch)


async def test_facts_and_instruction_are_redacted_and_fenced_in_the_prompt() -> None:
    ir = load_fixture("fastapi_rag").ir
    rm, arch = _parts(ir)
    llm = _LLM(_echo(build_skeleton(ir, rm, arch)))
    await plan_chapters(
        cast(Any, llm),
        ir,
        rm,
        arch,
        [f"Config sets token = '{TOKEN}'"],
        technical_level="intermediate",
        instruction=f"focus on auth, my key is {TOKEN}",
        job_id="j",
        project_id="p",
    )
    req = llm.requests[0]
    prompt = req.messages[0].content
    assert TOKEN not in prompt and prompt.count(REDACTED) == 2
    assert "<repository_data" in prompt and "repository_data" in req.system


def test_v1_plan_rows_still_load() -> None:
    v1 = {
        "estimated_read_minutes": 9,
        "sections": [
            {
                "key": "overview",
                "title": "Overview",
                "kind": "backbone",
                "purpose": "What it is",
                "target_minutes": 3.0,
                "required_concepts": ["rag"],
                "evidence_objectives": ["app"],
                "visual_needs": ["architecture"],
                "depth": "standard",
                "related_subsystems": [],
            }
        ],
    }
    plan = GuidePlan.model_validate(v1)
    sec = plan.sections[0]
    assert (
        sec.objective == "What it is" and sec.questions == [] and sec.targets == [] and sec.depends_on == []
    )
    assert PLANNER_VERSION == "2"


def test_a_later_chapter_keeps_only_targets_no_earlier_chapter_owns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two chapters handed the same targets read the same code and write the same text (the `requests` guide's
    overview and internals did): the later chapter gets what is new, or everything when nothing is."""
    from unfold.guide import plan as plan_mod

    ir = _ir(tmp_path / "cli", CLI)
    rm, arch = _parts(ir)
    files = [r.id for r in rm.files()]
    assert len(files) >= 3
    monkeypatch.setitem(plan_mod._SELECT, "overview", lambda v: files[:1])
    monkeypatch.setitem(plan_mod._SELECT, "components", lambda v: files)
    internals = {c.key: c for c in build_skeleton(ir, rm, arch)}["internals"]
    assert internals.targets == files[1:] and files[0] not in internals.targets

    monkeypatch.setitem(plan_mod._SELECT, "overview", lambda v: files)
    internals = {c.key: c for c in build_skeleton(ir, rm, arch)}["internals"]
    assert internals.targets == files  # nothing new: its own questions are what set it apart
