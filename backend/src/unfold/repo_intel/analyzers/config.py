"""Manifest, container, infrastructure and documentation analyzer (LLD §7).

Parses dependency manifests (pyproject, requirements, package.json, go.mod, Cargo.toml), Dockerfiles, Compose files,
Terraform, Kubernetes manifests and Markdown headings into `FileAnalysis` facts. Everything is data: YAML is read
with `safe_load`, TOML with `tomllib`, HCL with python-hcl2; nothing is executed or fetched.
"""

from __future__ import annotations

import json
import posixpath
import re
import tomllib
from typing import Any

import yaml

from unfold.repo_intel.analyzers.base import AnalysisContext
from unfold.repo_intel.models import (
    ComposeServiceDef,
    DependencyDef,
    DocSection,
    FileAnalysis,
    InfraDef,
    InventoryFile,
)

_PEP508_NAME = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*([<>=!~].*)?")
_MAX_HEADING_LINE = 400
_ENGINE_TYPES = {"aws_db_instance", "aws_rds_cluster", "google_sql_database_instance"}
_COMPOSE_NAMES = re.compile(r"^(docker-)?compose([.\-].+)?\.ya?ml$", re.IGNORECASE)


def _kind(file: InventoryFile) -> str | None:
    name = file.name.lower()
    if name == "pyproject.toml":
        return "pyproject"
    if name.startswith("requirements") and name.endswith(".txt"):
        return "requirements"
    if name == "package.json":
        return "npm"
    if name == "go.mod":
        return "gomod"
    if name == "cargo.toml":
        return "cargo"
    if name == "dockerfile" or name.startswith("dockerfile.") or name.endswith(".dockerfile"):
        return "dockerfile"
    if _COMPOSE_NAMES.match(name) or (name.startswith("docker-compose") and file.ext in ("yml", "yaml")):
        return "compose"
    if file.ext == "tf":
        return "terraform"
    if file.ext in ("yml", "yaml"):
        return "yaml"
    if file.ext in ("md", "markdown"):
        return "markdown"
    return None


def _unquote(value: Any) -> str:
    """python-hcl2 keeps the quotes of block labels and string literals in some versions; drop them."""
    return str(value).strip().strip('"')


def _line_of(text: str, needle: str) -> int:
    idx = text.find(needle)
    return text.count("\n", 0, idx) + 1 if idx >= 0 else 0


def _str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if isinstance(v, str | int)]
    return []


class ConfigAnalyzer:
    name = "config"

    def supports(self, file: InventoryFile) -> bool:
        return file.is_text and not file.oversized and _kind(file) is not None

    def analyze(self, file: InventoryFile, text: str, ctx: AnalysisContext | None = None) -> FileAnalysis:
        kind = _kind(file) or ""
        fa = FileAnalysis(path=file.path, language=file.language, analyzer=self.name)
        try:
            getattr(self, f"_{kind}")(file, text, fa)
        except Exception as exc:
            fa.errors.append(f"{kind}: {type(exc).__name__}: {str(exc)[:160]}")
        return fa

    # --- manifests -----------------------------------------------------------------------------------------

    def _pyproject(self, file: InventoryFile, text: str, fa: FileAnalysis) -> None:
        data = tomllib.loads(text)
        project = data.get("project", {})

        def add(spec: str, dev: bool) -> None:
            m = _PEP508_NAME.match(spec)
            if m:
                version = spec[m.end() :].split(";")[0].strip() or None
                fa.dependencies.append(
                    DependencyDef(
                        name=m.group(1),
                        version=version,
                        ecosystem="pypi",
                        dev=dev,
                        line=_line_of(text, m.group(1)),
                    )
                )

        for spec in project.get("dependencies", []):
            add(str(spec), False)
        for group in project.get("optional-dependencies", {}).values():
            for spec in group:
                add(str(spec), True)
        for group in data.get("dependency-groups", {}).values():
            for spec in group:
                if isinstance(spec, str):
                    add(spec, True)
        poetry = data.get("tool", {}).get("poetry", {})
        for dep, ver in poetry.get("dependencies", {}).items():
            if dep.lower() != "python":
                fa.dependencies.append(
                    DependencyDef(
                        name=dep,
                        version=ver if isinstance(ver, str) else None,
                        ecosystem="pypi",
                        line=_line_of(text, dep),
                    )
                )
        for name, target in project.get("scripts", {}).items():
            fa.scripts[str(name)] = str(target)

    def _requirements(self, file: InventoryFile, text: str, fa: FileAnalysis) -> None:
        dev = "dev" in file.name.lower() or "test" in file.name.lower()
        for i, raw in enumerate(text.splitlines(), 1):
            line = raw.split("#", 1)[0].strip()
            if not line or line.startswith(("-", "http", "git+")):
                continue
            m = _REQ_NAME.match(line)
            if m:
                version = (m.group(2) or "").split(";")[0].strip() or None
                fa.dependencies.append(
                    DependencyDef(name=m.group(1), version=version, ecosystem="pypi", dev=dev, line=i)
                )

    def _npm(self, file: InventoryFile, text: str, fa: FileAnalysis) -> None:
        data = json.loads(text)
        for key, dev in (("dependencies", False), ("devDependencies", True), ("peerDependencies", False)):
            for dep, ver in (data.get(key) or {}).items():
                fa.dependencies.append(
                    DependencyDef(
                        name=dep,
                        version=ver if isinstance(ver, str) else None,
                        ecosystem="npm",
                        dev=dev,
                        line=_line_of(text, f'"{dep}"'),
                    )
                )
        for name, cmd in (data.get("scripts") or {}).items():
            if isinstance(cmd, str):
                fa.scripts[str(name)] = cmd

    def _gomod(self, file: InventoryFile, text: str, fa: FileAnalysis) -> None:
        in_block = False
        for i, raw in enumerate(text.splitlines(), 1):
            line = raw.split("//", 1)[0].strip()
            if line.startswith("require ("):
                in_block = True
                continue
            if in_block and line == ")":
                in_block = False
                continue
            spec = (
                line[len("require ") :]
                if line.startswith("require ") and "(" not in line
                else line
                if in_block
                else ""
            )
            parts = spec.split()
            if len(parts) >= 2:
                fa.dependencies.append(DependencyDef(name=parts[0], version=parts[1], ecosystem="go", line=i))

    def _cargo(self, file: InventoryFile, text: str, fa: FileAnalysis) -> None:
        data = tomllib.loads(text)
        for key, dev in (("dependencies", False), ("dev-dependencies", True)):
            for dep, ver in (data.get(key) or {}).items():
                version = (
                    ver if isinstance(ver, str) else (ver.get("version") if isinstance(ver, dict) else None)
                )
                fa.dependencies.append(
                    DependencyDef(
                        name=dep, version=version, ecosystem="cargo", dev=dev, line=_line_of(text, dep)
                    )
                )

    # --- containers and orchestration ----------------------------------------------------------------------

    def _dockerfile(self, file: InventoryFile, text: str, fa: FileAnalysis) -> None:
        images: list[str] = []
        ports: list[str] = []
        command: str | None = None
        for raw in text.splitlines():
            line = raw.strip()
            upper = line.upper()
            if upper.startswith("FROM "):
                tokens = [t for t in line.split()[1:] if not t.startswith("--")]
                if tokens and tokens[0].lower() != "scratch":
                    images.append(tokens[0])
            elif upper.startswith("EXPOSE "):
                ports.extend(line.split()[1:])
            elif upper.startswith(("CMD ", "ENTRYPOINT ")):
                body = line.split(None, 1)[1].strip()
                if body.startswith("["):
                    try:
                        command = " ".join(str(t) for t in json.loads(body))
                    except ValueError:
                        command = body
                else:
                    command = body
        fa.entry_command = command
        fa.infra.append(
            InfraDef(
                resource_kind="dockerfile",
                name=file.path,
                provider="docker",
                start_line=1,
                end_line=max(1, text.count("\n") + 1),
                details={
                    k: v for k, v in {"images": images, "ports": ports, "entry_command": command}.items() if v
                },
            )
        )

    def _compose(self, file: InventoryFile, text: str, fa: FileAnalysis) -> None:
        data = yaml.safe_load(text) or {}
        services = data.get("services") if isinstance(data, dict) else None
        if not isinstance(services, dict):
            return
        base = file.directory
        for name, svc in services.items():
            if not isinstance(svc, dict):
                continue
            build = svc.get("build")
            context = dockerfile = None
            if isinstance(build, str):
                context = build
            elif isinstance(build, dict):
                context = str(build.get("context", "."))
                dockerfile = build.get("dockerfile")
            ctx_path = posixpath.normpath(posixpath.join(base, context)) if context is not None else None
            if ctx_path == ".":
                ctx_path = ""
            df_path = None
            if context is not None:
                df_path = posixpath.normpath(posixpath.join(ctx_path or "", str(dockerfile or "Dockerfile")))
            depends = svc.get("depends_on") or []
            env = svc.get("environment") or []
            env_names = (
                [str(k) for k in env]
                if isinstance(env, dict)
                else [str(e).split("=", 1)[0] for e in env if isinstance(e, str)]
            )
            command = svc.get("command")
            fa.compose_services.append(
                ComposeServiceDef(
                    name=str(name),
                    image=svc.get("image") if isinstance(svc.get("image"), str) else None,
                    build_context=ctx_path,
                    dockerfile=df_path,
                    command=" ".join(command) if isinstance(command, list) else command,
                    ports=[
                        str(p) if not isinstance(p, dict) else str(p.get("published", p.get("target", "")))
                        for p in svc.get("ports") or []
                    ],
                    depends_on=[str(d) for d in (depends.keys() if isinstance(depends, dict) else depends)],
                    env=env_names,
                    line=_line_of(text, f"{name}:"),
                )
            )

    def _terraform(self, file: InventoryFile, text: str, fa: FileAnalysis) -> None:
        import hcl2

        data = hcl2.loads(text)
        fa.infra.append(
            InfraDef(resource_kind="terraform", name=file.path, provider="terraform", start_line=1)
        )
        for block in data.get("resource", []):
            for raw_type, instances in block.items():
                rtype = _unquote(raw_type)
                for raw_name, body in instances.items():
                    rname = _unquote(raw_name)
                    kind = rtype
                    if rtype in _ENGINE_TYPES and isinstance(body, dict):
                        engine = _unquote(body.get("engine") or body.get("database_version") or "").lower()
                        for known in ("postgres", "mysql", "mariadb"):
                            if engine.startswith(known):
                                kind = f"{rtype}:{known}"
                    fa.infra.append(
                        InfraDef(
                            resource_kind=kind,
                            name=rname,
                            provider=rtype.split("_", 1)[0],
                            start_line=_line_of(text, f'"{rtype}" "{rname}"') or _line_of(text, rname),
                            details={"type": rtype},
                        )
                    )

    def _yaml(self, file: InventoryFile, text: str, fa: FileAnalysis) -> None:
        if file.path.startswith(".github/workflows/"):
            fa.infra.append(
                InfraDef(resource_kind="github_actions", name=file.path, provider="github", start_line=1)
            )
            return
        if file.name.lower() == ".gitlab-ci.yml":
            fa.infra.append(
                InfraDef(resource_kind="gitlab_ci", name=file.path, provider="gitlab", start_line=1)
            )
            return
        if "apiVersion:" not in text or "kind:" not in text:
            return
        for doc in yaml.safe_load_all(text):
            if not isinstance(doc, dict) or "kind" not in doc or "apiVersion" not in doc:
                continue
            raw_meta = doc.get("metadata")
            meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
            name = str(meta.get("name") or doc["kind"])
            fa.infra.append(
                InfraDef(
                    resource_kind="kubernetes",
                    name=name,
                    provider="kubernetes",
                    start_line=_line_of(text, f"name: {name}") or 1,
                    details={"k8s_kind": str(doc["kind"]), "images": sorted(_images_of(doc))},
                )
            )

    # --- docs ----------------------------------------------------------------------------------------------

    def _markdown(self, file: InventoryFile, text: str, fa: FileAnalysis) -> None:
        lines = text.split("\n")
        in_fence = False
        found: list[tuple[int, int, str]] = []
        for i, line in enumerate(lines, 1):
            if line.lstrip().startswith(("```", "~~~")):
                in_fence = not in_fence
            elif not in_fence and line.startswith("#") and len(line) <= _MAX_HEADING_LINE:
                # Plain scanning, not a regex: a backtracking pattern over untrusted lines is a ReDoS.
                body = line.lstrip("#")
                level = len(line) - len(body)
                title = body.strip().rstrip("#").strip()
                if level <= 6 and body[:1] in (" ", "\t") and title:
                    found.append((i, level, title))
        for idx, (start, level, title) in enumerate(found):
            end = found[idx + 1][0] - 1 if idx + 1 < len(found) else len(lines)
            fa.sections.append(
                DocSection(title=title, level=level, start_line=start, end_line=max(start, end))
            )


_MAX_YAML_NODES = 20_000


def _images_of(root: Any) -> set[str]:
    """Container images anywhere in a Kubernetes object (`containers[].image`, `initContainers[].image`).

    Iterative with a visited set and a node cap: YAML aliases share nodes, so a recursive walk of a hostile
    document could otherwise expand without bound.
    """
    out: set[str] = set()
    seen: set[int] = set()
    stack = [root]
    while stack and len(seen) < _MAX_YAML_NODES:
        node = stack.pop()
        if isinstance(node, dict | list):
            if id(node) in seen:
                continue
            seen.add(id(node))
        if isinstance(node, dict):
            image = node.get("image")
            if isinstance(image, str):
                out.add(image)
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return out
