"""Technology and project-category detection (LLD §7). Pure: file analyses in, `DetectionResult` out.

Matching is by name only (dependency names, import prefixes, container images, infra resource kinds), using the
versioned catalogue in `rules.py`. Nothing is resolved over the network and nothing from the repository runs.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field

from unfold.repo_intel.models import (
    CategoryScore,
    Detected,
    DetectionResult,
    Evidence,
    FileAnalysis,
)
from unfold.repo_intel.rules import (
    AI_DEPENDENCY_RULES,
    AI_IMPORT_RULES,
    ALL_TECH_RULES,
    CATEGORY_THRESHOLD,
    CLI_APP_OBJECTS,
    PROJECT_CATEGORIES,
    RULES_BY_TECH,
    RULES_VERSION,
    WEB_APP_OBJECTS,
    WORKER_APP_OBJECTS,
    TechRule,
    canonical_dep_name,
    dep_matches,
    image_repository,
    module_matches,
)

MAX_EVIDENCE = 5
_CONFIDENCE = {"import": 1.0, "dependency": 0.9, "image": 0.9, "infra": 0.9, "code": 0.8}

_EXACT_DEPS: dict[str, list[TechRule]] = defaultdict(list)
_PREFIX_DEPS: list[tuple[str, TechRule]] = []
_IMPORT_PREFIXES: dict[str, list[TechRule]] = defaultdict(list)
_IMAGES: dict[str, list[TechRule]] = defaultdict(list)
_INFRA: dict[str, list[TechRule]] = defaultdict(list)
for _rule in ALL_TECH_RULES:
    for _dep in _rule.deps:
        if _dep.endswith("*"):
            _PREFIX_DEPS.append((_dep, _rule))
        else:
            _EXACT_DEPS[_dep].append(_rule)
    for _imp in _rule.imports:
        _IMPORT_PREFIXES[_imp].append(_rule)
    for _img in _rule.images:
        _IMAGES[_img].append(_rule)
    for _kind in _rule.infra:
        _INFRA[_kind].append(_rule)


@dataclass
class _Hit:
    rule: TechRule
    confidence: float = 0.0
    evidence: list[Evidence] = field(default_factory=list)

    def add(self, ev: Evidence, confidence: float | None = None) -> None:
        self.confidence = max(
            self.confidence, confidence if confidence is not None else _CONFIDENCE[ev.source]
        )
        if len(self.evidence) < MAX_EVIDENCE:
            self.evidence.append(ev)


def _import_candidates(module: str) -> list[str]:
    """Every prefix of `module` that ends on a `.` or `/` boundary, plus the module itself."""
    out = [module]
    for i, ch in enumerate(module):
        if ch in "./" and i > 0:
            out.append(module[:i])
            out.append(module[: i + 1])
    return out


def detect(analyses: Mapping[str, FileAnalysis]) -> DetectionResult:
    hits: dict[str, _Hit] = {}
    ai: dict[tuple[str, str], Detected] = {}

    def hit(rule: TechRule) -> _Hit:
        return hits.setdefault(rule.technology, _Hit(rule))

    def ai_hit(kind: str, tech: str, ev: Evidence) -> None:
        key = (kind, tech)
        found = ai.setdefault(
            key, Detected(kind="ai", name=tech, category=kind, technology=tech, confidence=0.0)
        )
        found.confidence = max(found.confidence, _CONFIDENCE[ev.source])
        if len(found.evidence) < MAX_EVIDENCE:
            found.evidence.append(ev)

    for path in sorted(analyses):
        fa = analyses[path]
        for d in fa.dependencies:
            name = canonical_dep_name(d.name, d.ecosystem)
            ev = Evidence(path=path, line=d.line, source="dependency")
            for rule in _EXACT_DEPS.get(name, ()):
                hit(rule).add(ev)
            for pattern, rule in _PREFIX_DEPS:
                if dep_matches(name, pattern):
                    hit(rule).add(ev)
            for ai_rule in AI_DEPENDENCY_RULES:
                if dep_matches(name, ai_rule.prefix):
                    ai_hit(ai_rule.component_kind, ai_rule.technology, ev)
        for imp in fa.imports:
            if not imp.external:
                continue
            ev = Evidence(path=path, line=imp.line, source="import")
            seen: set[str] = set()
            for cand in _import_candidates(imp.module):
                for rule in _IMPORT_PREFIXES.get(cand, ()):
                    if rule.technology not in seen and any(
                        module_matches(imp.module, p) for p in rule.imports
                    ):
                        seen.add(rule.technology)
                        hit(rule).add(ev)
            for ai_rule in AI_IMPORT_RULES:
                if module_matches(imp.module, ai_rule.prefix):
                    ai_hit(ai_rule.component_kind, ai_rule.technology, ev)
        for svc in fa.compose_services:
            if svc.image:
                ev = Evidence(path=path, line=svc.line, source="image")
                for rule in _IMAGES.get(image_repository(svc.image), ()):
                    hit(rule).add(ev)
        for res in fa.infra:
            ev = Evidence(path=path, line=res.start_line, source="infra")
            for rule in _INFRA.get(res.resource_kind, ()):
                hit(rule).add(ev)
        for hint in fa.ai_hints:
            ai_hit(hint.component_kind, hint.technology, Evidence(path=path, line=hint.line, source="code"))

    # `implies`: a framework evidencing another technology (arq implies redis) at a stated confidence.
    for found in list(hits.values()):
        for tech, conf in found.rule.implies:
            target = RULES_BY_TECH.get(tech)
            if target is not None and found.evidence:
                hit(target).add(found.evidence[0], min(conf, found.confidence))

    result = DetectionResult(rules_version=RULES_VERSION)
    for entry in sorted(hits.values(), key=lambda x: (x.rule.kind, -x.confidence, x.rule.technology)):
        detected = Detected(
            kind=entry.rule.kind,
            name=entry.rule.name,
            category=entry.rule.category,
            technology=entry.rule.technology,
            confidence=round(entry.confidence, 2),
            evidence=entry.evidence,
        )
        {
            "framework": result.frameworks,
            "datastore": result.datastores,
            "queue": result.queues,
            "external_service": result.external_services,
            "ai": result.ai_components,
        }[entry.rule.kind].append(detected)
    result.ai_components.extend(sorted(ai.values(), key=lambda d: (d.category, d.technology)))
    scores = _score_categories(analyses, result)
    result.category_scores = scores
    result.categories = [s for s in scores if s.score >= CATEGORY_THRESHOLD]
    return result


def _score_categories(analyses: Mapping[str, FileAnalysis], det: DetectionResult) -> list[CategoryScore]:
    app_objects = {o for fa in analyses.values() for o in fa.app_objects}
    has_routes = any(fa.routes for fa in analyses.values())
    has_manifest = any(fa.dependencies for fa in analyses.values())
    fw_cats = {d.category for d in det.frameworks}
    fw_techs = {d.technology for d in det.frameworks}
    ai_kinds = {d.category for d in det.ai_components}
    compose_builds = sum(1 for fa in analyses.values() for s in fa.compose_services if s.build_context)
    infra_resources = sum(len(fa.infra) for fa in analyses.values())
    web_backend = bool(app_objects & WEB_APP_OBJECTS) or has_routes or "web_backend" in fw_cats
    frontend = "web_frontend" in fw_cats

    signals: dict[str, list[tuple[float, str]]] = {c: [] for c in PROJECT_CATEGORIES}

    def sig(cat: str, weight: float, why: str) -> None:
        signals[cat].append((weight, why))

    if web_backend:
        sig("api_service", 0.7, "web framework or routes")
        sig("web_app", 0.5, "web framework or routes")
    if frontend:
        sig("frontend_app", 0.7, "frontend framework")
        sig("web_app", 0.3, "frontend framework")
    if web_backend and frontend:
        sig("fullstack", 0.8, "backend and frontend frameworks")
    if ai_kinds & {"vector_search", "embedding", "lexical_search"} and "llm_client" in ai_kinds:
        sig("rag_system", 0.8, "retrieval components and an LLM client")
    elif "vector_search" in ai_kinds or "embedding" in ai_kinds:
        sig("rag_system", 0.5, "retrieval components")
    if "llm_client" in ai_kinds or "llm_framework" in ai_kinds:
        sig("llm_application", 0.7, "LLM client or framework")
    if "agent" in ai_kinds:
        sig("agent_system", 0.7, "agent framework")
    if "training" in ai_kinds or "experiment_tracking" in ai_kinds:
        sig("ml_training", 0.7, "training framework")
    if "data" in fw_cats:
        sig("data_pipeline", 0.6, "data framework")
    if app_objects & CLI_APP_OBJECTS or "cli" in fw_cats:
        sig("cli_tool", 0.8, "CLI framework")
    if app_objects & WORKER_APP_OBJECTS or any(d.category == "task_queue" for d in det.queues):
        sig("background_worker", 0.6, "task queue worker")
    if compose_builds >= 3:
        sig("microservices", 0.6, "three or more built compose services")
    if infra_resources and infra_resources >= len(analyses) // 2:
        sig("infrastructure", 0.6, "mostly infrastructure definitions")
    if "mobile" in fw_cats:
        sig("mobile_app", 0.8, "mobile framework")
    if "desktop" in fw_cats:
        sig("desktop_app", 0.8, "desktop framework")
    if has_manifest and not (web_backend or frontend or app_objects & CLI_APP_OBJECTS):
        sig("library", 0.5, "package manifest without an application entry")
    if {"nextjs", "nuxt", "remix", "sveltekit"} & fw_techs:
        sig("fullstack", 0.5, "server-rendered frontend framework")

    out = [
        CategoryScore(
            category=cat,
            score=round(min(1.0, sum(w for w, _ in items)), 2),
            signals=[why for _, why in items],
        )
        for cat, items in signals.items()
    ]
    return sorted(out, key=lambda s: (-s.score, s.category))
