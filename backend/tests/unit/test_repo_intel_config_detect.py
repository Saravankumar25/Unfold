"""Config analyzer, technology detection and claim grading — pure, no stores."""

from __future__ import annotations

import pytest

from unfold.domain.enums import ClaimKind, ClaimStatus
from unfold.domain.models import ClaimRecord
from unfold.guide.generate import grade_claims, normalise_plan
from unfold.guide.models import GuidePlan, GuideSectionPlan
from unfold.repo_intel.analyzers.config import ConfigAnalyzer
from unfold.repo_intel.detect import detect
from unfold.repo_intel.models import FileAnalysis, ImportRef, InventoryFile

A = ConfigAnalyzer()


def _file(path: str, ext: str | None = None) -> InventoryFile:
    return InventoryFile(
        path=path, size=10, ext=ext if ext is not None else path.rsplit(".", 1)[-1], sha256="x"
    )


def _run(path: str, text: str) -> FileAnalysis:
    f = _file(path)
    assert A.supports(f), path
    return A.analyze(f, text, None)  # type: ignore[arg-type]


def test_pyproject_dependencies_and_scripts() -> None:
    fa = _run(
        "pyproject.toml",
        '[project]\nname="x"\ndependencies=["fastapi>=0.1", "qdrant-client[async]>=1"]\n'
        '[project.optional-dependencies]\ndev=["pytest"]\n[project.scripts]\nrun="app.cli:main"\n',
    )
    deps = {(d.name, d.dev) for d in fa.dependencies}
    assert deps == {("fastapi", False), ("qdrant-client", False), ("pytest", True)}
    assert fa.scripts == {"run": "app.cli:main"}


def test_compose_services_resolve_build_context_and_never_keep_env_values() -> None:
    fa = _run(
        "deploy/docker-compose.yml",
        "services:\n  api:\n    build: ../api\n    ports: ['8000:8000']\n    environment:\n      SECRET_TOKEN: hunter2\n"
        "    depends_on: [db]\n  db:\n    image: postgres:16\n",
    )
    api = next(s for s in fa.compose_services if s.name == "api")
    assert api.build_context == "api" and api.depends_on == ["db"]
    assert api.env == ["SECRET_TOKEN"] and "hunter2" not in repr(fa)
    assert next(s for s in fa.compose_services if s.name == "db").image == "postgres:16"


def test_terraform_and_kubernetes_become_infra() -> None:
    tf = _run("infra/main.tf", 'resource "aws_db_instance" "main" {\n  engine = "postgres"\n}\n')
    assert {i.resource_kind for i in tf.infra} >= {"terraform", "aws_db_instance:postgres"}
    k8s = _run(
        "k8s/api.yaml",
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: api\nspec:\n  template:\n    spec:\n"
        "      containers:\n        - name: api\n          image: ghcr.io/acme/api:1\n",
    )
    res = k8s.infra[0]
    assert (res.resource_kind, res.name) == ("kubernetes", "api")
    assert res.details["images"] == ["ghcr.io/acme/api:1"]


def test_malformed_manifest_is_an_error_not_a_crash() -> None:
    fa = _run("package.json", "{not json")
    assert fa.errors and not fa.dependencies


def test_detection_from_dependencies_imports_and_images() -> None:
    manifest = _run("pyproject.toml", '[project]\nname="x"\ndependencies=["fastapi", "arq"]\n')
    code = FileAnalysis(
        path="app/main.py",
        analyzer="python",
        imports=[ImportRef(module="qdrant_client.models", line=3, external=True)],
    )
    compose = _run(
        "docker-compose.yml", "services:\n  db:\n    image: docker.io/library/postgres:16-alpine\n"
    )
    det = detect({"pyproject.toml": manifest, "app/main.py": code, "docker-compose.yml": compose})
    assert "fastapi" in {d.technology for d in det.frameworks}
    assert {"qdrant", "postgresql"} <= {d.technology for d in det.datastores}
    assert "redis" in {d.technology for d in det.datastores}  # arq implies redis
    assert "arq" in {d.technology for d in det.queues}
    assert any(c.category == "api_service" for c in det.categories)


def _claim(text: str, kind: ClaimKind, ids: list[str]) -> ClaimRecord:
    return ClaimRecord(text=text, kind=kind, evidence_ids=ids)


def test_claims_are_graded_against_the_cited_text_only() -> None:
    texts = {"c1": "def verify_token(token): return jwt.decode(token)"}
    graded, quality = grade_claims(
        [
            _claim("`verify_token` decodes the JWT", ClaimKind.PROJECT_FACT, ["c1"]),
            _claim("`RefreshRotationService` rotates tokens", ClaimKind.PROJECT_FACT, ["c1"]),
            _claim("It is stateless", ClaimKind.PROJECT_FACT, []),
            _claim("This implies horizontal scaling", ClaimKind.INFERENCE, ["c1"]),
            _claim("Add rate limiting", ClaimKind.IMPROVEMENT, []),
            _claim("Cites a chunk that is not stored", ClaimKind.PROJECT_FACT, ["gone"]),
        ],
        texts,
    )
    assert [g.status for g in graded] == [
        ClaimStatus.VERIFIED,
        ClaimStatus.INFERRED,  # names a class the evidence does not contain
        ClaimStatus.UNSUPPORTED,
        ClaimStatus.STRONGLY_INFERRED,
        ClaimStatus.PROPOSED,
        ClaimStatus.UNSUPPORTED,
    ]
    assert quality["claims"] == 6 and quality["verified"] == 1 and quality["unsupported"] == 2
    assert quality["grounded_ratio"] == pytest.approx(0.2)


def test_plan_normalisation_makes_keys_unique_and_rejects_tiny_plans() -> None:
    def sec(key: str) -> GuideSectionPlan:
        return GuideSectionPlan(key=key, title="Same Title", kind="core", purpose="p", target_minutes=3)

    plan = normalise_plan(GuidePlan(estimated_read_minutes=1, sections=[sec(""), sec(""), sec("Overview!")]))
    assert [s.key for s in plan.sections] == ["same-title", "same-title-2", "overview"]
    with pytest.raises(Exception, match="at least"):
        normalise_plan(GuidePlan(estimated_read_minutes=1, sections=[sec("a")]))


# --- hostile input: nothing here may hang or fail the job --------------------------------------------------


def _under(seconds: float, fn: object) -> None:
    import time

    start = time.perf_counter()
    fn()  # type: ignore[operator]
    assert time.perf_counter() - start < seconds


def test_malformed_and_deeply_nested_files_are_recorded_not_raised() -> None:
    assert _run("main.tf", 'resource "a" "b" {{{ not hcl').errors
    assert _run("package.json", "[" * 200_000).errors
    assert _run("k8s/x.yaml", "apiVersion: v1\nkind: [unclosed").errors


def test_yaml_alias_bomb_is_bounded() -> None:
    refs = ", ".join(["*a"] * 9)
    lines = ["apiVersion: v1", "kind: Bomb", "x0: &a0 [image-less]"]
    for i in range(1, 10):
        lines.append(f"x{i}: &a{i} [{', '.join(f'*a{i - 1}' for _ in range(9))}]")
    del refs
    _under(2.0, lambda: _run("k8s/bomb.yaml", "\n".join(lines) + "\n"))


def test_markdown_headings_do_not_backtrack() -> None:
    nasty = "# a" + " " * 5000 + "x\n" + "#" * 300 + " not a heading\n# Real\n"
    fa = A.analyze(_file("README.md"), nasty, None)  # type: ignore[arg-type]
    _under(1.0, lambda: A.analyze(_file("README.md"), nasty * 20, None))  # type: ignore[arg-type]
    assert [s.title for s in fa.sections][-1] == "Real"


def test_secret_and_injection_scanners_are_not_quadratic() -> None:
    from unfold.security.injection import scan
    from unfold.security.secrets import redact

    _under(3.0, lambda: redact("-----BEGIN PRIVATE KEY-----\n" * 8000))
    _under(3.0, lambda: scan("\n" * 100_000 + "system: hi"))


def test_claim_with_nothing_checkable_is_not_verified_and_names_match_whole_words() -> None:
    texts = {"c1": "class UserNameStore: pass"}
    graded, _ = grade_claims(
        [
            _claim("The service handles requests through a queue", ClaimKind.PROJECT_FACT, ["c1"]),
            _claim(
                "`user` is stored", ClaimKind.PROJECT_FACT, ["c1"]
            ),  # `user` is only a prefix of UserNameStore
        ],
        texts,
    )
    assert graded[0].status is ClaimStatus.STRONGLY_INFERRED and graded[0].confidence == 0.5
    assert graded[1].status is ClaimStatus.INFERRED
