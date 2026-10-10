"""`MemoryGraph` on a hand-built graph (parity with Neo4j over real fixtures is an integration test)."""

from __future__ import annotations

import pytest

from unfold.graph.memory import MemoryGraph, _Node, _tokens
from unfold.graph.models import GraphValidationError

PID = "p1"


def _graph() -> MemoryGraph:
    def n(uid: str, label: str, name: str, **props: object) -> _Node:
        return _Node(uid, [label], {"name": name, "project_id": PID, "source": "deterministic", **props})

    nodes = {
        x.uid: x
        for x in [
            n("project:p1", "Project", "demo"),
            n("m:a", "Module", "a", path="a"),
            n("m:b", "Module", "b", path="b"),
            n("f:a1", "File", "a1.py", path="a/a1.py", module_id="m:a"),
            n("f:b1", "File", "b1.py", path="b/b1.py", module_id="m:b"),
            n("s:one", "Function", "one", qualname="a1.one", path="a/a1.py", start_line=3),
            n("s:two", "Function", "two", qualname="b1.two", path="b/b1.py", start_line=1),
            n("s:three", "Function", "three", qualname="b1.three", path="b/b1.py", start_line=9),
            n("e:get", "APIEndpoint", "GET /x", path="a/a1.py", route="/x", method="GET"),
            n("d:db", "DataStore", "postgres"),
        ]
    }
    edges = {
        ("CALLS", "s:one", "s:two"): {"source": "deterministic", "confidence": 1.0},
        ("CALLS", "s:two", "s:three"): {"source": "inferred", "confidence": 0.5},
        ("IMPORTS", "f:a1", "f:b1"): {"source": "deterministic", "confidence": 1.0},
        ("HANDLES", "s:one", "e:get"): {"source": "deterministic", "confidence": 1.0},
        ("USES", "s:three", "d:db"): {"source": "deterministic", "confidence": 1.0},
        ("CONTAINS", "project:p1", "m:a"): {"source": "deterministic", "confidence": 1.0},
    }
    return MemoryGraph(PID, nodes, edges)


def test_tokens_follow_the_standard_analyzer_rules() -> None:
    assert _tokens("HybridRetriever.__init__") == ["hybridretriever", "__init__"]
    assert _tokens("login.py") == ["login.py"]
    assert _tokens("foo-bar baz_qux") == ["foo", "bar", "baz_qux"]


async def test_callers_callees_are_directed_and_ranked_by_distance() -> None:
    g = _graph()
    assert [(n.uid, n.score) for n in await g.callees(PID, "s:one", depth=2)] == [
        ("s:two", 1.0),
        ("s:three", 2.0),
    ]
    assert [n.uid for n in await g.callers(PID, "s:three", depth=3)] == ["s:two", "s:one"]
    assert await g.callers(PID, "s:one") == []


async def test_neighbors_exclude_project_and_center_and_report_edges_among_the_result() -> None:
    g = _graph()
    hood = await g.neighbors(PID, "m:a", depth=1)  # only edge is CONTAINS from the Project node
    assert hood.center is not None and hood.nodes == []
    hood = await g.neighbors(PID, "s:two", depth=1, limit=10)
    assert {n.uid for n in hood.nodes} == {"s:one", "s:three"}
    assert {(e.source, e.target, e.type) for e in hood.edges} == {
        ("s:one", "s:two", "CALLS"),
        ("s:two", "s:three", "CALLS"),
    }
    with pytest.raises(GraphValidationError):
        await g.neighbors(PID, "s:two", rel_types=["NOT_A_REL"])


async def test_other_project_ids_see_nothing() -> None:
    g = _graph()
    assert await g.get_node("other", "s:one") is None
    assert (await g.neighbors("other", "s:one")).center is None
    assert await g.find_entities("other", ["one"]) == []


async def test_path_overview_module_dependencies_and_endpoint_flows() -> None:
    g = _graph()
    path = await g.path_between(PID, "e:get", "d:db", max_hops=6)
    assert path is not None
    assert [n.uid for n in path.nodes] == ["e:get", "s:one", "s:two", "s:three", "d:db"]
    assert path.relationships == ["HANDLES", "CALLS", "CALLS", "USES"]
    assert await g.path_between(PID, "e:get", "d:db", max_hops=2) is None
    assert await g.path_between(PID, "s:one", "s:one") is None

    ov = await g.overview(PID)
    assert ov.node_counts["Function"] == 3 and ov.relationship_counts["CALLS"] == 2
    assert ov.top_nodes[0].node.uid in {"s:two", "s:three", "s:one"}
    assert all(t.node.label not in {"Project", "Library"} for t in ov.top_nodes)

    deps = await g.module_dependencies(PID)
    assert [(d.source_uid, d.target_uid, d.weight) for d in deps] == [("m:a", "m:b", 1)]

    flows = await g.endpoint_flows(PID)
    assert len(flows) == 1 and flows[0].handler is not None and flows[0].handler.uid == "s:one"
    assert [c.uid for c in flows[0].callees] == ["s:two", "s:three"]
    assert [t.uid for t in flows[0].touches] == ["d:db"]


async def test_find_entities_exact_beats_case_insensitive_beats_fulltext() -> None:
    g = _graph()
    found = await g.find_entities(PID, ["one"], limit=20)
    assert found[0].uid == "s:one" and found[0].score == 3.0
    ci = await g.find_entities(PID, ["ONE"], limit=20)
    assert ci[0].uid == "s:one" and ci[0].score == 2.0
    ft = await g.find_entities(PID, ["b1.two"], limit=20)  # qualname exact -> 2.0
    assert ft[0].uid == "s:two"


def test_graph_backend_setting_selects_the_read_side() -> None:
    from types import SimpleNamespace

    from unfold.graph.queries import Neo4jGraphQueries, graph_queries_for

    rt = SimpleNamespace(settings=SimpleNamespace(graph_backend="neo4j"), neo4j=object(), extras={})
    assert isinstance(graph_queries_for(rt, None), Neo4jGraphQueries)  # type: ignore[arg-type]
    rt.settings.graph_backend = "off"
    assert graph_queries_for(rt, None) is None  # type: ignore[arg-type]
    rt.settings.graph_backend = "memory"
    assert graph_queries_for(rt, None) is None  # type: ignore[arg-type]  # no IR: recorded, retrieval degrades
