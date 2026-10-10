"""Regression (audit F-2): tree-sitter 0.26.0 corrupted memory while the TypeScript extractor ran (access violation
on Windows, exit 0xC0000005). Fixed by pinning `tree-sitter<0.26`. The inputs are committed copies of the two files
that crashed; each is analysed in a child process so a native crash fails this test instead of killing pytest.
Linux behaviour of 0.26.0 was not measured (Windows only), so the pin stays until a Linux run proves it safe.
"""

from __future__ import annotations

import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

_CHILD = "\n".join(
    [
        "import sys",
        "from pathlib import Path",
        "from unfold.repo_intel.analyzers.treesitter import TreeSitterAnalyzer",
        "from unfold.repo_intel.models import InventoryFile",
        "p = Path(sys.argv[1]); text = p.read_text(encoding='utf-8')",
        "f = InventoryFile(path=p.name, size=len(text), ext='ts', sha256='0' * 64, lines=text.count(chr(10)))",
        "fa = TreeSitterAnalyzer().analyze(f, text)",
        "print(len(fa.symbols), '|'.join(fa.errors))",
    ]
)


def test_tree_sitter_0_26_is_excluded() -> None:
    assert tuple(int(p) for p in version("tree-sitter").split(".")[:2]) < (0, 26)


@pytest.mark.parametrize("name", ["sse_stream.ts", "api_types.ts"])
def test_typescript_files_that_crashed_the_native_parser_analyse_cleanly(name: str) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, str(FIXTURES / name)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, f"native crash (exit {proc.returncode}): {proc.stderr[-400:]}"
    count, _, errors = proc.stdout.strip().partition(" ")
    assert int(count) > 0 and not errors
