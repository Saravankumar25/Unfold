from __future__ import annotations

from typing import Any

import pytest

from unfold.core.ids import entity_id
from unfold.domain.enums import RetrievalIntent, RetrievalMode
from unfold.domain.models import Provenance
from unfold.graph.models import SubsystemCandidate
from unfold.project_ir.schema import (
    DataStoreIR,
    EndpointIR,
    FileIR,
    ProjectIdentity,
    ProjectIR,
    SchemaIR,
    ServiceIR,
    SymbolIR,
)
from unfold.retrieval.planner import SubsystemHint, classify_intent, expansions_for, extract, plan_query
from unfold.retrieval.retrievers import StructuralRetriever

PID = "11111111-1111-1111-1111-111111111111"
PROV = Provenance(analyzer="test")


def _file(path: str, **kw: Any) -> FileIR:
    return FileIR(
        id=entity_id(PID, "file", path), name=path.rsplit("/", 1)[-1], path=path, provenance=PROV, **kw
    )


def _sym(path: str, qualname: str, kind: str = "function") -> SymbolIR:
    return SymbolIR(
        id=entity_id(PID, kind, path, qualname),
        name=qualname.rsplit(".", 1)[-1],
        qualname=qualname,
        kind=kind,  # type: ignore[arg-type]
        file_id=entity_id(PID, "file", path),
        path=path,
        start_line=1,
        end_line=10,
        provenance=PROV,
    )


def make_ir() -> ProjectIR:
    files = [
        _file("src/auth/service.py", language="python"),
        _file("src/api/routes.py", language="python"),
        _file("src/db/models.py", language="python"),
        _file("docker-compose.yml"),
    ]
    symbols = [
        _sym("src/auth/service.py", "verify_access_token"),
        _sym("src/auth/service.py", "AuthService", "class"),
        _sym("src/auth/service.py", "AuthService.login", "method"),
        _sym("src/api/routes.py", "upload_file"),
        _sym("src/api/routes.py", "run"),
        _sym("src/db/models.py", "User", "class"),
    ]
    handler = symbols[3]
    return ProjectIR(
        analysis_version="t1",
        project=ProjectIdentity(project_id=PID, name="demo", input_type="zip"),
        files=files,
        symbols=symbols,
        endpoints=[
            EndpointIR(
                id=entity_id(PID, "endpoint", "src/api/routes.py", "POST /api/upload"),
                name="POST /api/upload",
                method="POST",
                route="/api/upload/{kind}",
                handler_id=handler.id,
                path="src/api/routes.py",
                provenance=PROV,
            )
        ],
        schemas=[
            SchemaIR(
                id=entity_id(PID, "schema", "src/db/models.py", "User"),
                name="User",
                schema_kind="orm_model",
                symbol_id=symbols[5].id,
                fields=[{"name": "email"}, {"name": "password_hash"}],
                path="src/db/models.py",
                provenance=PROV,
            )
        ],
        services=[
            ServiceIR(
                id=entity_id(PID, "service", "docker-compose.yml", "api"),
                name="api",
                service_kind="api",
                path="docker-compose.yml",
                start_line=3,
                end_line=12,
                provenance=PROV,
            )
        ],
        data_stores=[
            DataStoreIR(
                id=entity_id(PID, "datastore", "docker-compose.yml", "postgres"),
                name="postgres",
                technology="postgresql",
                category="relational",
                path="docker-compose.yml",
                start_line=14,
                end_line=20,
                provenance=PROV,
            )
        ],
    )


# --- extraction -------------------------------------------------------------------------------------------------


def test_extracts_code_identifiers() -> None:
    ex = extract("Where is verifyAccessToken used by HybridRetriever and MAX_RETRY_COUNT or user_id?")
    assert ex.identifiers == ["verifyAccessToken", "HybridRetriever", "MAX_RETRY_COUNT", "user_id"]


def test_extracts_dotted_calls_backticks_paths_routes() -> None:
    ex = extract(
        "What does `auth/service.py` do in Service.run() and pkg.mod.func when POST /api/upload runs?"
    )
    assert "auth/service.py" in ex.paths
    assert "Service.run" in ex.identifiers
    assert "pkg.mod.func" in ex.identifiers
    assert "run" not in ex.identifiers  # the call is captured as the dotted name
    assert ex.routes == ["/api/upload"]
    assert ex.http_methods == ["POST"]


def test_ignores_urls_abbreviations_and_filenames_as_identifiers() -> None:
    ex = extract("see https://github.com/org/repo.py and e.g. config.yaml")
    assert ex.identifiers == []
    assert ex.paths == ["config.yaml"]
    assert "github" not in ex.terms and "e.g" not in ex.terms


def test_terms_drop_question_noise() -> None:
    ex = extract("Please explain where the retry logic is defined")
    assert ex.terms == ["retry", "logic"]


# --- intent ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "intent"),
    [
        ("Where is verifyAccessToken defined?", RetrievalIntent.IMPLEMENTATION_LOCATION),
        ("verifyAccessToken", RetrievalIntent.IMPLEMENTATION_LOCATION),
        ("Trace the upload flow end to end", RetrievalIntent.WORKFLOW),
        ("What happens when a request hits the API?", RetrievalIntent.WORKFLOW),
        ("Why is Redis used instead of memcached?", RetrievalIntent.DESIGN_REASONING),
        ("What happens if the database connection fails?", RetrievalIntent.FAILURE_ANALYSIS),
        ("Is there any SQL injection risk in the queries?", RetrievalIntent.SECURITY),
        ("Explain the high-level architecture and main components", RetrievalIntent.ARCHITECTURE),
        ("Give me an overview of this project", RetrievalIntent.OVERVIEW),
        ("Ask me an interview question about the cache", RetrievalIntent.INTERVIEW_QUESTION),
        ("What is reciprocal rank fusion?", RetrievalIntent.TECHNICAL_CONCEPT),
        ("", RetrievalIntent.OVERVIEW),
    ],
)
def test_intent_rules(query: str, intent: RetrievalIntent) -> None:
    assert classify_intent(query) is intent


def test_identifiers_do_not_trigger_intent_keywords() -> None:
    # `error_handler` is code, not a question about failures.
    assert classify_intent("Where is `error_handler` defined?") is RetrievalIntent.IMPLEMENTATION_LOCATION


# --- expansion ----------------------------------------------------------------------------------------------------


def test_expansion_table_is_bounded_and_intent_specific() -> None:
    assert expansions_for(["authentication"], ()) == [
        "login",
        "token",
        "jwt",
        "session",
        "password",
        "authenticate",
    ]
    db = expansions_for(["database"], ())
    assert {"model", "schema", "migration"} <= set(db)
    assert len(expansions_for(["auth", "db", "cache", "queue", "api"], ())) <= 12
    assert expansions_for(["hello", "world"], ()) == []


def test_expansions_skip_terms_already_present() -> None:
    assert "login" not in expansions_for(["login", "auth"], ())


# --- plans ------------------------------------------------------------------------------------------------------


def test_plan_without_ir() -> None:
    plan = plan_query(PID, "How does authentication work?", RetrievalMode.CHAT)
    assert plan.project_id == PID
    assert plan.intent is RetrievalIntent.TECHNICAL_CONCEPT
    assert plan.lexical_queries[0] == "How does authentication work?"
    assert "login token jwt session" in plan.lexical_queries[1]
    assert len(plan.semantic_queries) == 2
    assert plan.target_symbols == []
    assert plan.graph_seed_entities == ["authentication"]
    assert plan.desired_evidence_types == ["source", "docs"]
    assert plan.breadth == "medium"


def test_plan_matches_ir_symbols_and_paths() -> None:
    ir = make_ir()
    plan = plan_query(PID, "Where is verify_access_token and what does src/auth/service.py do?", ir=ir)
    target = entity_id(PID, "function", "src/auth/service.py", "verify_access_token")
    assert plan.target_symbols == [target]
    assert plan.graph_seed_entities[0] == "verify_access_token"
    assert plan.structural_filters["paths"] == ["src/auth/service.py"]
    assert plan.breadth == "narrow"


def test_plan_matches_dotted_qualname_and_plain_words() -> None:
    ir = make_ir()
    plan = plan_query(PID, "explain AuthService.login", ir=ir)
    assert plan.target_symbols == [entity_id(PID, "method", "src/auth/service.py", "AuthService.login")]
    plan2 = plan_query(PID, "how is the user stored", ir=ir)  # plain word matches the `User` class
    assert entity_id(PID, "class", "src/db/models.py", "User") in plan2.target_symbols


def test_plan_structural_hints_and_evidence_types() -> None:
    plan = plan_query(PID, "How is the app deployed with docker and configured?")
    kinds = plan.structural_filters["entity_kinds"]
    assert "infrastructure" in kinds and "services" in kinds
    assert "infra" in plan.desired_evidence_types and "config" in plan.desired_evidence_types


def test_plan_breadth_and_intent_override() -> None:
    plan = plan_query(PID, "Explain the architecture", RetrievalMode.GUIDE)
    assert plan.breadth == "broad"
    plan = plan_query(PID, "Explain the architecture", breadth="narrow", intent=RetrievalIntent.WORKFLOW)
    assert plan.breadth == "narrow"
    assert plan.intent is RetrievalIntent.WORKFLOW


def test_plan_targets_subsystems() -> None:
    subs = [
        SubsystemHint(id="subsystem:auth", name="Authentication", keywords=["login", "jwt"]),
        SubsystemCandidate(id="subsystem:db", label_hint="Persistence layer", key_files=["src/db/models.py"]),
        {"id": "subsystem:ui", "name": "Frontend"},
    ]
    plan = plan_query(PID, "How does login and session handling work?", subsystems=subs)
    assert plan.target_subsystems == ["subsystem:auth"]


def test_plan_is_deterministic() -> None:
    ir = make_ir()
    q = "Trace POST /api/upload through upload_file to storage"
    assert plan_query(PID, q, ir=ir) == plan_query(PID, q, ir=ir)


# --- structural references (pure part of the structural retriever) -------------------------------------------------


def test_structural_refs_route_path_and_kinds() -> None:
    ir = make_ir()
    plan = plan_query(PID, "Trace POST /api/upload/{kind} and which table stores the email", ir=ir)
    retriever = StructuralRetriever(ir, chunks=None)  # type: ignore[arg-type]  # plan_refs does no I/O
    refs = retriever.plan_refs(plan)
    handler = entity_id(PID, "function", "src/api/routes.py", "upload_file")
    endpoint = entity_id(PID, "endpoint", "src/api/routes.py", "POST /api/upload")
    route_refs = [r for r in refs if handler in r.ids]
    assert route_refs and endpoint in route_refs[0].ids
    schema_refs = [r for r in refs if entity_id(PID, "schema", "src/db/models.py", "User") in r.ids]
    assert schema_refs
    assert any(r.path == "docker-compose.yml" and r.start == 14 for r in refs)  # postgres data store
    # ranking: route (2.5) before overlap-ranked kinds
    assert refs.index(route_refs[0]) < refs.index(schema_refs[0])


def test_structural_refs_route_params_are_normalized() -> None:
    ir = make_ir()
    plan = plan_query(PID, "What does POST /api/upload/:type do?", ir=ir)
    retriever = StructuralRetriever(ir, chunks=None)  # type: ignore[arg-type]
    handler = entity_id(PID, "function", "src/api/routes.py", "upload_file")
    assert any(handler in r.ids for r in retriever.plan_refs(plan))
    plan_get = plan_query(PID, "What does GET /api/upload/:type do?", ir=ir)
    assert not any(handler in r.ids for r in retriever.plan_refs(plan_get))
