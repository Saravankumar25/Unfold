from __future__ import annotations

import itertools

import pytest

from unfold.core.errors import InputError
from unfold.core.ids import chunk_id, entity_id
from unfold.domain.enums import PIPELINE, InvalidTransition, JobStage, check_transition
from unfold.ingestion.github import parse_github_url


@pytest.mark.parametrize(
    ("url", "owner", "repo", "ref"),
    [
        ("https://github.com/tiangolo/fastapi", "tiangolo", "fastapi", None),
        ("https://github.com/tiangolo/fastapi.git", "tiangolo", "fastapi", None),
        ("https://github.com/a-b/c.d/tree/release/1.0", "a-b", "c.d", "release/1.0"),
        ("  https://www.github.com/x/y/  ", "x", "y", None),
    ],
)
def test_parse_valid(url: str, owner: str, repo: str, ref: str | None) -> None:
    r = parse_github_url(url)
    assert (r.owner, r.repo, r.ref) == (owner, repo, ref)


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a/b",
        "https://gitlab.com/a/b",
        "https://github.com/a",
        "https://user:pw@github.com/a/b",
        "https://github.com:8443/a/b",
        "https://github.com/a/b/blob/main/x.py",
        "https://github.com/a/b/tree/../../x",
        "https://github.com.evil.com/a/b",
        "file:///etc/passwd",
    ],
)
def test_parse_invalid(url: str) -> None:
    with pytest.raises(InputError):
        parse_github_url(url)


def test_stable_ids() -> None:
    assert entity_id("p", "func", "a.py", "f") == entity_id("p", "func", "a.py", "f")
    assert entity_id("p", "func", "a.py", "f") != entity_id("q", "func", "a.py", "f")
    assert chunk_id("p", "a.py", 1, 5, "function").startswith("ch:")


def test_state_machine_forward_only() -> None:
    for a, b in itertools.pairwise(PIPELINE):
        check_transition(a, b)
    check_transition(JobStage.INDEXING_DENSE, JobStage.INDEXING_DENSE)  # resume
    check_transition(JobStage.INGESTING, JobStage.FAILED_INGESTION)
    with pytest.raises(InvalidTransition):
        check_transition(JobStage.BUILDING_GRAPH, JobStage.INGESTING)
    with pytest.raises(InvalidTransition):
        check_transition(JobStage.READY, JobStage.FINALIZING)
    assert JobStage.BUILDING_GRAPH.user_label == "Building project knowledge graph"
