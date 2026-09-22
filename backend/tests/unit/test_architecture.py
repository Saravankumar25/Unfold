"""Architecture rules from HLD §3 / backend/CLAUDE.md, enforced on the import graph (static, via ast)."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "unfold"


def _modules() -> dict[str, set[str]]:
    """module dotted name -> set of imported absolute module names."""
    out: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC.parent).with_suffix("")
        name = ".".join(rel.parts).removesuffix(".__init__")
        imports: set[str] = set()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imports.add(node.module)
        out[name] = imports
    return out


MODULES = _modules()


def _pkg(mod: str) -> str:
    parts = mod.split(".")
    return parts[1] if len(parts) > 1 else ""


def test_core_and_domain_are_leaf_layers() -> None:
    allowed = {"core", "domain"}
    for mod, imports in MODULES.items():
        if _pkg(mod) not in allowed:
            continue
        for imp in imports:
            if imp.startswith("unfold."):
                target = _pkg(imp)
                assert target in allowed or imp == "unfold.security.secrets", f"{mod} imports {imp}"


def test_provider_sdks_only_in_llm_providers() -> None:
    for mod, imports in MODULES.items():
        if mod.startswith("unfold.llm.providers"):
            continue
        for imp in imports:
            assert not imp.startswith(("google.genai", "google.generativeai", "groq", "openai")), (
                f"{mod} imports {imp}"
            )


def test_only_composition_roots_import_api_or_worker_entrypoints() -> None:
    for mod, imports in MODULES.items():
        if _pkg(mod) in {"api", "worker"}:
            continue
        for imp in imports:
            assert not imp.startswith(("unfold.api.main", "unfold.api.routes", "unfold.worker.settings")), (
                f"{mod} imports composition root {imp}"
            )


def test_no_subprocess_or_eval_of_repository_content() -> None:
    """Analysis must never execute repository code (PRD §19)."""
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                names = [a.name for a in node.names] + (
                    [node.module] if isinstance(node, ast.ImportFrom) else []
                )
                assert "subprocess" not in names, f"{path} imports subprocess"
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"eval", "exec"}
            ):
                raise AssertionError(f"{path} calls {node.func.id}")
