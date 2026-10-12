"""Repository inventory (LLD §7, ESD §7): every file's metadata, role and importance tier.

`build_inventory(root)` walks an already-extracted repository without following symlinks, hashes every regular
file, sniffs text vs binary (NUL byte + UTF-8 decode), flags generated/vendor content and assigns a `role` and a
`tier` (0 architecture-critical … 4 noise). `read_texts(root, inventory)` then loads the text of tier 0–3 files for
the analyzers. Nothing is executed; files are only read as bytes.
"""

from __future__ import annotations

import codecs
import hashlib
import os
import posixpath
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from unfold.core.logging import get_logger
from unfold.repo_intel.models import Inventory, InventoryFile, Tier

log = get_logger(__name__)

DEFAULT_MAX_SINGLE_FILE_BYTES = 1024 * 1024
LARGE_DATA_BYTES = 256 * 1024  # data files above this are "datasets too large for textual analysis"
_SNIFF_BYTES = 8192
_READ_CHUNK = 1024 * 1024

# Directories that are never descended into (recorded once in `Inventory.skipped`).
PRUNED_DIRS: dict[str, str] = {
    ".git": "vcs_metadata",
    ".hg": "vcs_metadata",
    ".svn": "vcs_metadata",
    "node_modules": "dependency_install",
    "bower_components": "dependency_install",
    ".venv": "virtualenv",
    "venv": "virtualenv",
    ".virtualenv": "virtualenv",
    "site-packages": "dependency_install",
    "__pycache__": "cache",
    ".mypy_cache": "cache",
    ".pytest_cache": "cache",
    ".ruff_cache": "cache",
    ".tox": "cache",
    ".nox": "cache",
    ".cache": "cache",
    ".gradle": "cache",
    ".idea": "editor_metadata",
    ".vscode-test": "cache",
    ".terraform": "dependency_install",
    ".turbo": "cache",
    ".parcel-cache": "cache",
    ".yarn": "dependency_install",
    ".pnpm-store": "dependency_install",
    ".ipynb_checkpoints": "cache",
    ".DS_Store": "os_metadata",
}

VENDOR_DIRS = frozenset(
    {"vendor", "vendors", "third_party", "thirdparty", "third-party", "external_libs", "Pods"}
)
GENERATED_DIRS = frozenset(
    {
        "dist",
        "build",
        ".next",
        ".nuxt",
        ".svelte-kit",
        ".output",
        ".docusaurus",
        "coverage",
        "htmlcov",
        ".coverage",
        "storybook-static",
        "__generated__",
        "generated",
        "out",
        "obj",
        "bin",
        "target",
        ".angular",
        ".expo",
    }
)
# `out`, `bin`, `obj` and `target` are build outputs only next to a build manifest (see `_is_build_output_dir`).
_CONDITIONAL_GENERATED_DIRS = {
    "out": ("package.json", "next.config.js", "next.config.mjs", "next.config.ts"),
    "bin": (".csproj", ".fsproj"),
    "obj": (".csproj", ".fsproj"),
    "target": ("Cargo.toml", "pom.xml", "build.sbt"),
}

LOCKFILES = frozenset(
    {
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lockb",
        "bun.lock",
        "poetry.lock",
        "uv.lock",
        "pdm.lock",
        "Pipfile.lock",
        "Cargo.lock",
        "Gemfile.lock",
        "composer.lock",
        "go.sum",
        "mix.lock",
        "flake.lock",
        "packages.lock.json",
        "gradle.lockfile",
        "pubspec.lock",
        "Podfile.lock",
        "conda-lock.yml",
    }
)
_GENERATED_NAME = re.compile(
    r"""(?x)(
        \.min\.(js|css|mjs)$ | \.bundle\.js$ | \.chunk\.js$ | \.map$ |
        _pb2(_grpc)?\.pyi?$ | \.pb\.go$ | \.pb\.(cc|h)$ | _grpc\.pb\.go$ | \.pb\.ts$ |
        \.generated\.[a-z]+$ | \.g\.dart$ | \.freezed\.dart$ | \.designer\.cs$ | _generated\.go$ |
        ^zz_generated\.
    )"""
)
WEIGHT_EXTS = frozenset(
    {
        "pt",
        "pth",
        "ckpt",
        "safetensors",
        "onnx",
        "h5",
        "hdf5",
        "keras",
        "tflite",
        "pb",
        "gguf",
        "ggml",
        "pkl",
        "pickle",
        "joblib",
        "npy",
        "npz",
        "mlmodel",
        "bin",
        "msgpack",
    }
)
DATA_EXTS = frozenset(
    {
        "csv",
        "tsv",
        "parquet",
        "jsonl",
        "ndjson",
        "arrow",
        "feather",
        "avro",
        "orc",
        "sqlite",
        "sqlite3",
        "db",
        "xlsx",
    }
)
ASSET_EXTS = frozenset(
    {
        "png",
        "jpg",
        "jpeg",
        "gif",
        "bmp",
        "ico",
        "icns",
        "webp",
        "avif",
        "svg",
        "tiff",
        "psd",
        "woff",
        "woff2",
        "ttf",
        "otf",
        "eot",
        "mp3",
        "mp4",
        "wav",
        "ogg",
        "webm",
        "mov",
        "avi",
        "pdf",
        "zip",
        "gz",
        "tgz",
        "bz2",
        "xz",
        "7z",
        "rar",
        "jar",
        "war",
        "exe",
        "dll",
        "so",
        "dylib",
        "o",
        "a",
        "class",
        "pyc",
        "wasm",
    }
)

LANGUAGE_BY_EXT: dict[str, str] = {
    "py": "python",
    "pyi": "python",
    "pyx": "cython",
    "ipynb": "jupyter",
    "js": "javascript",
    "mjs": "javascript",
    "cjs": "javascript",
    "jsx": "javascript",
    "ts": "typescript",
    "mts": "typescript",
    "cts": "typescript",
    "tsx": "typescript",
    "vue": "vue",
    "svelte": "svelte",
    "astro": "astro",
    "go": "go",
    "java": "java",
    "kt": "kotlin",
    "kts": "kotlin",
    "scala": "scala",
    "groovy": "groovy",
    "gradle": "groovy",
    "rs": "rust",
    "c": "c",
    "h": "c",
    "cc": "cpp",
    "cpp": "cpp",
    "cxx": "cpp",
    "hpp": "cpp",
    "hh": "cpp",
    "cs": "csharp",
    "fs": "fsharp",
    "rb": "ruby",
    "rake": "ruby",
    "gemspec": "ruby",
    "php": "php",
    "swift": "swift",
    "m": "objc",
    "mm": "objc",
    "dart": "dart",
    "ex": "elixir",
    "exs": "elixir",
    "erl": "erlang",
    "lua": "lua",
    "r": "r",
    "jl": "julia",
    "hs": "haskell",
    "ml": "ocaml",
    "clj": "clojure",
    "pl": "perl",
    "zig": "zig",
    "sh": "shell",
    "bash": "shell",
    "zsh": "shell",
    "fish": "shell",
    "ps1": "powershell",
    "bat": "batch",
    "cmd": "batch",
    "sql": "sql",
    "prisma": "prisma",
    "graphql": "graphql",
    "gql": "graphql",
    "proto": "protobuf",
    "tf": "hcl",
    "tfvars": "hcl",
    "hcl": "hcl",
    "bicep": "bicep",
    "yaml": "yaml",
    "yml": "yaml",
    "json": "json",
    "jsonc": "json",
    "json5": "json",
    "toml": "toml",
    "ini": "ini",
    "cfg": "ini",
    "conf": "ini",
    "env": "dotenv",
    "xml": "xml",
    "html": "html",
    "htm": "html",
    "jinja": "jinja",
    "jinja2": "jinja",
    "j2": "jinja",
    "hbs": "handlebars",
    "ejs": "ejs",
    "css": "css",
    "scss": "scss",
    "sass": "sass",
    "less": "less",
    "md": "markdown",
    "mdx": "markdown",
    "markdown": "markdown",
    "rst": "restructuredtext",
    "adoc": "asciidoc",
    "txt": "text",
    "csv": "csv",
    "tsv": "csv",
}
LANGUAGE_BY_NAME: dict[str, str] = {
    "Dockerfile": "dockerfile",
    "Containerfile": "dockerfile",
    "Makefile": "make",
    "GNUmakefile": "make",
    "Jenkinsfile": "groovy",
    "Gemfile": "ruby",
    "Rakefile": "ruby",
    "Podfile": "ruby",
    "Procfile": "procfile",
    "Vagrantfile": "ruby",
    "BUILD": "starlark",
    "WORKSPACE": "starlark",
    "go.mod": "go_module",
    "go.work": "go_module",
    "Pipfile": "toml",
    ".env.example": "dotenv",
    ".env.sample": "dotenv",
    ".env.template": "dotenv",
    ".gitignore": "ignore",
    ".dockerignore": "ignore",
    "CODEOWNERS": "text",
    "LICENSE": "text",
}

# ------------------------------------------------------------------------------------------ role heuristics

_DOC_NAMES = re.compile(
    r"^(readme|changelog|changes|history|contributing|code_of_conduct|security|license|licence|copying|authors|"
    r"notice|support|governance|maintainers|roadmap|faq)(\.[a-z]+)?$",
    re.I,
)
_DOC_EXTS = frozenset({"md", "mdx", "markdown", "rst", "adoc", "txt"})
_TEST_DIRS = frozenset(
    {"tests", "test", "__tests__", "spec", "specs", "e2e", "testing", "integration_tests", "cypress"}
)
_TEST_NAME = re.compile(
    r"""(?x)(
        ^test_.*\.py$ | .*_test\.py$ | ^conftest\.py$ | .*_test\.go$ |
        .*\.(test|spec)\.(t|j)sx?$ | .*\.(test|spec)\.m?(t|j)s$ |
        .*(Test|Tests|IT)\.(java|kt|scala|cs)$ | .*_spec\.rb$ | .*_test\.rb$ | .*Test\.php$ | .*_test\.exs$
    )"""
)
_EXAMPLE_DIRS = frozenset(
    {"examples", "example", "samples", "sample", "demo", "demos", "cookbook", "tutorials"}
)
_CI_FILES = frozenset(
    {
        ".gitlab-ci.yml",
        "Jenkinsfile",
        ".travis.yml",
        "azure-pipelines.yml",
        "bitbucket-pipelines.yml",
        ".drone.yml",
        "cloudbuild.yaml",
        "cloudbuild.yml",
        "appveyor.yml",
        "buildkite.yml",
        "codemagic.yaml",
    }
)
_MANIFEST_FILES = frozenset(
    {
        "package.json",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "Pipfile",
        "go.mod",
        "go.work",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "Cargo.toml",
        "composer.json",
        "Gemfile",
        "mix.exs",
        "pubspec.yaml",
        "environment.yml",
        "environment.yaml",
        "deno.json",
        "build.sbt",
        "Package.swift",
        "Project.toml",
        "DESCRIPTION",
    }
)
_REQUIREMENTS = re.compile(r"^(requirements|constraints)([-_.][\w.-]*)?\.(txt|in)$", re.I)
_IAC_DIRS = frozenset(
    {"k8s", "kubernetes", "kube", "manifests", "helm", "charts", "deploy", "deployment", "infra", "kustomize"}
)
_IAC_EXTS = frozenset({"tf", "tfvars", "hcl", "bicep"})
_IAC_NAMES = frozenset(
    {
        "Pulumi.yaml",
        "serverless.yml",
        "serverless.yaml",
        "cdk.json",
        "Chart.yaml",
        "kustomization.yaml",
        "kustomization.yml",
        "skaffold.yaml",
        "fly.toml",
        "render.yaml",
        "app.yaml",
        "vercel.json",
        "netlify.toml",
        "template.yaml",
        "samconfig.toml",
        "helmfile.yaml",
    }
)
_K8S_SNIFF = re.compile(r"^apiVersion:\s*\S+", re.M)
_K8S_KIND = re.compile(r"^kind:\s*[A-Z]\w+", re.M)
_CFN_SNIFF = re.compile(r"^AWSTemplateFormatVersion:|^Resources:\s*$", re.M)
_MIGRATION_DIRS = frozenset(
    {"migrations", "migration", "migrate", "alembic", "versions", "flyway", "liquibase"}
)
_SCHEMA_EXTS = frozenset({"prisma", "graphql", "gql", "proto", "avsc", "thrift"})
_SCHEMA_NAMES = re.compile(
    r"^(schema|schemas|openapi|swagger|asyncapi)(\.[\w-]+)?\.(py|sql|ya?ml|json|ts|js|graphql|prisma)$", re.I
)
_SCHEMA_DIRS = frozenset({"schemas", "schema", "dto", "dtos", "serializers"})
_MODEL_NAMES = re.compile(
    r"^(models?|entities|entity|tables?|orm|domain_models)\.(py|ts|js|go|rb|rs|php|kt|java)$"
    r"|.*\.(model|entity)\.(ts|js)$|.*(Entity|Model)\.(java|kt|cs)$|^(pipeline|pipelines|dag)\.py$",
    re.I,
)
_MODEL_DIRS = frozenset({"models", "model", "entities", "entity", "domain", "dags", "pipelines"})
_ENTRY_NAMES = frozenset(
    {
        "main.py",
        "__main__.py",
        "app.py",
        "wsgi.py",
        "asgi.py",
        "manage.py",
        "server.py",
        "cli.py",
        "run.py",
        "train.py",
        "main.go",
        "main.rs",
        "lib.rs",
        "main.ts",
        "main.js",
        "server.ts",
        "server.js",
        "index.ts",
        "index.js",
        "index.tsx",
        "index.jsx",
        "main.tsx",
        "main.jsx",
        "app.ts",
        "app.js",
        "Program.cs",
        "Startup.cs",
        "main.dart",
        "main.kt",
        "Main.java",
        "Main.kt",
        "_app.tsx",
        "_app.jsx",
        "layout.tsx",
        "layout.jsx",
        "main.cpp",
        "main.c",
        "config.ru",
        "artisan",
        "Procfile",
    }
)
_ENTRY_DEEP_OK = frozenset(
    {"main.go", "main.rs", "__main__.py", "manage.py", "wsgi.py", "asgi.py", "Program.cs"}
)
_ENTRY_SHALLOW_DIRS = frozenset(
    {"", "src", "app", "server", "cmd", "bin", "backend", "frontend", "web", "api", "pages"}
)
_MAIN_GUARD = re.compile(r"""^if\s+__name__\s*==\s*["']__main__["']\s*:""", re.M)
_APP_CLASS = re.compile(r"(Application|App)\.(java|kt)$")
_ROUTE_DIRS = frozenset(
    {
        "routes",
        "routers",
        "router",
        "endpoints",
        "controllers",
        "controller",
        "handlers",
        "views",
        "resolvers",
    }
)
_ROUTE_NAMES = re.compile(
    r"^(routes?|routers?|urls|views|endpoints?|controllers?|handlers?|api)\.(py|ts|js|go|rb|php)$|"
    r".*(Controller|Resource|Handler)\.(java|kt|cs|php)$|.*\.(controller|routes?|router)\.(ts|js)$|"
    r"^route\.(ts|js)$",
    re.I,
)
_ROUTE_SNIFF = re.compile(
    r"""(?x)
    @\w+\.(get|post|put|delete|patch|route|api_route|websocket)\(\s*["'/]
    | \b(app|router|api|server|r|mux|e|g)\.(get|post|put|delete|patch|all|route|handle|handlefunc|Get|Post|Put|Delete|Patch|HandleFunc|Handle)\(\s*["'`/]
    | @(Get|Post|Put|Delete|Patch|Request)Mapping\b | @(Get|Post|Put|Delete|Patch|Controller)\(
    | \burlpatterns\s*= | \.include_router\( | \.register_blueprint\(
    """
)
_SERVICE_DIRS = frozenset(
    {
        "services",
        "service",
        "repositories",
        "repository",
        "repos",
        "usecases",
        "use_cases",
        "core",
        "domain",
        "workers",
        "worker",
        "tasks",
        "jobs",
        "consumers",
        "pipelines",
        "agents",
        "engine",
        "internal",
        "pkg",
        "store",
        "stores",
        "retrieval",
        "indexing",
        "providers",
        "clients",
        "adapters",
    }
)
_SERVICE_NAMES = re.compile(
    r".*(service|Service|repository|Repository|Repo|Manager|Engine|Worker|Client|Store|Gateway)\.(py|ts|js|go|java|kt|cs|rb|php|rs)$"
    r"|^(tasks|worker|workers|jobs|consumer|consumers|pipeline|retriever|agent|agents)\.py$",
)
_COMPONENT_DIRS = frozenset(
    {"components", "component", "pages", "views", "screens", "layouts", "widgets", "app", "ui"}
)
_COMPONENT_EXTS = frozenset({"tsx", "jsx", "vue", "svelte", "astro"})
_CONFIG_NAMES = re.compile(
    r"""(?x)^(
        settings\.py | config\.(py|ts|js|go|rb|json|ya?ml|toml) | configuration\.\w+ | conf\.py |
        .*\.config\.(js|cjs|mjs|ts) | tsconfig(\.[\w.-]+)?\.json | jsconfig\.json | \.env(\.[\w-]+)? |
        \.(eslintrc|prettierrc|babelrc|stylelintrc|editorconfig|npmrc|nvmrc|flake8|pylintrc|pre-commit-config)(\.\w+)? |
        (ruff|mypy|pytest|tox|setup|alembic|babel|lerna|nx|turbo|renovate|dependabot)\.(ini|toml|cfg|json|ya?ml) |
        Makefile | Justfile | Taskfile\.ya?ml | nginx\.conf | .*\.conf | .*\.ini | .*\.cfg | .*\.properties |
        application(-\w+)?\.(ya?ml|properties) | appsettings(\.\w+)?\.json | \.gitattributes | \.gitignore |
        \.dockerignore | CODEOWNERS
    )$"""
)
_CONFIG_DIRS = frozenset(
    {"config", "configs", "conf", "settings", "etc", ".github", ".devcontainer", ".vscode"}
)


PROGRAM_LIKE = frozenset(
    {
        "python",
        "javascript",
        "typescript",
        "go",
        "java",
        "kotlin",
        "scala",
        "rust",
        "csharp",
        "ruby",
        "php",
        "swift",
        "dart",
        "elixir",
        "cpp",
        "c",
    }
)


@dataclass(frozen=True)
class _Scan:
    size: int
    sha256: str
    is_text: bool
    lines: int
    head: str  # decoded text (whole file; prefix when oversized) for role sniffing; "" for binaries


def build_inventory(
    root: Path,
    max_single_file_bytes: int = DEFAULT_MAX_SINGLE_FILE_BYTES,
    *,
    large_data_bytes: int = LARGE_DATA_BYTES,
) -> Inventory:
    """Walk `root` (never following symlinks) and describe every regular file. Pure metadata; nothing executes."""
    root = Path(root)
    files: list[InventoryFile] = []
    skipped: dict[str, str] = {}
    total = 0
    all_paths: set[str] = set()
    pending: list[tuple[str, Path]] = []

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current = Path(dirpath)
        rel_dir = _rel(root, current)
        keep: list[str] = []
        for d in sorted(dirnames):
            rel = posixpath.join(rel_dir, d) if rel_dir else d
            full = current / d
            if full.is_symlink():
                skipped[rel] = "symlink"
            elif d in PRUNED_DIRS:
                skipped[rel] = PRUNED_DIRS[d]
            else:
                keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            rel = posixpath.join(rel_dir, name) if rel_dir else name
            full = current / name
            try:
                st = full.lstat()
            except OSError as exc:
                skipped[rel] = f"unreadable: {type(exc).__name__}"
                continue
            if stat.S_ISLNK(st.st_mode):
                skipped[rel] = "symlink"
                continue
            if not stat.S_ISREG(st.st_mode):
                skipped[rel] = "not_regular_file"
                continue
            if name in PRUNED_DIRS:  # e.g. .DS_Store
                skipped[rel] = PRUNED_DIRS[name]
                continue
            all_paths.add(rel)
            pending.append((rel, full))

    for rel, full in pending:
        try:
            scan = _scan_file(full, max_single_file_bytes)
        except OSError as exc:
            skipped[rel] = f"unreadable: {type(exc).__name__}"
            continue
        files.append(_describe(rel, scan, max_single_file_bytes, large_data_bytes, all_paths))
        total += scan.size

    files.sort(key=lambda f: f.path)
    inventory = Inventory(files=files, skipped=dict(sorted(skipped.items())), total_bytes=total)
    log.info(
        "repo_intel.inventory_built",
        files=len(files),
        skipped=len(skipped),
        total_bytes=total,
        tiers=inventory.tier_counts(),
    )
    return inventory


def read_texts(
    root: Path,
    inventory: Inventory,
    max_tier: int = 3,
    max_single_file_bytes: int = DEFAULT_MAX_SINGLE_FILE_BYTES,
) -> dict[str, str]:
    """Decode the text of every analyzable file (text, not oversized, tier <= `max_tier`). CRLF → LF."""
    root = Path(root)
    root_resolved = root.resolve()
    out: dict[str, str] = {}
    for f in inventory.files:
        if not f.is_text or f.oversized or f.tier > max_tier:
            continue
        full = root / f.path
        try:
            st = full.lstat()
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode) or st.st_size > max_single_file_bytes:
                continue
            if not full.resolve().is_relative_to(root_resolved):
                continue
            data = full.read_bytes()
        except OSError as exc:
            log.warning("repo_intel.read_failed", path=f.path, error=type(exc).__name__)
            continue
        out[f.path] = decode_text(data)
    return out


def decode_text(data: bytes) -> str:
    """UTF-8 (BOM stripped), falling back to replacement characters; newlines normalised to LF."""
    text = data.decode("utf-8-sig", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


# --------------------------------------------------------------------------------------------------- internals


def _rel(root: Path, path: Path) -> str:
    rel = path.relative_to(root).as_posix()
    return "" if rel == "." else rel


def _scan_file(path: Path, max_bytes: int) -> _Scan:
    digest = hashlib.sha256()
    size = 0
    newlines = 0
    last_byte = b""
    head = b""
    body = bytearray()
    has_nul = False
    with path.open("rb") as fh:
        while chunk := fh.read(_READ_CHUNK):
            if not head:
                head = chunk[:_SNIFF_BYTES]
                has_nul = b"\x00" in head
            digest.update(chunk)
            size += len(chunk)
            newlines += chunk.count(b"\n")
            last_byte = chunk[-1:]
            if size <= max_bytes:
                body.extend(chunk)
    if has_nul:
        return _Scan(size, digest.hexdigest(), False, 0, "")
    lines = newlines + (1 if size and last_byte != b"\n" else 0)
    small = size <= max_bytes
    is_text = _is_text(bytes(body), final=True) if small else _is_text(head, final=False)
    head_text = (decode_text(bytes(body)) if small else decode_text(head)) if is_text else ""
    return _Scan(size, digest.hexdigest(), is_text, lines if is_text else 0, head_text)


def _is_text(data: bytes, *, final: bool) -> bool:
    if not data:
        return True
    try:
        if final:
            data.decode("utf-8")
        else:
            codecs.getincrementaldecoder("utf-8")().decode(data, final=False)
        return True
    except UnicodeDecodeError:
        # legacy single-byte encodings: text when control characters are rare
        sample = data[:_SNIFF_BYTES]
        controls = sum(1 for b in sample if b < 32 and b not in (9, 10, 12, 13))
        return controls / max(len(sample), 1) < 0.02


def _ext(name: str) -> str:
    if name.startswith(".") and name.count(".") == 1:
        return ""
    return name.rsplit(".", 1)[1].lower() if "." in name else ""


def language_for(path: str) -> str | None:
    name = posixpath.basename(path)
    if name in LANGUAGE_BY_NAME:
        return LANGUAGE_BY_NAME[name]
    if name.startswith("Dockerfile") or name.endswith(".dockerfile") or name.startswith("Containerfile"):
        return "dockerfile"
    if name.startswith(".env"):
        return "dotenv"
    if _REQUIREMENTS.match(name):
        return "pip_requirements"
    return LANGUAGE_BY_EXT.get(_ext(name))


def _segments(path: str) -> list[str]:
    return path.split("/")[:-1]


def _is_build_output_dir(path: str, index: int, segment: str, all_paths: set[str]) -> bool:
    markers = _CONDITIONAL_GENERATED_DIRS.get(segment)
    if markers is None:
        return True
    parent = "/".join(path.split("/")[:index])
    prefix = f"{parent}/" if parent else ""
    return any(
        p.startswith(prefix) and "/" not in p[len(prefix) :] and p[len(prefix) :].endswith(markers)
        for p in all_paths
    )


def _vendor_generated(path: str, name: str, ext: str, all_paths: set[str]) -> tuple[bool, bool]:
    segments = _segments(path)
    vendor = any(
        s in VENDOR_DIRS or s in ("node_modules", "bower_components", "site-packages") for s in segments
    )
    generated = False
    for i, seg in enumerate(segments):
        if seg in GENERATED_DIRS and _is_build_output_dir(path, i, seg, all_paths):
            generated = True
            break
    if name in LOCKFILES or _GENERATED_NAME.search(name) or ext in WEIGHT_EXTS:
        generated = True
    return vendor, generated


def _role(path: str, name: str, ext: str, language: str | None, head: str) -> str:
    segments = [s.lower() for s in _segments(path)]
    seg_set = set(segments)
    lname = name.lower()

    if ext in WEIGHT_EXTS or ext in DATA_EXTS:
        return "data"
    if ext in ASSET_EXTS:
        return "asset"
    if segments[:2] == [".github", "workflows"] and ext in ("yml", "yaml"):
        return "ci"
    if name in _CI_FILES or (segments[:1] == [".circleci"] and ext in ("yml", "yaml")):
        return "ci"
    if language == "dockerfile" or re.match(r"^(docker-)?compose([.-][\w.-]+)?\.ya?ml$", lname):
        return "container"
    if (
        name in LOCKFILES
        or name in _MANIFEST_FILES
        or _REQUIREMENTS.match(name)
        or ext in ("csproj", "fsproj", "gemspec")
    ):
        return "manifest"
    if _DOC_NAMES.match(name) or (
        ext in _DOC_EXTS and (seg_set & {"docs", "doc", "documentation", "wiki"} or ext != "txt")
    ):
        return "docs"
    if _TEST_NAME.match(name) or seg_set & _TEST_DIRS:
        return "test"
    if ext in _IAC_EXTS or name in _IAC_NAMES:
        return "iac"
    if ext in ("yml", "yaml") and (
        (_K8S_SNIFF.search(head) and _K8S_KIND.search(head))
        or _CFN_SNIFF.search(head)
        or ("templates" in seg_set and bool(seg_set & {"helm", "charts"}))
    ):
        return "iac"
    if ext in ("yml", "yaml", "json") and seg_set & _IAC_DIRS and _K8S_KIND.search(head):
        return "iac"
    if seg_set & _MIGRATION_DIRS and ext in (
        "py",
        "sql",
        "rb",
        "ts",
        "js",
        "go",
        "xml",
        "yaml",
        "yml",
        "php",
        "java",
        "kt",
    ):
        return "migration"
    if re.match(r"^V\d+(_\d+)*__\w+\.sql$", name):
        return "migration"
    if ext in _SCHEMA_EXTS or _SCHEMA_NAMES.match(name) or (ext == "sql" and "create table" in head.lower()):
        return "schema"
    if seg_set & _SCHEMA_DIRS and language in ("python", "typescript", "javascript"):
        return "schema"
    if _MODEL_NAMES.match(name) or (
        seg_set & _MODEL_DIRS
        and language
        in ("python", "typescript", "javascript", "go", "java", "kotlin", "csharp", "ruby", "php")
        and not name.startswith("__init__")
    ):
        return "model_def"
    if _is_entrypoint(path, name, segments, language, head):
        return "entrypoint"
    code = language in PROGRAM_LIKE
    sniffed = bool(code and _ROUTE_SNIFF.search(head))
    if sniffed or (
        code and not name.startswith("__init__") and (_ROUTE_NAMES.match(name) or seg_set & _ROUTE_DIRS)
    ):
        return "route"
    if _CONFIG_NAMES.match(name):
        return "config"
    if _SERVICE_NAMES.match(name) or (seg_set & _SERVICE_DIRS and code and not name.startswith("__init__")):
        return "service"
    if ext in _COMPONENT_EXTS or (
        ext in ("ts", "js") and seg_set & {"components", "pages", "screens", "layouts", "widgets"}
    ):
        return "component"
    if seg_set & _CONFIG_DIRS and ext in ("yml", "yaml", "json", "toml", "ini", "cfg", "py", "ts", "js"):
        return "config"
    if language in ("yaml", "json", "toml", "ini", "dotenv", "xml", "ignore", "make"):
        return "config"
    if language in ("html", "css", "scss", "sass", "less", "jinja", "handlebars", "ejs"):
        return "component" if seg_set & {"templates", "components", "views"} else "source"
    if ext == "txt" or language == "text":
        return "docs"
    return "source"


def _is_entrypoint(path: str, name: str, segments: list[str], language: str | None, head: str) -> bool:
    if language == "python" and _MAIN_GUARD.search(head):
        return True
    if _APP_CLASS.search(name) and language in ("java", "kotlin") and "@SpringBootApplication" in head:
        return True
    if name not in _ENTRY_NAMES:
        return False
    if name in _ENTRY_DEEP_OK:
        return True
    parent = segments[-1] if segments else ""
    if len(segments) <= 1 and parent in _ENTRY_SHALLOW_DIRS:
        return True
    # `web/src/main.tsx`, `backend/app/main.py`, `cmd/api/main.go`, `app/layout.tsx`
    return (len(segments) <= 3 and parent in _ENTRY_SHALLOW_DIRS) or (
        len(segments) >= 2 and segments[-2] == "cmd"
    )


_TIER_BY_ROLE: dict[str, Tier] = {
    "entrypoint": 0,
    "manifest": 0,
    "container": 0,
    "ci": 0,
    "iac": 0,
    "route": 0,
    "schema": 0,
    "model_def": 0,
    "service": 1,
    "component": 1,
    "migration": 1,
    "source": 2,
    "config": 2,
    "docs": 3,
    "test": 3,
    "data": 3,
    "asset": 4,
}


def _tier(
    role: str,
    path: str,
    *,
    is_text: bool,
    generated: bool,
    vendor: bool,
    oversized: bool,
    size: int,
    large_data: int,
) -> Tier:
    if vendor or generated or not is_text or oversized:
        return 4
    if role == "data" and size > large_data:
        return 4
    if role == "asset" and not is_text:
        return 4
    tier = _TIER_BY_ROLE.get(role, 2)
    segments = {s.lower() for s in _segments(path)}
    if segments & _EXAMPLE_DIRS and tier < 3:
        return 3
    return tier


def _describe(path: str, scan: _Scan, max_bytes: int, large_data: int, all_paths: set[str]) -> InventoryFile:
    name = posixpath.basename(path)
    ext = _ext(name)
    language = language_for(path)
    vendor, generated = _vendor_generated(path, name, ext, all_paths)
    oversized = scan.size > max_bytes
    role = _role(path, name, ext, language, scan.head)
    tier = _tier(
        role,
        path,
        is_text=scan.is_text,
        generated=generated,
        vendor=vendor,
        oversized=oversized,
        size=scan.size,
        large_data=large_data,
    )
    return InventoryFile(
        path=path,
        size=scan.size,
        ext=ext,
        language=language,
        is_text=scan.is_text,
        generated=generated,
        vendor=vendor,
        oversized=oversized,
        sha256=scan.sha256,
        lines=scan.lines,
        role=role,
        tier=tier,
    )
