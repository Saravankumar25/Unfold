from __future__ import annotations

from unfold.core.ids import entity_id
from unfold.domain.models import Provenance
from unfold.project_ir.complexity import MAX_READ_MINUTES, MIN_READ_MINUTES, score
from unfold.project_ir.schema import (
    AIComponentIR,
    DataStoreIR,
    EndpointIR,
    FileIR,
    InfraResourceIR,
    ProjectIdentity,
    ProjectIR,
    RelationshipIR,
    SchemaIR,
    ServiceIR,
)

PID = "33333333-3333-3333-3333-333333333333"
P = Provenance(analyzer="test")


def _files(n: int, lines: int, languages: tuple[str, ...] = ("python",)) -> list[FileIR]:
    out = []
    for i in range(n):
        lang = languages[i % len(languages)]
        path = f"pkg{i % 40}/m{i}.{lang[:2]}"
        out.append(
            FileIR(
                id=entity_id(PID, "file", path),
                name=path,
                path=path,
                language=lang,
                lines=lines,
                provenance=P,
            )
        )
    return out


def _ir(**kw: object) -> ProjectIR:
    return ProjectIR(
        analysis_version="v1",
        project=ProjectIdentity(project_id=PID, name="x", input_type="zip"),
        **kw,  # type: ignore[arg-type]
    )


def _rels(files: list[FileIR], n: int) -> list[RelationshipIR]:
    return [
        RelationshipIR(
            source_id=files[i % len(files)].id,
            target_id=files[(i * 7 + 1) % len(files)].id,
            type="IMPORTS",
            provenance=P,
        )
        for i in range(n)
    ]


def test_tiny_repository_is_small_with_minimal_investigators() -> None:
    files = _files(4, 60)
    profile = score(_ir(files=files))
    assert profile.level == "small"
    assert profile.investigators == ["architecture", "workflow"]
    assert profile.target_read_minutes == MIN_READ_MINUTES
    assert profile.agent_budget.max_llm_calls < 10
    assert profile.signals.code_files == 4


def test_non_code_files_do_not_count_as_code() -> None:
    files = _files(3, 100, ("markdown",))
    profile = score(_ir(files=files))
    assert profile.signals.code_files == 0
    assert profile.investigators == ["architecture"]


def test_medium_web_app_with_data_and_ai() -> None:
    files = _files(400, 150, ("python", "typescript"))
    p = Provenance(analyzer="test")
    ir = _ir(
        files=files,
        relationships=_rels(files, 3000),
        endpoints=[
            EndpointIR(id=f"endpoint:{i}", name=f"GET /x{i}", method="GET", route=f"/x{i}", provenance=p)
            for i in range(25)
        ],
        schemas=[SchemaIR(id=f"schema:{i}", name=f"S{i}", schema_kind="orm_model", provenance=p) for i in range(8)],
        data_stores=[DataStoreIR(id="datastore:pg", name="pg", technology="postgresql", category="relational", provenance=p)],
        services=[
            ServiceIR(id="service:api", name="api", service_kind="api", image="x/api", provenance=p),
            ServiceIR(id="service:db", name="db", service_kind="database", image="postgres", provenance=p),
        ],
        ai_components=[
            AIComponentIR(id="ai:llm", name="openai", component_kind="llm_client", technology="openai", provenance=p)
        ],
    )  # fmt: skip
    profile = score(ir)
    assert profile.level == "medium"
    assert profile.investigators == [
        "architecture",
        "workflow",
        "data",
        "ai_ml",
        "infrastructure",
        "security",
    ]
    assert MIN_READ_MINUTES < profile.target_read_minutes < MAX_READ_MINUTES
    assert profile.retrieval_breadth.candidate_k == 40
    assert profile.agent_budget.max_tool_calls == 24


def test_large_platform_gets_every_investigator_and_bigger_budgets() -> None:
    files = _files(6000, 120, ("python", "go", "typescript", "java", "rust"))
    p = Provenance(analyzer="test")
    ir = _ir(
        files=files,
        relationships=_rels(files, 120_000),
        endpoints=[
            EndpointIR(id=f"endpoint:{i}", name=f"GET /x{i}", method="GET", route=f"/x{i}", provenance=p)
            for i in range(80)
        ],
        services=[
            ServiceIR(id=f"service:{i}", name=f"s{i}", service_kind="api", image=f"img{i}", provenance=p)
            for i in range(10)
        ],
        data_stores=[
            DataStoreIR(id=f"datastore:{i}", name=f"d{i}", technology=f"t{i}", category="relational", provenance=p)
            for i in range(4)
        ],
        infrastructure=[
            InfraResourceIR(id=f"infra:{i}", name=f"r{i}", resource_kind="k8s_deployment", provenance=p)
            for i in range(20)
        ],
        ai_components=[
            AIComponentIR(id=f"ai:{i}", name=f"a{i}", component_kind="embedding", provenance=p) for i in range(6)
        ],
    )  # fmt: skip
    profile = score(ir)
    assert profile.level == "large"
    assert profile.score == 100.0
    assert profile.investigators == [
        "architecture",
        "workflow",
        "data",
        "ai_ml",
        "infrastructure",
        "security",
    ]
    assert profile.target_read_minutes == MAX_READ_MINUTES
    assert profile.retrieval_breadth.graph_depth == 2
    assert profile.agent_budget.max_llm_calls > 10


def test_score_is_deterministic_and_bounded() -> None:
    files = _files(120, 90)
    ir = _ir(files=files, relationships=_rels(files, 900))
    a, b = score(ir), score(ir)
    assert a == b
    assert 0.0 <= a.score <= 100.0
    assert MIN_READ_MINUTES <= a.target_read_minutes <= MAX_READ_MINUTES


def test_uses_builder_stats_when_present() -> None:
    ir = _ir(files=_files(2, 10), stats={"code_files": 900, "code_lines": 150_000, "code_languages": 3})
    profile = score(ir)
    assert profile.signals.code_files == 900
    assert profile.signals.code_lines == 150_000
    assert profile.level == "medium"
