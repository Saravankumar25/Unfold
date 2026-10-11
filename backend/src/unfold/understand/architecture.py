"""ArchitectureModel from a `ProjectIR` (investigation engine §4-5). Pure and deterministic; the pipeline calls
`build_architecture` once the IR exists. Every list is sorted; nothing is invented: what cannot be resolved is
listed in `uncertainty`."""

from __future__ import annotations

import posixpath
from collections import defaultdict

from unfold.core.ids import entity_id
from unfold.graph import communities
from unfold.project_ir.builder import DYNAMIC_CALLEES
from unfold.project_ir.schema import ProjectIR
from unfold.repo_intel.analyzers.base import PROGRAMMING_LANGUAGES
from unfold.understand.flows import trace_flow
from unfold.understand.models import (
    ArchitectureModel,
    Boundary,
    Component,
    Dependency,
    FlowTrace,
    ProjectKind,
)
from unfold.understand.repomap import RepoMap

MAX_FLOWS = 12
MAX_ACCESS = 20
MAX_UNCERTAINTY_FILES = 10
_RUNNABLE = frozenset({"web", "api", "worker", "cron"})
_ACCESS_RELS = frozenset({"USES", "READS_FROM", "WRITES_TO", "DEPENDS_ON"})
_APP = frozenset({"frontend_app", "mobile_app", "desktop_app", "web_app", "fullstack"})
_PIPELINE = frozenset({"data_pipeline", "background_worker", "ml_training"})


# a manifest under one of these directories belongs to docs, tooling or samples, not to a package of the project
_NOT_PACKAGES = frozenset({"docs", "doc", "test", "tests", "example", "examples", "script", "scripts"})
_WORKSPACE_MARKERS = frozenset({"pnpm-workspace.yaml", "lerna.json", "nx.json", "turbo.json", "go.work"})


def _package_roots(ir: ProjectIR) -> set[str]:
    return {
        d
        for dep in ir.dependencies
        if (d := posixpath.dirname(dep.manifest_path)) and not _NOT_PACKAGES & set(d.split("/"))
    }


def _monorepo(ir: ProjectIR) -> bool:
    """Three packages below the root, or two with a workspace marker at the root: a backend/ plus a frontend/
    (plus a docs/requirements.txt) is one service, not a monorepo."""
    marked = any(f.path in _WORKSPACE_MARKERS for f in ir.files)
    return len(_package_roots(ir)) >= (2 if marked else 3)


def _packages(ir: ProjectIR) -> list[Component]:
    """One component per package root (a file belongs to the deepest root above it), for a monorepo, where import
    edges join the packages into a single community."""
    roots = sorted(_package_roots(ir), key=lambda r: (-r.count("/"), r))
    members: dict[str, list[str]] = defaultdict(list)
    for p in sorted(f.path for f in ir.files):
        if root := next((r for r in roots if p.startswith(r + "/")), None):
            members[root].append(p)
    pid = ir.project.project_id
    return [
        Component(
            id=entity_id(pid, "package", root),
            title=root,
            member_paths=paths,
            responsibility=f"package at {root}; {len(paths)} files",
        )
        for root, paths in sorted(members.items())
    ]


def _kind(ir: ProjectIR) -> ProjectKind:
    cats = set(ir.project.categories)
    if _monorepo(ir):
        return "monorepo"
    if "cli_tool" in cats and not ir.endpoints:
        return "cli"
    if ir.endpoints or "api_service" in cats:
        return "web_service"
    if cats & _PIPELINE:
        return "pipeline"
    if cats & _APP:
        return "app"
    if "library" in cats:
        return "library"
    return "unknown"


def _components(ir: ProjectIR) -> list[Component]:
    path_of = {f.id: f.path for f in ir.files}
    out: list[Component] = _packages(ir) if _monorepo(ir) else []
    packaged = {p for c in out for p in c.member_paths}
    for c in communities.detect(ir):
        paths = sorted(path_of[f] for f in c.member_file_ids if f in path_of)
        if paths and packaged.issuperset(paths):
            continue  # the packages above already say it
        pset = set(paths)
        keys = ", ".join(path_of[k].rsplit("/", 1)[-1] for k in c.key_files if k in path_of)
        facts = [f"{len(paths)} files", f"key files: {keys}"]
        if routes := sum(1 for e in ir.endpoints if e.path in pset):
            facts.append(f"{routes} endpoints")
        if models := sorted({s.name for s in ir.schemas if s.path in pset})[:5]:
            facts.append(f"models: {', '.join(models)}")
        out.append(
            Component(
                id=c.id,
                title=c.label_hint,
                member_paths=paths,
                responsibility="; ".join(facts),
                community=c.id,
            )
        )
    for sv in ir.services:
        if sv.service_kind in _RUNNABLE:
            entry = path_of.get(sv.entrypoint_file_id or "")
            where = [entry] if entry else ([sv.path] if sv.path else [])
            text = f"{sv.service_kind} service" + (f", entry {entry}" if entry else "")
            out.append(Component(id=sv.id, title=sv.name, member_paths=where, responsibility=text))
    return out


def _persistence(ir: ProjectIR, rm: RepoMap) -> tuple[list[Boundary], list[str]]:
    access: dict[str, set[str]] = defaultdict(set)
    store_ids = {d.id for d in ir.data_stores}
    schema_ids = {s.id for s in ir.schemas}
    schema_users: set[str] = set()
    for r in ir.relationships:
        if r.type in _ACCESS_RELS and r.target_id in store_ids:
            access[r.target_id].add(r.source_id)
        elif r.type in ("READS_FROM", "WRITES_TO") and r.target_id in schema_ids:
            schema_users.add(r.source_id)
    notes: list[str] = []
    orm_stores = [d for d in ir.data_stores if d.category in ("relational", "document")]
    if schema_users and len(orm_stores) == 1:
        access[orm_stores[0].id] |= schema_users
    elif schema_users:
        notes.append(
            f"model access by {len(schema_users)} symbols is not attributed to one of {len(orm_stores)} stores"
        )
    out = []
    for d in sorted(ir.data_stores, key=lambda d: (d.name, d.id)):
        refs = sorted(
            (ref for i in access.get(d.id, ()) if (ref := rm.ref(i))),
            key=lambda ref: (ref.path, ref.start_line, ref.id),
        )
        out.append(
            Boundary(store=d.name, technology=d.technology, category=d.category, access=refs[:MAX_ACCESS])
        )
    return out, notes


def _uncertainty(ir: ProjectIR, flows: list[FlowTrace], notes: list[str], untraced: int) -> list[str]:
    out = list(notes)
    dynamic = sorted({u.path for u in ir.unresolved_calls if u.name.rsplit(".", 1)[-1] in DYNAMIC_CALLEES})
    out += [f"dynamic dispatch in {p}" for p in dynamic[:MAX_UNCERTAINTY_FILES]]
    by_file: dict[str, int] = defaultdict(int)
    for u in ir.unresolved_calls:
        if u.path not in dynamic:
            by_file[u.path] += 1
    out += [
        f"{by_file[p]} calls to project-named symbols not resolved in {p}"
        for p in sorted(by_file)[:MAX_UNCERTAINTY_FILES]
    ]
    file_lang = {f.id: f.language for f in ir.files}
    supported = {file_lang.get(s.file_id) for s in ir.symbols}
    code_langs = {f.language for f in ir.files if f.language in PROGRAMMING_LANGUAGES and f.tier <= 2}
    out += [
        f"unsupported language: {lang} (no symbols extracted)"
        for lang in sorted(filter(None, code_langs - supported))
    ]
    if failed := int(ir.stats.get("parse_error_files", 0) or 0):
        out.append(f"{failed} files failed to parse")
    big = list(ir.stats.get("oversized_code_files") or [])
    out += [f"{p} was skipped by the file size cap and is not analysed" for p in big[:MAX_UNCERTAINTY_FILES]]
    if len(big) > MAX_UNCERTAINTY_FILES:
        out.append(f"{len(big) - MAX_UNCERTAINTY_FILES} more code files were skipped by the file size cap")
    out += [f"flow from {f.entry.name} was cut by the depth or fan-out cap" for f in flows if f.truncated]
    if untraced:
        out.append(f"{untraced} entry points were not traced (cap {MAX_FLOWS})")
    return out


def build_architecture(ir: ProjectIR) -> ArchitectureModel:
    rm = RepoMap(ir)
    entries = rm.entry_points()
    flows = [trace_flow(rm, e.id) for e in entries[:MAX_FLOWS]]
    persistence, notes = _persistence(ir, rm)
    uncertainty = _uncertainty(ir, flows, notes, max(0, len(entries) - MAX_FLOWS))
    if not entries:
        uncertainty.append("no entry points detected")
    external = [
        Dependency(name=d.name, ecosystem=d.ecosystem, version=d.version, manifest_path=d.manifest_path)
        for d in sorted(ir.dependencies, key=lambda d: (d.ecosystem, d.name))
        if not d.dev
    ]
    return ArchitectureModel(
        project_kind=_kind(ir),
        components=_components(ir),
        entry_points=entries,
        flows=flows,
        persistence=persistence,
        external=external,
        uncertainty=uncertainty,
    )
