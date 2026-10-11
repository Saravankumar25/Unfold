#!/usr/bin/env python3
"""The quality gate: the single definition of "green" (LLD §24). Cross-platform, no dependencies.

    python scripts/check.py                 # static checks + unit/security tests + offline evals + frontend
    python scripts/check.py --integration   # also integration tests (needs `docker compose up -d`)
    python scripts/check.py --only backend  # or: frontend, infra

Exit code is non-zero if any step fails; a summary table is printed at the end.
"""

# ruff: noqa: T201, S603
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"


def _npm() -> str:
    return shutil.which("npm.cmd") or shutil.which("npm") or "npm"


def steps(args: argparse.Namespace) -> list[tuple[str, list[str], Path]]:
    uv = ["uv", "run"]
    out: list[tuple[str, list[str], Path]] = []
    if args.only in (None, "backend"):
        out += [
            ("ruff lint", [*uv, "ruff", "check", "src", "tests"], BACKEND),
            ("ruff format", [*uv, "ruff", "format", "--check", "src", "tests"], BACKEND),
            ("mypy (strict)", [*uv, "mypy"], BACKEND),
            ("unit + security + contract tests", [*uv, "pytest"], BACKEND),
            ("migrations in sync", [*uv, "alembic", "check"], BACKEND),
        ]
        out.append(
            ("offline evals (gated)", [*uv, "python", "-m", "unfold.evaluation.offline", "--gate"], BACKEND)
        )
        if args.integration:
            out.append(("integration tests", [*uv, "pytest", "-m", "integration"], BACKEND))
    if args.only in (None, "infra") and shutil.which("terraform"):
        out += [
            ("terraform fmt", ["terraform", "fmt", "-check", "-recursive", "infra/terraform"], ROOT),
            # offline plans of every root plus the violating fixtures (first run downloads the AWS provider)
            ("infra policy tests", [*uv, "pytest", "../infra/policy"], BACKEND),
        ]
    if args.only in (None, "frontend") and (FRONTEND / "package.json").exists():
        npm = _npm()
        out += [
            ("frontend lint", [npm, "run", "lint"], FRONTEND),
            ("frontend typecheck", [npm, "run", "typecheck"], FRONTEND),
            ("frontend tests", [npm, "test"], FRONTEND),
            ("frontend build", [npm, "run", "build"], FRONTEND),
        ]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--integration", action="store_true")
    parser.add_argument("--only", choices=["backend", "frontend", "infra"])
    parser.add_argument("--keep-going", action="store_true", help="run all steps even after a failure")
    args = parser.parse_args()

    results: list[tuple[str, bool, float]] = []
    for name, cmd, cwd in steps(args):
        print(f"\n=== {name}: {' '.join(cmd)}  (cwd={cwd.relative_to(ROOT)})", flush=True)
        start = time.monotonic()
        ok = subprocess.run(cmd, cwd=cwd).returncode == 0
        results.append((name, ok, time.monotonic() - start))
        if not ok and not args.keep_going:
            break

    print("\n" + "-" * 60)
    for name, ok, secs in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<40} {secs:6.1f}s")
    failed = [n for n, ok, _ in results if not ok]
    print("-" * 60)
    print("GREEN" if not failed else f"RED: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
