"""The API Lambda image installs the core and `ai` dependency groups and nothing else (`docker/backend.Dockerfile`).
Every third-party package the API process can import (statically, function-level imports included) must therefore be
in those groups or be a dependency of one of them. Packages of the local stack are exempt: the AWS profile imports
them nowhere (`test_aws_profile` proves it at run time) and the local API image installs them.

This is the check that would have caught the in-memory graph (networkx) living in the analysis-only group while chat
retrieval, which runs in the API function, needs it.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from importlib import metadata
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
SRC = BACKEND / "src" / "unfold"
LOCAL_STACK = {"sqlalchemy", "asyncpg", "alembic", "redis", "minio", "neo4j", "qdrant_client"}
ENTRY = ("unfold.api.aws", "unfold.api.main")


def _name(requirement: str) -> str:
    return re.split(r"[<>=!~\[ ;(]", requirement, maxsplit=1)[0].lower().replace("_", "-").replace(".", "-")


def _closure(roots: set[str]) -> set[str]:
    seen: set[str] = set()
    todo = list(roots)
    while todo:
        dist = todo.pop()
        if dist in seen:
            continue
        seen.add(dist)
        try:
            requires = metadata.requires(dist) or []
        except metadata.PackageNotFoundError:
            continue
        # skip extras-only requirements (`; extra == "..."`): the image does not install extras it did not ask for
        todo += [_name(r) for r in requires if "extra ==" not in r]
    return seen


def _imports() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    internal: dict[str, set[str]] = {}
    external: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        mod = ".".join(path.relative_to(SRC.parent).with_suffix("").parts).removesuffix(".__init__")
        mine: set[str] = set()
        theirs: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module, *[f"{node.module}.{a.name}" for a in node.names]]
            else:
                continue
            for n in names:
                (mine if n.startswith("unfold") else theirs).add(n)
        internal[mod], external[mod] = mine, theirs
    return internal, external


def test_the_api_image_holds_everything_the_api_can_import() -> None:
    project = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    declared = {_name(r) for r in project["project"]["dependencies"]}
    declared |= {_name(r) for r in project["dependency-groups"]["ai"]}
    installed = _closure(declared)

    internal, external = _imports()
    seen: set[str] = set()
    todo = list(ENTRY)
    while todo:
        mod = todo.pop()
        if mod in seen:
            continue
        seen.add(mod)
        todo += [i for i in internal.get(mod, ()) if i in internal]

    owners = metadata.packages_distributions()
    missing: dict[str, set[str]] = {}
    for mod in seen:
        for imp in external.get(mod, ()):
            root = imp.split(".")[0]
            if root in sys.stdlib_module_names or root == "__future__" or root in LOCAL_STACK:
                continue
            dists = {_name(d) for d in owners.get(root, [])}
            if not dists & installed:
                missing.setdefault(root, set()).add(mod)
    assert not missing, f"imported by the API but not in the api-lambda image groups: {missing}"
