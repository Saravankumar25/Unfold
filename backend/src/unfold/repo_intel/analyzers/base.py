"""Analyzer protocol + shared per-repository resolution context (LLD §7, ESD §8.2).

`LanguageAnalyzer.analyze(file, text, ctx)` turns one file's text into a `FileAnalysis`. `AnalysisContext` is built
once per repository (`build_context`) and answers "which repository file does this import refer to?" for Python
modules, JS/TS specifiers (relative paths + tsconfig `paths`) and Go import paths. It only ever reads text that was
already loaded; nothing here touches the filesystem or executes repository code.
"""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from unfold.repo_intel.models import FileAnalysis, Inventory, InventoryFile

# Languages whose files carry program structure (symbols/imports/calls). Structured-text analyzers (manifests,
# infra, docs, schema files) may run on such a file *in addition* to one code analyzer (e.g. `setup.py`).
PROGRAMMING_LANGUAGES: frozenset[str] = frozenset(
    {
        "python",
        "javascript",
        "typescript",
        "tsx",
        "jsx",
        "go",
        "java",
        "kotlin",
        "scala",
        "rust",
        "c",
        "cpp",
        "csharp",
        "ruby",
        "php",
        "swift",
        "dart",
        "elixir",
        "lua",
        "r",
        "julia",
        "haskell",
        "ocaml",
        "clojure",
        "groovy",
        "perl",
        "shell",
        "vue",
        "svelte",
        "zig",
        "objc",
    }
)

JS_EXTENSIONS: tuple[str, ...] = (
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".mts",
    ".cts",
    ".vue",
    ".svelte",
)
_JS_EMITTED_TO_SOURCE: dict[str, tuple[str, ...]] = {
    ".js": (".ts", ".tsx"),
    ".jsx": (".tsx",),
    ".mjs": (".mts",),
    ".cjs": (".cts",),
}
_PY_EXTENSIONS = (".py", ".pyi")
_PY_PROJECT_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile")


@runtime_checkable
class LanguageAnalyzer(Protocol):
    """One analyzer: decides which inventory files it handles and extracts facts from their text."""

    name: str

    def supports(self, file: InventoryFile) -> bool: ...

    def analyze(self, file: InventoryFile, text: str, ctx: AnalysisContext) -> FileAnalysis: ...


@dataclass(frozen=True)
class PathAlias:
    """A tsconfig/jsconfig `compilerOptions.paths` entry, already made repository-relative."""

    base_dir: str  # directory of the tsconfig that declares it ("" = repository root)
    pattern: str  # e.g. "@/*" or "config"
    targets: tuple[str, ...]  # repository-relative, e.g. ("src/*",)

    def match(self, spec: str) -> list[str]:
        if "*" in self.pattern:
            head, _, tail = self.pattern.partition("*")
            if not (spec.startswith(head) and spec.endswith(tail) and len(spec) >= len(head) + len(tail)):
                return []
            star = spec[len(head) : len(spec) - len(tail) if tail else None]
            return [t.replace("*", star, 1) for t in self.targets]
        return list(self.targets) if spec == self.pattern else []


@dataclass(frozen=True)
class AnalysisContext:
    """Repository-wide lookup tables shared by every analyzer (immutable; safe to share across threads)."""

    files: Mapping[str, InventoryFile]
    directories: frozenset[str]
    python_modules: Mapping[str, str]  # dotted module -> file (packages map to their __init__.py)
    python_packages: Mapping[str, str]  # dotted package -> directory (regular and namespace packages)
    path_modules: Mapping[str, str]  # python file -> canonical dotted module
    ts_aliases: tuple[PathAlias, ...] = ()
    ts_base_urls: Mapping[str, str] = field(
        default_factory=dict
    )  # tsconfig dir -> repository-relative baseUrl
    go_modules: Mapping[str, str] = field(default_factory=dict)  # go module path -> directory of its go.mod

    # ------------------------------------------------------------------ generic

    def has_file(self, path: str) -> bool:
        return path in self.files

    def has_dir(self, path: str) -> bool:
        return path in self.directories

    # ------------------------------------------------------------------ python

    def module_for_path(self, path: str) -> str | None:
        return self.path_modules.get(path)

    def resolve_python_module(self, module: str) -> str | None:
        """Repository file defining `module` (package → its `__init__.py`), or None when external/namespace."""
        return self.python_modules.get(module)

    def python_package_dir(self, module: str) -> str | None:
        return self.python_packages.get(module)

    def absolute_python_module(self, path: str, level: int, module: str | None) -> str:
        """Make `from <level dots><module> import …` in `path` absolute (PEP 328 semantics)."""
        if level <= 0:
            return module or ""
        canonical = self.path_modules.get(path)
        if canonical is not None:
            parts = canonical.split(".") if canonical else []
            if not path.endswith(("/__init__.py", "/__init__.pyi")) and path not in (
                "__init__.py",
                "__init__.pyi",
            ):
                parts = parts[:-1]
        else:  # not a known module: fall back to the directory layout
            directory = posixpath.dirname(path)
            parts = directory.split("/") if directory else []
        up = level - 1
        base = parts[: len(parts) - up] if up <= len(parts) else []
        if module:
            base = [*base, *module.split(".")]
        return ".".join(p for p in base if p)

    # ------------------------------------------------------------------ javascript / typescript

    def resolve_js(self, from_path: str, spec: str) -> str | None:
        """Resolve an import specifier written in `from_path` to a repository file (None = external package)."""
        if not spec:
            return None
        if spec.startswith(("./", "../")) or spec in (".", ".."):
            base = posixpath.normpath(posixpath.join(posixpath.dirname(from_path), spec))
            return None if base.startswith("..") else self._js_candidates(base)
        for alias in self._aliases_for(from_path):
            for target in alias.match(spec):
                hit = self._js_candidates(posixpath.normpath(target))
                if hit:
                    return hit
        for tsdir, base_url in sorted(self.ts_base_urls.items(), key=lambda kv: -len(kv[0])):
            if _is_under(from_path, tsdir):
                hit = self._js_candidates(posixpath.normpath(posixpath.join(base_url, spec)))
                if hit:
                    return hit
        return None

    def _aliases_for(self, from_path: str) -> list[PathAlias]:
        scoped = [a for a in self.ts_aliases if _is_under(from_path, a.base_dir)]
        # nearest tsconfig first, then the most specific pattern
        return sorted(scoped, key=lambda a: (-len(a.base_dir), -len(a.pattern.replace("*", ""))))

    def _js_candidates(self, base: str) -> str | None:
        if base in self.files and not base.endswith("/"):
            return base
        stem, ext = posixpath.splitext(base)
        for source_ext in _JS_EMITTED_TO_SOURCE.get(ext, ()):
            if stem + source_ext in self.files:
                return stem + source_ext
        for js_ext in JS_EXTENSIONS:
            if base + js_ext in self.files:
                return base + js_ext
        for js_ext in JS_EXTENSIONS:
            candidate = f"{base}/index{js_ext}" if base not in ("", ".") else f"index{js_ext}"
            if candidate in self.files:
                return candidate
        return None

    # ------------------------------------------------------------------ go

    def resolve_go_import(self, spec: str) -> str | None:
        """Directory of a Go import path that belongs to a module declared in this repository."""
        for module, directory in sorted(self.go_modules.items(), key=lambda kv: -len(kv[0])):
            if spec == module or spec.startswith(module + "/"):
                sub = spec[len(module) :].lstrip("/")
                target = posixpath.normpath(posixpath.join(directory, sub)) if sub else directory
                target = "" if target == "." else target
                if target in self.directories:
                    return target
        return None


# --------------------------------------------------------------------------------------------------- building


def build_context(inventory: Inventory, texts: Mapping[str, str]) -> AnalysisContext:
    """Build the lookup tables from the inventory (paths) and the already-loaded texts (tsconfig, go.mod)."""
    files = {f.path: f for f in inventory.files}
    directories = _directories(files)
    python_modules, python_packages, path_modules = _python_module_map(files)
    aliases, base_urls = _ts_aliases(files, texts)
    return AnalysisContext(
        files=files,
        directories=directories,
        python_modules=python_modules,
        python_packages=python_packages,
        path_modules=path_modules,
        ts_aliases=tuple(aliases),
        ts_base_urls=base_urls,
        go_modules=_go_modules(files, texts),
    )


def _directories(paths: Iterable[str]) -> frozenset[str]:
    dirs: set[str] = {""}
    for path in paths:
        parent = posixpath.dirname(path)
        while parent and parent not in dirs:
            dirs.add(parent)
            parent = posixpath.dirname(parent)
    return frozenset(dirs)


def _is_under(path: str, directory: str) -> bool:
    return not directory or path == directory or path.startswith(directory + "/")


def _module_name(path: str, root: str) -> str:
    rel = path[len(root) + 1 :] if root else path
    stem = rel.rsplit(".", 1)[0]
    parts = stem.split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _python_module_map(
    files: Mapping[str, InventoryFile],
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    py_files = sorted(
        (p for p in files if p.endswith(_PY_EXTENSIONS)),
        key=lambda p: (p.endswith(".pyi"), p.count("/"), p),
    )
    init_dirs = {
        posixpath.dirname(p) for p in py_files if posixpath.basename(p) in ("__init__.py", "__init__.pyi")
    }
    project_roots = {
        posixpath.dirname(p) for p in files if posixpath.basename(p) in _PY_PROJECT_MARKERS and "/" in p
    }

    modules: dict[str, str] = {}
    packages: dict[str, str] = {}
    canonical: dict[str, str] = {}
    alternatives: list[tuple[int, str, str]] = []  # (priority, module, path)

    for path in py_files:
        parent = posixpath.dirname(path)
        root = parent
        while root and root in init_dirs:
            root = posixpath.dirname(root)
        name = _module_name(path, root)
        if not name:
            continue
        canonical.setdefault(path, name)
        modules.setdefault(name, path)
        # alternative spellings: repository-relative, src-layout and monorepo sub-project roots
        ancestors: list[str] = [""]
        segments = parent.split("/") if parent else []
        for i in range(1, len(segments) + 1):
            prefix = "/".join(segments[:i])
            if segments[i - 1] in ("src", "lib", "python") or prefix in project_roots:
                ancestors.append(prefix)
        for anc in ancestors:
            if anc != root and _is_under(path, anc):
                alternatives.append((anc.count("/") if anc else -1, _module_name(path, anc), path))

    for _prio, name, path in sorted(alternatives):
        if name:
            modules.setdefault(name, path)

    for name, path in modules.items():
        parts = name.split(".")
        directory = posixpath.dirname(path)
        if posixpath.basename(path).startswith("__init__."):
            packages.setdefault(name, directory)
            parts = parts[:-1]
            directory = posixpath.dirname(directory)
        else:
            parts = parts[:-1]
        while parts:
            packages.setdefault(".".join(parts), directory)
            parts = parts[:-1]
            directory = posixpath.dirname(directory)
    return modules, packages, canonical


def _ts_aliases(
    files: Mapping[str, InventoryFile], texts: Mapping[str, str]
) -> tuple[list[PathAlias], dict[str, str]]:
    aliases: list[PathAlias] = []
    base_urls: dict[str, str] = {}
    for path in sorted(files):
        name = posixpath.basename(path)
        if not (re.fullmatch(r"tsconfig(\.[\w.-]+)?\.json", name) or name == "jsconfig.json"):
            continue
        text = texts.get(path)
        if text is None:
            continue
        try:
            data = load_jsonc(text)
        except ValueError:
            continue
        options = data.get("compilerOptions") if isinstance(data, dict) else None
        if not isinstance(options, dict):
            continue
        tsdir = posixpath.dirname(path)
        base_url_raw = options.get("baseUrl")
        base = tsdir
        if isinstance(base_url_raw, str):
            base = posixpath.normpath(posixpath.join(tsdir, base_url_raw))
            base = "" if base == "." else base
            base_urls.setdefault(tsdir, base)
        paths = options.get("paths")
        if not isinstance(paths, dict):
            continue
        for pattern, targets in paths.items():
            if not isinstance(pattern, str) or not isinstance(targets, list):
                continue
            resolved = tuple(
                "" if (t := posixpath.normpath(posixpath.join(base, target))) == "." else t
                for target in targets
                if isinstance(target, str)
            )
            if resolved:
                aliases.append(PathAlias(base_dir=tsdir, pattern=pattern, targets=resolved))
    return aliases, base_urls


_GO_MODULE = re.compile(r"^\s*module\s+(\S+)", re.M)


def _go_modules(files: Mapping[str, InventoryFile], texts: Mapping[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(files):
        if posixpath.basename(path) != "go.mod":
            continue
        match = _GO_MODULE.search(texts.get(path, ""))
        if match:
            out.setdefault(match.group(1).strip('"'), posixpath.dirname(path))
    return out


# --------------------------------------------------------------------------------------------------- helpers


def load_jsonc(text: str) -> Any:
    """Parse JSON that may contain `//` / `/* */` comments and trailing commas (tsconfig, VS Code style)."""
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(ch)
            i += 1
    cleaned = re.sub(r",(\s*[}\]])", r"\1", "".join(out))
    return json.loads(cleaned.lstrip("﻿"))


def line_of(text: str, index: int) -> int:
    """1-based line number of character offset `index`."""
    return text.count("\n", 0, max(index, 0)) + 1


def find_line(text: str, pattern: str | re.Pattern[str], start: int = 0) -> int:
    """Line of the first regex match at/after `start` (0 when absent)."""
    rx = re.compile(pattern) if isinstance(pattern, str) else pattern
    match = rx.search(text, start)
    return line_of(text, match.start()) if match else 0
