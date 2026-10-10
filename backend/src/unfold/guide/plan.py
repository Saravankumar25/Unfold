"""Adaptive guide planner v2 (investigation engine §5, task 3.4).

A deterministic skeleton is built from the `ArchitectureModel` (project kind picks the backbone, complexity picks the
chapter count, every target is a RepoMap entity or component). One LLM call may refine titles, objectives, questions,
targets and dependencies *inside* that skeleton. The result is validated (known targets, acyclic dependencies, 2-6
questions, fixed key set); invalid model output falls back to the skeleton and is reported as `degraded`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, Field

from unfold.core.errors import ErrorCode, GenerationError
from unfold.core.logging import get_logger
from unfold.domain.enums import ModelCapability, Priority
from unfold.guide.generate import slugify
from unfold.guide.models import ChapterPlan, EvidenceKind, GuidePlan
from unfold.llm.schemas import LLMClient, LLMRequest, Message
from unfold.project_ir.complexity import score
from unfold.project_ir.schema import ProjectIR
from unfold.security.fence import UNTRUSTED_POLICY, untrusted
from unfold.security.secrets import redact_text
from unfold.understand.models import ArchitectureModel
from unfold.understand.repomap import RepoMap

log = get_logger(__name__)

MIN_CHAPTERS = 4
MAX_CHAPTERS = 14
MIN_QUESTIONS = 2
MAX_QUESTIONS = 6
MAX_TARGETS = 8
PLAN_PROMPT_VERSION = "2"
_HTTP = re.compile(r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|ANY|RPC) ")
_CONFIG_ROLES = frozenset({"config", "container", "iac", "ci"})


def _clip(text: str, limit: int) -> str:
    """One line, bounded: repository-derived names must not smuggle newlines into a prompt."""
    return " ".join(text.split())[:limit]


def chapter_budget(ir: ProjectIR) -> int:
    """Chapter count from the deterministic complexity score (0-100), clamped to 4-14."""
    return max(MIN_CHAPTERS, min(MAX_CHAPTERS, MIN_CHAPTERS + round(score(ir).score / 10)))


# ---- skeleton ---------------------------------------------------------------------------------------------


class _View:
    """What the selectors can see: the RepoMap, the architecture model and a few IR indexes."""

    def __init__(self, ir: ProjectIR, rm: RepoMap, arch: ArchitectureModel) -> None:
        self.ir, self.rm, self.arch = ir, rm, arch
        self.entries = arch.entry_points
        self.label = {e.id: rm.entry_label(e.id) or "" for e in self.entries}
        self.components = {c.id: c for c in arch.components if c.member_paths}
        self.files = {f.id: f for f in ir.files}

    def display(self, target: str) -> str:
        comp = self.components.get(target)
        if comp is not None:
            return comp.title
        label = self.label.get(target, "")
        ref = self.rm.ref(target)
        if _HTTP.match(label):
            return label
        return ref.name if ref else target

    def is_valid(self, target: str) -> bool:
        return target in self.components or self.rm.ref(target) is not None

    def code_files(self, n: int) -> list[str]:
        refs = [r for r in self.rm.files() if self.files[r.id].role == "source"]
        return [r.id for r in sorted(refs, key=lambda r: (-r.end_line, r.path))[:n]]

    def sized_components(self) -> list[str]:
        return [c.id for c in sorted(self.components.values(), key=lambda c: (-len(c.member_paths), c.id))]


def _by_label(v: _View, ok: Callable[[str], bool]) -> list[str]:
    return [e.id for e in v.entries if ok(v.label[e.id])][:MAX_TARGETS]


def _overview(v: _View) -> list[str]:
    return ([e.id for e in v.entries[:3]] + v.sized_components()[:2]) or v.code_files(3)


def _persistence(v: _View) -> list[str]:
    ids = [r.id for b in v.arch.persistence for r in b.access] + [s.id for s in v.ir.schemas]
    return [i for i in dict.fromkeys(ids) if v.rm.ref(i)][:MAX_TARGETS]


def _models(v: _View) -> list[str]:
    return [s.id for s in v.ir.schemas if v.rm.ref(s.id)][:MAX_TARGETS]


def _config(v: _View) -> list[str]:
    def wanted(f_id: str) -> bool:
        f = v.files[f_id]
        return f.role in _CONFIG_ROLES or (f.role == "manifest" and "lock" not in f.path.lower())

    ids = [i for i in sorted(v.files, key=lambda i: (v.files[i].tier, v.files[i].path)) if wanted(i)]
    return [i for i in ids if v.rm.ref(i)][:6]


def _api(v: _View) -> list[str]:
    ids = [s.id for s in v.ir.symbols if s.exported and s.kind in ("class", "function") and v.rm.ref(s.id)]
    return ids[:MAX_TARGETS] or v.sized_components()[:MAX_TARGETS]


_SELECT: dict[str, Callable[[_View], list[str]]] = {
    "overview": _overview,
    "routes": lambda v: _by_label(v, lambda s: bool(_HTTP.match(s))),
    "commands": lambda v: _by_label(v, lambda s: s in ("command", "main")) or v.sized_components()[:4],
    "jobs": lambda v: _by_label(
        v, lambda s: s.startswith(("queue consumer", "worker service", "cron service"))
    ),
    "entries": lambda v: [e.id for e in v.entries][:MAX_TARGETS] or v.sized_components()[:4],
    "components": lambda v: v.sized_components()[:MAX_TARGETS],
    "persistence": _persistence,
    "models": _models,
    "config": _config,
    "api": _api,
}


@dataclass(frozen=True)
class _Template:
    key: str
    title: str
    objective: str
    questions: tuple[str, ...]
    select: str
    evidence: tuple[EvidenceKind, ...]
    depth: str = "standard"
    visuals: tuple[str, ...] = ()
    minutes: float = 4.0


_T = {
    t.key: t
    for t in (
        _Template(
            "overview",
            "Overview",
            "Explain what {name} is for, who uses it and how its main parts fit together.",
            (
                "What does {name} do and what are its main parts?",
                "Where does execution start ({focus})?",
                "Which technologies and external services does it depend on?",
            ),
            "overview",
            ("definition", "config"),
            "overview",
            ("architecture",),
            3.0,
        ),
        _Template(
            "usage",
            "Commands and usage",
            "Show how a person runs {name} and what each entry point does.",
            (
                "Which commands or entry points exist ({focus}) and what does each one do?",
                "Which arguments, options or configuration does each entry point read?",
                "What happens when an entry point fails or receives bad input?",
            ),
            "commands",
            ("definition", "flow", "config"),
        ),
        _Template(
            "request-lifecycle",
            "Request lifecycle",
            "Follow a request from the route to the response, step by step.",
            (
                "Which routes does the service expose ({focus}) and what handles each?",
                "What does a request pass through between the route and the response?",
                "What happens when validation, a dependency or a store fails?",
            ),
            "routes",
            ("route", "flow", "definition"),
            "deep",
            ("request_flow",),
        ),
        _Template(
            "data-persistence",
            "Data and persistence",
            "Explain what data {name} stores, where, and which code reads or writes it.",
            (
                "Which data models and stores exist ({focus}) and how are they defined?",
                "Which code reads or writes each store?",
                "How would a developer change a model or add a field?",
            ),
            "persistence",
            ("model", "definition", "flow"),
            "standard",
            ("data_model",),
        ),
        _Template(
            "background-jobs",
            "Background work",
            "Explain the jobs, workers and queues, how they are triggered and what they do.",
            (
                "Which workers or queue consumers exist ({focus}) and what triggers each?",
                "What does a job do from start to finish?",
                "What happens when a job fails or is retried?",
            ),
            "jobs",
            ("flow", "definition", "config"),
            "standard",
            ("ai_pipeline",),
        ),
        _Template(
            "pipeline-stages",
            "Pipeline stages",
            "Walk through the stages data goes through from input to output.",
            (
                "What are the stages and entry points ({focus}) and in what order do they run?",
                "What does each stage take in and hand on?",
                "What happens when a stage fails?",
            ),
            "entries",
            ("flow", "definition"),
            "deep",
            ("ai_pipeline",),
        ),
        _Template(
            "config-deploy",
            "Configuration and deployment",
            "Explain how {name} is configured, built and deployed.",
            (
                "Which configuration, manifests and build files exist ({focus}) and what do they set?",
                "How is the project built, started or deployed?",
                "Which settings would a developer change first?",
            ),
            "config",
            ("config", "definition"),
            "standard",
            ("deployment",),
        ),
        _Template(
            "public-api",
            "Public API",
            "Explain the interface {name} exposes to the code that uses it.",
            (
                "What does the public interface consist of ({focus})?",
                "How is the main entry point of the library used?",
                "How would a developer extend or change this interface?",
            ),
            "api",
            ("definition", "test"),
        ),
        _Template(
            "internals",
            "How it works inside",
            "Explain the internal parts of {name} and how they work together.",
            (
                "What are the internal parts ({focus}) and what is each responsible for?",
                "How do these parts call or depend on each other?",
                "Where would a developer change behaviour, and what could go wrong?",
            ),
            "components",
            ("definition", "flow"),
            "deep",
            ("module_dependencies",),
        ),
        _Template(
            "structure",
            "Application structure",
            "Explain how the application is organised and how its parts interact.",
            (
                "What are the main parts ({focus}) and what is each for?",
                "How do the parts depend on each other?",
                "Where does state or data live?",
            ),
            "components",
            ("definition", "flow"),
            "standard",
            ("module_dependencies",),
        ),
        _Template(
            "packages",
            "Packages",
            "Explain the packages in this repository and how they relate.",
            (
                "Which packages exist ({focus}) and what is each for?",
                "How do the packages depend on each other?",
                "Which package would a developer change for a given task?",
            ),
            "components",
            ("definition", "config"),
            "standard",
            ("module_dependencies",),
        ),
        _Template(
            "how-it-runs",
            "How it runs",
            "Explain how {name} starts and what it does when it runs.",
            (
                "Where does execution start ({focus})?",
                "What does it call next, and in what order?",
                "What happens when something fails?",
            ),
            "entries",
            ("flow", "definition"),
            "standard",
        ),
    )
}
_BACKBONE: dict[str, tuple[str, ...]] = {
    "cli": ("overview", "usage", "internals", "config-deploy"),
    "library": ("overview", "public-api", "internals", "config-deploy"),
    "web_service": ("overview", "request-lifecycle", "data-persistence", "background-jobs", "config-deploy"),
    "pipeline": ("overview", "pipeline-stages", "data-persistence", "background-jobs", "config-deploy"),
    "app": ("overview", "structure", "data-persistence", "config-deploy"),
    "monorepo": ("overview", "packages", "config-deploy"),
    "unknown": ("overview", "how-it-runs", "internals", "config-deploy"),
}


def _chapter(
    v: _View,
    t: _Template,
    targets: list[str],
    *,
    kind: str,
    key: str | None = None,
    title: str | None = None,
    objective: str | None = None,
) -> ChapterPlan:
    name = _clip(v.ir.project.name, 80)
    focus = ", ".join(_clip(v.display(x), 60) for x in targets[:3])
    return ChapterPlan(
        key=key or t.key,
        title=title or t.title,
        kind="backbone" if kind == "backbone" else "core",
        purpose=objective or t.objective.format(name=name),
        objective=objective or t.objective.format(name=name),
        target_minutes=t.minutes,
        visual_needs=list(t.visuals),
        depth=t.depth,  # type: ignore[arg-type]
        questions=[q.format(name=name, focus=focus) for q in t.questions],
        targets=targets,
        expected_evidence=list(t.evidence),
    )


_COMPONENT = _Template(
    "component",
    "",
    "",
    (
        "What is {focus} responsible for and what are its main entry points?",
        "Which data or other parts of the project does {focus} work with?",
        "What happens when something goes wrong in {focus}, and how would a developer change it?",
    ),
    "components",
    ("definition", "flow"),
)


def build_skeleton(ir: ProjectIR, rm: RepoMap, arch: ArchitectureModel) -> list[ChapterPlan]:
    """Deterministic chapters: the project kind's backbone, then one chapter per large component up to the
    complexity budget. A chapter whose selector finds nothing is dropped, so every chapter has a valid target."""
    v = _View(ir, rm, arch)
    want = chapter_budget(ir)
    chapters: list[ChapterPlan] = []
    for key in _BACKBONE[arch.project_kind]:
        t = _T[key]
        targets = _SELECT[t.select](v)
        if targets:
            chapters.append(_chapter(v, t, targets, kind="backbone"))
    chapters = chapters[:want]
    used = {x for c in chapters for x in c.targets}
    taken = {c.key for c in chapters}
    for comp_id in v.sized_components():
        if len(chapters) >= want:
            break
        if comp_id in used:
            continue
        comp = v.components[comp_id]
        key, n = slugify(comp.title), 2
        while key in taken:
            key, n = f"{key}-{n}", n + 1
        taken.add(key)
        obj = f"Explain what {_clip(comp.title, 80)} is responsible for and how the rest of the project uses it."
        chapters.append(
            _chapter(
                v, _COMPONENT, [comp_id], kind="core", key=key, title=_clip(comp.title, 120), objective=obj
            )
        )
    for file_id in v.code_files(MAX_CHAPTERS):  # tiny repos: one chapter per large file until the minimum
        if len(chapters) >= min(want, MIN_CHAPTERS):
            break
        if file_id in {x for c in chapters for x in c.targets}:
            continue
        ref = rm.ref(file_id)
        if ref is None:
            continue
        key, n = slugify(ref.path), 2
        while key in taken:
            key, n = f"{key}-{n}", n + 1
        taken.add(key)
        obj = f"Explain what {ref.path} does and how the rest of the project uses it."
        chapters.append(
            _chapter(v, _COMPONENT, [file_id], kind="core", key=key, title=ref.path, objective=obj)
        )
    if chapters:
        first = chapters[0].key
        chapters = [c if c.key == first else c.model_copy(update={"depends_on": [first]}) for c in chapters]
    return chapters


# ---- validation -------------------------------------------------------------------------------------------


def _has_cycle(deps: Mapping[str, Sequence[str]]) -> bool:
    state: dict[str, int] = {}  # 1 = on the stack, 2 = done

    def visit(k: str) -> bool:
        if state.get(k) == 1:
            return True
        if state.get(k) == 2:
            return False
        state[k] = 1
        if any(visit(d) for d in deps.get(k, ()) if d in deps):
            return True
        state[k] = 2
        return False

    return any(visit(k) for k in deps)


def validate_chapters(chapters: Sequence[ChapterPlan], valid_target: Callable[[str], bool]) -> list[str]:
    """Human-readable problems; empty means the plan is usable."""
    problems: list[str] = []
    keys = [c.key for c in chapters]
    if not 1 <= len(chapters) <= MAX_CHAPTERS:
        problems.append(f"{len(chapters)} chapters (allowed 1-{MAX_CHAPTERS})")
    if len(set(keys)) != len(keys):
        problems.append("duplicate chapter keys")
    for c in chapters:
        if not c.objective.strip() or not c.title.strip():
            problems.append(f"{c.key}: empty title or objective")
        if not MIN_QUESTIONS <= len(c.questions) <= MAX_QUESTIONS:
            problems.append(
                f"{c.key}: {len(c.questions)} questions (allowed {MIN_QUESTIONS}-{MAX_QUESTIONS})"
            )
        if not c.targets:
            problems.append(f"{c.key}: no target")
        problems += [f"{c.key}: unknown target {t!r}" for t in c.targets if not valid_target(t)]
        problems += [f"{c.key}: unknown dependency {d!r}" for d in c.depends_on if d not in keys]
        if c.key in c.depends_on:
            problems.append(f"{c.key}: depends on itself")
    if _has_cycle({c.key: c.depends_on for c in chapters}):
        problems.append("dependency cycle")
    return problems


# ---- LLM refinement ---------------------------------------------------------------------------------------


class ChapterRefinement(BaseModel):
    key: str
    title: str
    objective: str
    questions: list[str]
    targets: list[str] = Field(default_factory=list)  # names from the catalogue; empty keeps the skeleton's
    depends_on: list[str] | None = None  # chapter keys; None keeps the skeleton's


class PlanRefinement(BaseModel):
    chapters: list[ChapterRefinement]


_SYSTEM = (
    "You refine a reading-guide outline for a software repository. You receive a deterministic outline (chapter "
    "keys, titles, objectives, research questions, targets) and a catalogue of real targets. Return every chapter "
    "key exactly once; do not add, remove or rename keys. Improve titles so they name this repository's real "
    "concepts, make each objective one concrete sentence, and write 2-6 research questions per chapter that can be "
    "answered from the code (names, routes, call order, data, failure behaviour). A target is a name copied "
    "verbatim from the catalogue; leave `targets` empty to keep the outline's. `depends_on` lists chapter keys the "
    "reader should have read first, no cycles; leave it null to keep the outline's. Never invent components that "
    "are not in the catalogue.\n\n" + UNTRUSTED_POLICY
)


def _catalogue(v: _View) -> list[str]:
    names = [c.title for c in v.components.values()]
    names += [v.display(e.id) for e in v.entries]
    names += [r.path for r in v.rm.files() if v.files[r.id].role == "source"][:40]
    return [_clip(n, 100) for n in dict.fromkeys(names)][:80]


def _resolve(v: _View, name: str) -> str | None:
    n = name.strip()
    for c in v.components.values():
        if n in (c.title, c.id):
            return c.id
    hits = [e.id for e in v.entries if v.label[e.id] == n]
    if len(hits) == 1:
        return hits[0]
    ref = v.rm.symbol(n)
    return ref.id if ref else None


def _merge(
    skeleton: list[ChapterPlan], refined: PlanRefinement, v: _View
) -> tuple[list[ChapterPlan], list[str]]:
    by_key = {r.key: r for r in refined.chapters}
    if len(by_key) != len(refined.chapters) or set(by_key) != {c.key for c in skeleton}:
        return [], ["the refinement does not list exactly the outline's chapter keys"]
    out: list[ChapterPlan] = []
    problems: list[str] = []
    for c in skeleton:
        r = by_key[c.key]
        targets = c.targets
        if r.targets:
            resolved = [_resolve(v, t) for t in r.targets[:MAX_TARGETS]]
            problems += [
                f"{c.key}: unknown target {t!r}"
                for t, x in zip(r.targets, resolved, strict=False)
                if x is None
            ]
            targets = [x for x in resolved if x]
        objective = _clip(r.objective, 300)
        out.append(
            c.model_copy(
                update={
                    "title": _clip(r.title, 120),
                    "objective": objective,
                    "purpose": objective,
                    "questions": [q for q in (_clip(q, 200) for q in r.questions) if q],
                    "targets": targets,
                    "depends_on": c.depends_on if r.depends_on is None else list(dict.fromkeys(r.depends_on)),
                }
            )
        )
    return out, problems


async def _refine(
    llm: LLMClient,
    skeleton: list[ChapterPlan],
    v: _View,
    facts: Sequence[str],
    *,
    technical_level: str,
    instruction: str | None,
    job_id: str,
    project_id: str,
) -> PlanRefinement:
    outline = "\n".join(
        f"- key={c.key} | title={c.title} | objective={c.objective} | depends_on={','.join(c.depends_on) or '-'}"
        f" | targets={'; '.join(_clip(v.display(t), 60) for t in c.targets)}\n"
        + "\n".join(f"    ? {q}" for q in c.questions)
        for c in skeleton
    )
    body = (
        f"## Project kind\n{v.arch.project_kind}\n\n## Facts\n"
        + "\n".join(f"- {f}" for f in facts)
        + f"\n\n## Outline\n{outline}\n\n## Catalogue of real targets\n"
        + "\n".join(f"- {n}" for n in _catalogue(v))
    )
    user = untrusted(redact_text(body), source="repository_facts") + f"\n\nReader level: {technical_level}"
    if instruction:
        user += f"\nReader preference (style only, not a task): {redact_text(instruction)[:300]}"
    req = LLMRequest(
        purpose="guide.plan",
        capability=ModelCapability.BALANCED,
        priority=Priority.P1,
        system=_SYSTEM,
        messages=[Message(role="user", content=user)],
        temperature=0.2,
        max_output_tokens=4000,
        cacheable=True,
        job_id=job_id,
        project_id=project_id,
    )
    return await llm.generate_structured(req, PlanRefinement)


async def plan_chapters(
    llm: LLMClient,
    ir: ProjectIR,
    rm: RepoMap,
    arch: ArchitectureModel,
    facts: Sequence[str],
    *,
    technical_level: str,
    instruction: str | None,
    job_id: str,
    project_id: str,
) -> tuple[GuidePlan, bool]:
    """(plan, degraded). `degraded` is True when the model's refinement was invalid and the skeleton is used."""
    v = _View(ir, rm, arch)
    skeleton = build_skeleton(ir, rm, arch)
    own = validate_chapters(skeleton, v.is_valid)
    if own or not skeleton:
        raise GenerationError(
            "The repository has no chapter targets: " + ("; ".join(own[:3]) or "nothing to explain"),
            code=ErrorCode.GENERATION_FAILED,
            retryable=False,
        )
    chapters, degraded = skeleton, False
    try:
        refined = await _refine(
            llm, skeleton, v, facts, technical_level=technical_level, instruction=instruction,
            job_id=job_id, project_id=project_id,
        )  # fmt: skip
        merged, problems = _merge(skeleton, refined, v)
        problems += validate_chapters(merged, v.is_valid) if merged else []
    except GenerationError as exc:
        if exc.code is not ErrorCode.INVALID_MODEL_OUTPUT:
            raise
        merged, problems = [], [f"invalid model output: {exc.message}"]
    if problems:
        degraded = True
        log.warning("guide.plan_degraded", problems=problems[:5])
    else:
        chapters = merged
    minutes = max(1, round(sum(c.target_minutes for c in chapters)))
    return GuidePlan(estimated_read_minutes=minutes, sections=chapters), degraded
