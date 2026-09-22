"""Deterministic query planner (LLD §11, ESD §13, AI infra §21-§22).

`plan_query` turns a user question / guide objective into a `RetrievalPlan` with no model call:

1. identifier extraction — backticks, paths, routes, dotted names, camelCase / PascalCase, snake_case, `f()`;
2. intent keyword rules → `RetrievalIntent` (phrases weigh more than single words; fixed tie-break order);
3. a bounded, intent-agnostic expansion table ("auth" → login/token/jwt/session, "db" → database/model/…);
4. IR matching — identifiers and salient terms are matched against IR symbol / file / schema names, giving
   `target_symbols` (IR entity ids, directly usable with `ChunkRepository.for_symbols`) and graph seeds;
5. desired evidence types and structural filters by intent and keywords.

`structural_filters` keys (all JSON-serializable): `identifiers`, `terms`, `expansions`, `paths`, `routes`,
`http_methods`, `entity_kinds` — consumed by the structural retriever and by `sufficiency.assess`.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from unfold.domain.enums import ContentType, RetrievalIntent, RetrievalMode
from unfold.graph.models import SubsystemCandidate
from unfold.indexing.tokenize import STOPWORDS, tokenize_query
from unfold.project_ir.schema import ProjectIR
from unfold.retrieval.config import Breadth, default_breadth
from unfold.retrieval.models import RetrievalPlan

MAX_IDENTIFIERS = 12
MAX_TARGET_SYMBOLS = 12
MAX_SEEDS = 10
MAX_EXPANSIONS = 12
MAX_EXPANSIONS_PER_KEY = 6
MAX_SUBSYSTEMS = 3
MAX_TERMS = 16
_GENERIC_NAME_LIMIT = 5  # a plain word matching more symbols than this is too generic to target

_CODE_EXT = (
    "py|pyi|ts|tsx|js|jsx|mjs|cjs|go|rs|java|kt|kts|rb|php|cs|cpp|cc|c|h|hpp|swift|scala|sql|md|mdx|rst|"
    "yml|yaml|toml|json|ini|cfg|conf|tf|hcl|sh|bash|ps1|prisma|graphql|gql|proto|vue|svelte|html|css|scss|"
    "env|lock|txt|ipynb|dockerfile|gradle|xml"
)
_EXT_SET = frozenset(_CODE_EXT.split("|"))
_BACKTICK = re.compile(r"`([^`\n]{1,160})`")
_ROUTE = re.compile(
    r"(?:^|(?<=[\s\"'(]))(?:(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+)?(/[A-Za-z0-9_{}:.<>\-/]*)"
)
_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_PATH = re.compile(
    rf"(?<![\w/.\-])((?:[\w.\-]+/)+[\w.\-]+|[\w\-]+(?:\.[\w\-]+)*\.(?:{_CODE_EXT}))(?![\w/])", re.IGNORECASE
)
_DOTTED = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b")
_CAMEL = re.compile(r"\b[a-z][a-z0-9]*[A-Z][A-Za-z0-9]*\b")
_PASCAL = re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)+\b|\b[A-Z]{2,}[a-z][A-Za-z0-9]*\b")
_SNAKE = re.compile(r"\b_*[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+_*\b")
_CALL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\(\)")
_WORD = re.compile(r"[a-z][a-z0-9'\-]*")
_NOT_DOTTED = frozenset({"e.g", "i.e", "etc", "vs"})

# Words that carry no retrieval signal in questions (on top of the tokenizer stopwords).
QUERY_NOISE: frozenset[str] = frozenset(
    {
        "about",
        "all",
        "any",
        "can",
        "could",
        "code",
        "codebase",
        "describe",
        "detail",
        "details",
        "did",
        "each",
        "explain",
        "get",
        "give",
        "happen",
        "happens",
        "has",
        "have",
        "here",
        "into",
        "its",
        "like",
        "look",
        "made",
        "make",
        "me",
        "mean",
        "more",
        "much",
        "my",
        "need",
        "please",
        "project",
        "repo",
        "repository",
        "should",
        "show",
        "some",
        "tell",
        "than",
        "then",
        "there",
        "these",
        "they",
        "those",
        "use",
        "used",
        "uses",
        "using",
        "want",
        "we",
        "when",
        "who",
        "why",
        "will",
        "work",
        "works",
        "would",
        "you",
        "your",
        # meta words of the question itself (intent cues, not content)
        "see",
        "instead",
        "rather",
        "where",
        "defined",
        "definition",
        "located",
        "location",
        "implemented",
        "implementation",
        "find",
        "locate",
        "trace",
        "tracing",
        "flow",
        "flows",
        "workflow",
        "step",
        "steps",
        "sequence",
        "overview",
        "summary",
        "summarize",
        "purpose",
        "concept",
        "meaning",
        "reason",
        "rationale",
        "architecture",
        "architectural",
        "structure",
        "structured",
        "organized",
        "overall",
        "high",
        "level",
        "big",
        "picture",
        "happening",
        "handled",
        "handle",
        "way",
        "ways",
        "part",
        "parts",
    }
)

# --- intent rules ---------------------------------------------------------------------------------------------

# (intent, single words, phrases). Phrases are matched on the lowercased query with word boundaries (weight 2).
_INTENT_RULES: tuple[tuple[RetrievalIntent, frozenset[str], tuple[str, ...]], ...] = (
    (
        RetrievalIntent.INTERVIEW_QUESTION,
        frozenset({"interview", "interviewer", "quiz", "viva"}),
        ("ask me", "test me", "question me", "interview question", "interview questions"),
    ),
    (
        RetrievalIntent.SECURITY,
        frozenset(
            {
                "security",
                "secure",
                "vulnerability",
                "vulnerabilities",
                "vulnerable",
                "injection",
                "xss",
                "csrf",
                "ssrf",
                "exploit",
                "attack",
                "attacker",
                "secrets",
                "credential",
                "credentials",
                "sanitize",
                "sanitization",
                "encrypt",
                "encryption",
                "owasp",
                "cors",
                "permission",
                "permissions",
                "authorization",
                "rbac",
                "leak",
            }
        ),
        ("access control", "prompt injection", "sql injection", "threat model"),
    ),
    (
        RetrievalIntent.FAILURE_ANALYSIS,
        frozenset(
            {
                "fail",
                "fails",
                "failed",
                "failure",
                "failures",
                "error",
                "errors",
                "exception",
                "exceptions",
                "crash",
                "crashes",
                "bug",
                "bugs",
                "retry",
                "retries",
                "timeout",
                "timeouts",
                "fallback",
                "resilience",
                "resilient",
                "outage",
                "broken",
                "breaks",
                "debug",
                "recover",
                "recovery",
            }
        ),
        (
            "goes wrong",
            "go wrong",
            "edge case",
            "edge cases",
            "is down",
            "not working",
            "what happens if",
            "what if",
        ),
    ),
    (
        RetrievalIntent.DESIGN_REASONING,
        frozenset(
            {
                "why",
                "rationale",
                "reason",
                "reasons",
                "tradeoff",
                "tradeoffs",
                "trade-off",
                "trade-offs",
                "decision",
                "decisions",
                "alternative",
                "alternatives",
                "chose",
                "chosen",
                "choose",
                "benefit",
                "benefits",
                "downside",
                "downsides",
                "justify",
            }
        ),
        ("instead of", "rather than", "pros and cons", "design choice", "design decision"),
    ),
    (
        RetrievalIntent.WORKFLOW,
        frozenset(
            {
                "flow",
                "flows",
                "workflow",
                "workflows",
                "trace",
                "tracing",
                "lifecycle",
                "sequence",
                "steps",
                "journey",
                "end-to-end",
            }
        ),
        (
            "what happens",
            "step by step",
            "end to end",
            "request flow",
            "data flow",
            "how does a request",
            "call chain",
            "call path",
            "goes through",
        ),
    ),
    (
        RetrievalIntent.IMPLEMENTATION_LOCATION,
        frozenset({"where", "located", "location", "implemented", "defined", "definition", "locate", "find"}),
        ("which file", "which files", "where is", "where are", "entry point", "entrypoint", "defined in"),
    ),
    (
        RetrievalIntent.ARCHITECTURE,
        frozenset(
            {
                "architecture",
                "architectural",
                "structure",
                "structured",
                "component",
                "components",
                "subsystem",
                "subsystems",
                "module",
                "modules",
                "layer",
                "layers",
                "organized",
                "organised",
                "dependencies",
                "interact",
                "interacts",
                "diagram",
                "topology",
            }
        ),
        ("high level", "high-level", "big picture", "how is it organized", "how are the", "fit together"),
    ),
    (
        RetrievalIntent.OVERVIEW,
        frozenset(
            {"overview", "summary", "summarize", "summarise", "purpose", "introduction", "intro", "tldr"}
        ),
        ("what does this", "what is this project", "get started", "getting started", "what is it for"),
    ),
    (
        RetrievalIntent.TECHNICAL_CONCEPT,
        frozenset({"concept", "concepts", "meaning", "algorithm", "technique", "pattern", "works"}),
        ("what is", "what are", "how does", "how do", "explain how"),
    ),
)
_INTENT_PRIORITY = [rule[0] for rule in _INTENT_RULES]

# --- expansion table (bounded, AI infra §22) -----------------------------------------------------------------

_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "auth": ("login", "token", "jwt", "session", "password", "authenticate"),
    "db": ("database", "model", "schema", "migration", "query", "orm"),
    "cache": ("cache", "redis", "ttl", "invalidate", "memoize"),
    "queue": ("queue", "worker", "job", "task", "consumer", "enqueue"),
    "api": ("route", "endpoint", "handler", "router", "request", "response"),
    "upload": ("upload", "file", "multipart", "storage", "bucket"),
    "config": ("config", "settings", "env", "environment", "options"),
    "test": ("test", "fixture", "pytest", "assert", "mock"),
    "deploy": ("docker", "compose", "kubernetes", "deploy", "ci", "helm"),
    "error": ("error", "exception", "raise", "retry", "fallback", "handler"),
    "search": ("search", "index", "query", "rank", "retrieval", "embedding"),
    "llm": ("llm", "model", "prompt", "completion", "embedding", "provider"),
    "payment": ("payment", "billing", "invoice", "checkout", "charge", "stripe"),
    "notify": ("notification", "email", "send", "smtp", "message", "webhook"),
    "logging": ("logging", "logger", "log", "trace", "metrics", "telemetry"),
    "frontend": ("component", "page", "view", "render", "state", "hook"),
    "user": ("user", "account", "profile", "register", "signup"),
    "security": ("secret", "sanitize", "validate", "escape", "permission", "redact"),
}
_EXPANSION_KEYS: dict[str, str] = {
    **{
        w: "auth"
        for w in (
            "auth",
            "authentication",
            "authenticate",
            "authenticated",
            "login",
            "logins",
            "logout",
            "signin",
            "jwt",
        )
    },
    **{w: "db" for w in ("db", "database", "databases", "persistence", "persist", "persisted", "sql", "orm")},
    **{w: "cache" for w in ("cache", "caching", "cached", "memoization")},
    **{w: "queue" for w in ("queue", "queues", "background", "jobs", "worker", "workers", "celery", "async")},
    **{
        w: "api"
        for w in ("api", "apis", "endpoint", "endpoints", "route", "routes", "routing", "http", "rest")
    },
    **{w: "upload" for w in ("upload", "uploads", "uploading", "uploaded")},
    **{w: "config" for w in ("config", "configuration", "configure", "configured", "settings", "env")},
    **{w: "test" for w in ("test", "tests", "testing", "tested", "pytest", "unittest")},
    **{
        w: "deploy"
        for w in ("deploy", "deployed", "deployment", "deploying", "docker", "kubernetes", "k8s", "hosting")
    },
    **{w: "error" for w in ("error", "errors", "exception", "exceptions", "failure", "failures")},
    **{w: "search" for w in ("search", "searching", "retrieval", "retrieve", "rag", "ranking")},
    **{w: "llm" for w in ("llm", "llms", "ai", "gpt", "gemini", "openai", "genai", "prompt", "prompts")},
    **{w: "payment" for w in ("payment", "payments", "billing", "checkout", "subscription")},
    **{w: "notify" for w in ("notification", "notifications", "email", "emails", "notify")},
    **{w: "logging" for w in ("logging", "logs", "observability", "monitoring", "tracing", "telemetry")},
    **{w: "frontend" for w in ("frontend", "ui", "react", "component", "components", "page", "pages")},
    **{w: "user" for w in ("user", "users", "account", "accounts", "signup", "registration")},
    **{w: "security" for w in ("security", "secrets", "sanitization", "validation", "redaction")},
}

# --- structural hints ------------------------------------------------------------------------------------------

_ENTITY_KIND_WORDS: dict[str, frozenset[str]] = {
    "endpoints": frozenset(
        {"api", "apis", "endpoint", "endpoints", "route", "routes", "http", "rest", "request", "requests"}
        | {"url", "urls", "handler", "handlers", "controller", "controllers", "graphql", "rpc", "webhook"}
    ),
    "schemas": frozenset(
        {"schema", "schemas", "table", "tables", "database", "db", "orm", "entity", "entities", "fields"}
        | {"migration", "migrations", "persist", "persisted", "stored", "models", "dto", "pydantic"}
    ),
    "data_stores": frozenset(
        {"database", "db", "redis", "postgres", "postgresql", "mysql", "mongo", "mongodb", "sqlite", "s3"}
        | {"storage", "store", "stores", "stored", "persist", "persisted", "cache", "qdrant", "neo4j"}
        | {"elasticsearch"}
    ),
    "services": frozenset(
        {"service", "services", "deploy", "deployment", "docker", "container", "containers", "compose"}
        | {"worker", "workers", "microservice", "microservices", "process", "processes", "server"}
    ),
    "infrastructure": frozenset(
        {"infra", "infrastructure", "deploy", "deployment", "docker", "kubernetes", "k8s", "terraform"}
        | {"ci", "cd", "helm", "cloud", "aws", "gcp", "azure", "dockerfile", "github"}
    ),
    "queues": frozenset(
        {"queue", "queues", "job", "jobs", "worker", "workers", "celery", "kafka", "rabbitmq", "background"}
        | {"consumer", "consumers", "producer", "pubsub", "broker"}
    ),
    "ai_components": frozenset(
        {"llm", "llms", "ai", "ml", "embedding", "embeddings", "vector", "rag", "agent", "agents", "prompt"}
        | {"prompts", "inference", "training", "gemini", "openai", "reranker", "retrieval"}
    ),
}
_INTENT_ENTITY_KINDS: dict[RetrievalIntent, tuple[str, ...]] = {
    RetrievalIntent.OVERVIEW: ("services", "endpoints"),
    RetrievalIntent.ARCHITECTURE: ("services", "data_stores", "queues", "infrastructure"),
    RetrievalIntent.WORKFLOW: ("endpoints",),
    RetrievalIntent.SECURITY: ("endpoints",),
}
_INTENT_EVIDENCE: dict[RetrievalIntent, tuple[ContentType, ...]] = {
    RetrievalIntent.OVERVIEW: (ContentType.DOCS, ContentType.SOURCE, ContentType.CONFIG),
    RetrievalIntent.WORKFLOW: (ContentType.SOURCE,),
    RetrievalIntent.ARCHITECTURE: (
        ContentType.SOURCE,
        ContentType.CONFIG,
        ContentType.INFRA,
        ContentType.DOCS,
    ),
    RetrievalIntent.IMPLEMENTATION_LOCATION: (ContentType.SOURCE,),
    RetrievalIntent.TECHNICAL_CONCEPT: (ContentType.SOURCE, ContentType.DOCS),
    RetrievalIntent.DESIGN_REASONING: (ContentType.DOCS, ContentType.SOURCE, ContentType.CONFIG),
    RetrievalIntent.FAILURE_ANALYSIS: (ContentType.SOURCE, ContentType.CONFIG),
    RetrievalIntent.SECURITY: (ContentType.SOURCE, ContentType.CONFIG),
    RetrievalIntent.INTERVIEW_QUESTION: (ContentType.SOURCE, ContentType.DOCS),
}
_EVIDENCE_WORDS: dict[ContentType, frozenset[str]] = {
    ContentType.INFRA: frozenset(
        {"deploy", "deployment", "docker", "kubernetes", "k8s", "terraform", "ci", "infra", "infrastructure"}
    ),
    ContentType.CONFIG: frozenset(
        {"config", "configuration", "configured", "configurable", "settings", "env", "environment", "configure"}
    ),
    ContentType.TEST: frozenset({"test", "tests", "testing", "pytest", "coverage", "fixture", "fixtures"}),
    ContentType.DOCS: frozenset({"docs", "documentation", "readme", "guide", "documented"}),
}


class SubsystemHint(BaseModel):
    """What the planner needs to target a subsystem (from guide state, subsystem discovery, or the UI)."""

    id: str
    name: str
    keywords: list[str] = Field(default_factory=list)


SubsystemLike = SubsystemHint | SubsystemCandidate | Mapping[str, Any]


# --- IR index -------------------------------------------------------------------------------------------------


@dataclass
class _IRIndex:
    by_name: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # lower name -> [(id, name)]
    by_qualname: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    file_paths: dict[str, str] = field(default_factory=dict)  # lower path -> path
    by_basename: dict[str, list[str]] = field(default_factory=dict)  # lower basename -> paths
    by_stem: dict[str, list[str]] = field(default_factory=dict)  # lower stem -> paths


_IR_CACHE: dict[tuple[str, str, int, int], _IRIndex] = {}
_IR_CACHE_MAX = 8


def _ir_index(ir: ProjectIR) -> _IRIndex:
    key = (ir.project.project_id, ir.analysis_version, len(ir.symbols), len(ir.files))
    hit = _IR_CACHE.get(key)
    if hit is not None:
        return hit
    idx = _IRIndex()

    def add(bucket: dict[str, list[tuple[str, str]]], name: str | None, eid: str) -> None:
        if name:
            bucket.setdefault(name.lower(), []).append((eid, name))

    for s in ir.symbols:
        add(idx.by_name, s.name, s.id)
        add(idx.by_qualname, s.qualname, s.id)
    for group in (ir.schemas, ir.services, ir.data_stores, ir.queues, ir.ai_components):
        for e in group:
            add(idx.by_name, e.name, e.id)
    for f in ir.files:
        idx.file_paths[f.path.lower()] = f.path
        base = posixpath.basename(f.path).lower()
        idx.by_basename.setdefault(base, []).append(f.path)
        stem = base.split(".", 1)[0]
        if stem:
            idx.by_stem.setdefault(stem, []).append(f.path)
    if len(_IR_CACHE) >= _IR_CACHE_MAX:
        _IR_CACHE.pop(next(iter(_IR_CACHE)))
    _IR_CACHE[key] = idx
    return idx


# --- extraction -----------------------------------------------------------------------------------------------


@dataclass
class ExtractedQuery:
    identifiers: list[str]
    paths: list[str]
    routes: list[str]
    http_methods: list[str]
    words: list[str]  # lowercased words (for rules)
    terms: list[str]  # salient lowercased search terms (tokenizer output minus noise)


def _dedupe(items: Sequence[str], limit: int | None = None) -> list[str]:
    out = list(dict.fromkeys(i for i in items if i))
    return out if limit is None else out[:limit]


def extract(query: str) -> ExtractedQuery:
    """Identifiers, paths, routes and salient terms of a query (pure)."""
    text = _URL.sub(" ", query.strip())
    identifiers: list[str] = []
    paths: list[str] = []
    routes: list[str] = []
    methods: list[str] = []

    for m in _BACKTICK.finditer(text):
        inner = m.group(1).strip().removesuffix("()")
        if inner.startswith("/") and not _has_ext(inner):
            routes.append(inner)
        elif "/" in inner or _has_ext(inner):
            paths.append(inner.lstrip("/"))
        elif inner:
            identifiers.append(inner)
    unticked = _BACKTICK.sub(" ", text)
    for m in _ROUTE.finditer(unticked):
        route = m.group(2).rstrip(".,;:?!")
        if len(route) < 2:
            continue
        if _has_ext(route):
            paths.append(route.lstrip("/"))
            continue
        routes.append(route)
        if m.group(1):
            methods.append(m.group(1))
    no_routes = _ROUTE.sub(" ", unticked)
    for m in _PATH.finditer(no_routes):
        candidate = m.group(1).rstrip(".,;:?!-")
        if candidate.lower() in _NOT_DOTTED:
            continue
        if "/" in candidate or _has_ext(candidate):
            paths.append(candidate)
    path_set = set(paths)
    for pattern in (_CALL, _DOTTED, _CAMEL, _PASCAL, _SNAKE):
        for m in pattern.finditer(no_routes):
            ident = m.group(1) if pattern is _CALL else m.group(0)
            if ident.lower() in _NOT_DOTTED or any(ident in p for p in path_set):
                continue
            if any(ident != o and ident in o.split(".") for o in identifiers):
                continue  # a segment of a dotted name already captured (`AuthService` in `AuthService.login`)
            if pattern is _DOTTED and _has_ext(ident):
                continue
            identifiers.append(ident)
    identifiers = _dedupe(identifiers, MAX_IDENTIFIERS)
    # Prose = the question without code-like spans; intent rules and expansions only look at prose.
    prose = no_routes
    for span_text in sorted({*paths, *identifiers}, key=len, reverse=True):
        prose = prose.replace(span_text, " ")
    words = _WORD.findall(prose.lower())
    exts = {p.rsplit(".", 1)[1].lower() for p in paths if "." in posixpath.basename(p)}
    terms = [
        t
        for t in tokenize_query(text)
        if t not in QUERY_NOISE
        and t not in STOPWORDS
        and t not in _NOT_DOTTED
        and t not in exts
        and t.lower() not in {mth.lower() for mth in methods}
        and not t.isdigit()
    ]
    return ExtractedQuery(
        identifiers=identifiers,
        paths=_dedupe(paths, MAX_IDENTIFIERS),
        routes=_dedupe(routes, MAX_IDENTIFIERS),
        http_methods=_dedupe(methods),
        words=words,
        terms=_dedupe(terms, MAX_TERMS),
    )


def _has_ext(value: str) -> bool:
    base = posixpath.basename(value.lower())
    if base == "dockerfile":
        return True
    return "." in base and base.rsplit(".", 1)[1] in _EXT_SET


# --- rules ----------------------------------------------------------------------------------------------------


def classify_intent(query: str, extracted: ExtractedQuery | None = None) -> RetrievalIntent:
    ex = extracted or extract(query)
    low = " " + " ".join(ex.words) + " "
    words = set(ex.words)
    scores: dict[RetrievalIntent, int] = {}
    for intent, singles, phrases in _INTENT_RULES:
        score = sum(1 for w in singles if w in words)
        score += sum(2 for p in phrases if re.search(rf"(?<![a-z]){re.escape(p)}(?![a-z])", low))
        if score:
            scores[intent] = score
    if not scores:
        if ex.identifiers or ex.paths or ex.routes:
            return RetrievalIntent.IMPLEMENTATION_LOCATION
        return RetrievalIntent.TECHNICAL_CONCEPT if ex.terms else RetrievalIntent.OVERVIEW
    best = max(scores.values())
    return next(i for i in _INTENT_PRIORITY if scores.get(i) == best)


def expansions_for(words: Sequence[str], terms: Sequence[str]) -> list[str]:
    keys: list[str] = []
    for w in [*words, *terms]:
        key = _EXPANSION_KEYS.get(w) or _EXPANSION_KEYS.get(w.rstrip("s"))
        if key and key not in keys:
            keys.append(key)
    present = set(terms) | set(words)
    out: list[str] = []
    for key in keys:
        out.extend(t for t in _EXPANSIONS[key][:MAX_EXPANSIONS_PER_KEY] if t not in present)
    return _dedupe(out, MAX_EXPANSIONS)


def _entity_kinds(intent: RetrievalIntent, words: set[str]) -> list[str]:
    kinds = [k for k, vocab in _ENTITY_KIND_WORDS.items() if words & vocab]
    kinds.extend(_INTENT_ENTITY_KINDS.get(intent, ()))
    return _dedupe(kinds)


def _evidence_types(intent: RetrievalIntent, words: set[str]) -> list[str]:
    types = [t.value for t in _INTENT_EVIDENCE.get(intent, (ContentType.SOURCE,))]
    types.extend(t.value for t, vocab in _EVIDENCE_WORDS.items() if words & vocab)
    return _dedupe(types)


def _match_ir(
    ir: ProjectIR, ex: ExtractedQuery
) -> tuple[list[str], list[str], list[str]]:  # (target ids, seed names, paths)
    idx = _ir_index(ir)
    targets: list[str] = []
    names: list[str] = []
    paths: list[str] = []

    for raw in ex.paths:
        needle = raw.strip("./").lower()
        exact = idx.file_paths.get(needle)
        if exact:
            paths.append(exact)
            continue
        suffix = [p for lp, p in idx.file_paths.items() if lp.endswith("/" + needle) or lp == needle]
        paths.extend(sorted(suffix)[:3] or sorted(idx.by_basename.get(posixpath.basename(needle), []))[:3])

    for ident in ex.identifiers:
        low = ident.lower()
        hits = idx.by_qualname.get(low) or idx.by_name.get(low)
        if not hits and "." in ident:
            head, _, last = ident.rpartition(".")
            cands = idx.by_qualname.get(low) or idx.by_name.get(last.lower()) or []
            hits = cands
            if len(cands) > 1:  # `module.func`: prefer candidates whose qualname or path mentions the head
                narrowed = [h for h in cands if head.lower() in h[1].lower()]
                hits = narrowed or cands
        if hits:
            targets.extend(h[0] for h in hits[:_GENERIC_NAME_LIMIT])
            names.append(hits[0][1].rsplit(".", 1)[-1])
        elif low in idx.by_stem:
            paths.extend(sorted(idx.by_stem[low])[:2])

    ident_tokens = set(tokenize_query(" ".join([*ex.identifiers, *ex.paths])))
    for term in ex.terms:
        if len(term) < 4 or term in QUERY_NOISE or term in ident_tokens:
            continue
        hits = idx.by_name.get(term, [])
        if 0 < len(hits) <= _GENERIC_NAME_LIMIT:
            targets.extend(h[0] for h in hits[:3])
            names.append(hits[0][1])
    return _dedupe(targets, MAX_TARGET_SYMBOLS), _dedupe(names), _dedupe(paths, MAX_IDENTIFIERS)


def _normalize_subsystem(item: SubsystemLike) -> SubsystemHint | None:
    if isinstance(item, SubsystemHint):
        return item
    if isinstance(item, SubsystemCandidate):
        kws = [posixpath.basename(p) for p in item.key_files]
        return SubsystemHint(id=item.id, name=item.label_hint, keywords=kws)
    sid = item.get("id")
    name = item.get("name") or item.get("title") or item.get("label") or item.get("label_hint")
    if not sid or not name:
        return None
    raw_kws = item.get("keywords") or []
    return SubsystemHint(
        id=str(sid), name=str(name), keywords=[str(k) for k in raw_kws if isinstance(k, str | int)]
    )


def match_subsystems(
    subsystems: Sequence[SubsystemLike], terms: Sequence[str], expansions: Sequence[str]
) -> list[str]:
    wanted = set(terms) | set(expansions)
    if not wanted:
        return []
    scored: list[tuple[int, str]] = []
    for raw in subsystems:
        hint = _normalize_subsystem(raw)
        if hint is None:
            continue
        name_tokens = set(tokenize_query(hint.name))
        kw_tokens = set(tokenize_query(" ".join(hint.keywords)))
        score = 2 * len(name_tokens & wanted) + len(kw_tokens & wanted)
        if score:
            scored.append((score, hint.id))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [sid for _, sid in scored[:MAX_SUBSYSTEMS]]


# --- planner --------------------------------------------------------------------------------------------------


def plan_query(
    project_id: str,
    query: str,
    mode: RetrievalMode = RetrievalMode.CHAT,
    *,
    ir: ProjectIR | None = None,
    subsystems: Sequence[SubsystemLike] | None = None,
    breadth: Breadth | None = None,
    intent: RetrievalIntent | None = None,
) -> RetrievalPlan:
    """Deterministic retrieval plan. `intent` overrides classification (guide sections know their intent)."""
    ex = extract(query)
    chosen = intent or classify_intent(query, ex)
    words = set(ex.words)  # prose only: code-like spans must not trigger structural hints
    expansions = expansions_for(ex.words, ())

    targets: list[str] = []
    ir_names: list[str] = []
    ir_paths: list[str] = []
    if ir is not None:
        targets, ir_names, ir_paths = _match_ir(ir, ex)

    identifiers = ex.identifiers
    lexical = [query.strip()]
    extra_lex = " ".join(_dedupe([*identifiers, *ir_names, *ex.paths, *expansions]))
    if extra_lex and extra_lex.lower() != query.strip().lower():
        lexical.append(extra_lex)
    semantic = [query.strip()]
    if expansions:
        semantic.append(f"{query.strip()} ({', '.join(expansions[:4])})")

    ident_tokens = set(tokenize_query(" ".join([*identifiers, *ex.paths])))
    seed_terms = [t for t in ex.terms if len(t) >= 4 and t not in ident_tokens and t not in expansions][:4]
    seeds = _dedupe([*identifiers, *ir_names, *seed_terms], MAX_SEEDS)
    target_subsystems = match_subsystems(subsystems or [], ex.terms, expansions)

    structural: dict[str, Any] = {
        "identifiers": identifiers,
        "terms": ex.terms,
        "expansions": expansions,
        "paths": _dedupe([*ir_paths, *ex.paths], MAX_IDENTIFIERS),
        "routes": ex.routes,
        "http_methods": ex.http_methods,
        "entity_kinds": _entity_kinds(chosen, words),
    }
    return RetrievalPlan(
        project_id=project_id,
        query=query.strip(),
        intent=chosen,
        mode=mode,
        lexical_queries=[q for q in lexical if q],
        semantic_queries=[q for q in semantic if q],
        target_subsystems=target_subsystems,
        target_symbols=targets,
        graph_seed_entities=seeds,
        structural_filters=structural,
        desired_evidence_types=_evidence_types(chosen, words),
        breadth=breadth or default_breadth(chosen, mode),
    )
