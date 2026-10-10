"""Python analyzer on the stdlib `ast` (LLD §7, ESD §8.2). Parses text only — never imports or executes code.

Extracts symbols (qualnames, spans, docstrings, decorators, signatures, bases), imports resolved to repository
files, name bindings, call sites (pre-resolved where the receiver type is evident: `self.x`, `v = Cls(...)`,
annotated parameters, return annotations), FastAPI/Flask/Starlette/Django(+DRF) routes, routers and mounts,
Pydantic/SQLAlchemy/SQLModel/Django/dataclass schemas with fields, Celery/arq/RQ/Dramatiq/Huey/TaskIQ tasks and
their publishes, ORM data access, AI/ML hints and app objects. A `SyntaxError` is recorded in `errors`.
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Literal

from unfold.core.logging import get_logger
from unfold.repo_intel.analyzers.base import AnalysisContext
from unfold.repo_intel.models import (
    AIHint,
    Binding,
    CallRef,
    DataAccess,
    FieldDef,
    FileAnalysis,
    ImportRef,
    InventoryFile,
    MountDef,
    PublishRef,
    RouteDef,
    RouterDef,
    SchemaDef,
    SymbolDef,
    SymbolKind,
    TaskDef,
)
from unfold.repo_intel.rules import match_ai_import

log = get_logger(__name__)

MAX_CALLS_PER_FILE = 5000
MAX_DOCSTRING_CHARS = 2000
MAX_EXPR_CHARS = 240
CANDIDATE_SCHEMA_KIND = "candidate"  # resolved (promoted or dropped) by cross-file linking in `analyze`

_STDLIB = frozenset(sys.stdlib_module_names)
_HTTP_METHODS = frozenset({"get", "post", "put", "delete", "patch", "head", "options", "trace"})
_ROUTE_DECORATORS = _HTTP_METHODS | {"route", "api_route", "websocket", "websocket_route"}
_COMMON_ROUTER_NAMES = frozenset(
    {"app", "application", "router", "api", "bp", "blueprint", "api_router", "routes"}
)

# constructor name -> (router kind, app object, prefix keyword, module the name must come from (None = any))
_ROUTER_CTORS: dict[str, tuple[str, str | None, str | None, str | None]] = {
    "FastAPI": ("fastapi", "fastapi", None, "fastapi"),
    "APIRouter": ("apirouter", None, "prefix", "fastapi"),
    "Flask": ("flask", "flask", None, "flask"),
    "Blueprint": ("blueprint", None, "url_prefix", "flask"),
    "Quart": ("quart", "quart", None, "quart"),
    "Starlette": ("starlette", "starlette", None, "starlette"),
    "Sanic": ("sanic", "sanic", None, "sanic"),
    "Litestar": ("litestar", "litestar", None, "litestar"),
    "NinjaAPI": ("ninja", "django_ninja", None, "ninja"),
    "Router": ("ninja_router", None, None, "ninja"),
    "DefaultRouter": ("drf_router", None, None, "rest_framework"),
    "SimpleRouter": ("drf_router", None, None, "rest_framework"),
    "RouteTableDef": ("aiohttp", "aiohttp", None, "aiohttp"),
    "Application": ("aiohttp", "aiohttp", None, "aiohttp"),
}
_ROUTER_FRAMEWORK = {
    "fastapi": "fastapi",
    "apirouter": "fastapi",
    "flask": "flask",
    "blueprint": "flask",
    "quart": "quart",
    "starlette": "starlette",
    "sanic": "sanic",
    "litestar": "litestar",
    "ninja": "django_ninja",
    "ninja_router": "django_ninja",
    "drf_router": "django_rest_framework",
    "aiohttp": "aiohttp",
}
_WEB_IMPORT_FRAMEWORKS = (
    ("fastapi", "fastapi"),
    ("flask", "flask"),
    ("quart", "quart"),
    ("starlette", "starlette"),
    ("sanic", "sanic"),
    ("litestar", "litestar"),
    ("ninja", "django_ninja"),
    ("aiohttp", "aiohttp"),
)
# constructor / call name -> app object (in addition to routers)
_APP_OBJECT_CALLS: dict[str, tuple[str, str | None]] = {
    "Celery": ("celery", "celery"),
    "Typer": ("typer", "typer"),
    "ArgumentParser": ("argparse", "argparse"),
    "Fire": ("fire", "fire"),
    "Dramatiq": ("dramatiq", "dramatiq"),
    "Huey": ("huey", "huey"),
    "RedisHuey": ("huey", "huey"),
    "Worker": ("rq_worker", "rq"),
    "FastMCP": ("mcp_server", None),
    "StateGraph": ("langgraph_graph", "langgraph"),
    "create_react_agent": ("agent", None),
    "create_deep_agent": ("agent", "deepagents"),
    "Blocks": ("gradio", "gradio"),
    "Interface": ("gradio", "gradio"),
    "run": ("uvicorn", "uvicorn"),
}
_TASK_TECH_BY_IMPORT = (
    ("celery", "celery"),
    ("huey", "huey"),
    ("taskiq", "taskiq"),
    ("dramatiq", "dramatiq"),
)

_PYDANTIC_BASES = frozenset({"BaseModel", "BaseSettings", "RootModel", "GenericModel", "BaseConfig"})
_SQLA_BASE_CLASSES = frozenset(
    {"DeclarativeBase", "DeclarativeBaseNoMeta", "AsyncAttrs", "MappedAsDataclass"}
)
_SQLA_BASE_FACTORIES = frozenset({"declarative_base", "as_declarative"})
_SQLA_COLUMN_CALLS = frozenset(
    {"mapped_column", "Column", "column_property", "relationship", "composite", "Field"}
)
_DATACLASS_DECORATORS = frozenset(
    {
        "dataclass",
        "dataclasses.dataclass",
        "attr.s",
        "attrs.define",
        "define",
        "attr.attrs",
        "attrs.frozen",
        "frozen",
        "pydantic.dataclasses.dataclass",
    }
)
_OTHER_SCHEMA_BASES = frozenset(
    {
        "TypedDict",
        "NamedTuple",
        "Schema",
        "Serializer",
        "ModelSerializer",
        "HyperlinkedModelSerializer",
        "Struct",
        "Document",
        "EmbeddedDocument",
        "ObjectType",
        "InputObjectType",
        "Message",
    }
)
_SKIP_FIELD_NAMES = frozenset(
    {
        "model_config",
        "Config",
        "Meta",
        "__tablename__",
        "__table_args__",
        "__abstract__",
        "objects",
        "__mapper_args__",
        "__slots__",
    }
)

_DJANGO_READ = frozenset(
    {
        "filter",
        "get",
        "all",
        "exclude",
        "first",
        "last",
        "count",
        "exists",
        "values",
        "values_list",
        "order_by",
        "aggregate",
        "annotate",
        "select_related",
        "prefetch_related",
        "iterator",
        "in_bulk",
        "only",
        "defer",
        "distinct",
        "raw",
        "afilter",
        "aget",
        "acount",
        "aexists",
        "afirst",
        "alast",
    }
)
_DJANGO_WRITE = frozenset(
    {
        "create",
        "update",
        "delete",
        "bulk_create",
        "bulk_update",
        "update_or_create",
        "get_or_create",
        "acreate",
        "aupdate",
        "adelete",
        "abulk_create",
        "aupdate_or_create",
        "aget_or_create",
    }
)
_SESSION_WRITE = frozenset(
    {"add", "add_all", "merge", "delete", "bulk_save_objects", "bulk_insert_mappings", "bulk_update_mappings"}
)
_SQL_EXEC = frozenset(
    {
        "execute",
        "executemany",
        "exec_driver_sql",
        "fetch",
        "fetchrow",
        "fetchval",
        "fetchall",
        "fetchone",
        "raw",
        "text",
        "executescript",
        "query_raw",
        "execute_raw",
    }
)
_SQL_TABLES = re.compile(
    r"\b(?P<kw>from|join|into|update|delete\s+from|table|truncate)\s+(?:only\s+|if\s+(?:not\s+)?exists\s+)?"
    r"[`\"\[]?(?P<name>[A-Za-z_][\w.]*)[`\"\]]?",
    re.I,
)
_SQL_START = re.compile(
    r"^\s*(select|insert|update|delete|with|create|alter|drop|truncate|merge|upsert|replace)\b", re.I
)
_SQL_WRITE_KW = frozenset({"into", "update", "delete from", "table", "truncate"})
_SQL_NOT_TABLES = frozenset(
    {
        "select",
        "where",
        "set",
        "values",
        "lateral",
        "unnest",
        "generate_series",
        "dual",
        "json_each",
        "jsonb_each",
    }
)

# (call-tail parts, component kind, technology or None = infer from imports)
_AI_CALL_PATTERNS: tuple[tuple[tuple[str, ...], str, str | None], ...] = (
    (("chat", "completions", "create"), "llm_client", None),
    (("chat", "completions", "parse"), "llm_client", None),
    (("responses", "create"), "llm_client", None),
    (("completions", "create"), "llm_client", None),
    (("messages", "create"), "llm_client", "anthropic"),
    (("messages", "stream"), "llm_client", "anthropic"),
    (("models", "generate_content"), "llm_client", "gemini"),
    (("generate_content",), "llm_client", "gemini"),
    (("models", "embed_content"), "embedding", "gemini"),
    (("embed_content",), "embedding", "gemini"),
    (("embeddings", "create"), "embedding", None),
    (("embed_documents",), "embedding", None),
    (("embed_query",), "embedding", None),
    (("rerank",), "reranker", None),
    (("backward",), "training", "pytorch"),
    (("zero_grad",), "training", "pytorch"),
)
_AI_CTOR_PATTERNS: dict[str, tuple[str, str]] = {
    "SentenceTransformer": ("embedding", "sentence_transformers"),
    "CrossEncoder": ("reranker", "sentence_transformers"),
    "TextEmbedding": ("embedding", "fastembed"),
    "OpenAIEmbeddings": ("embedding", "openai"),
    "HuggingFaceEmbeddings": ("embedding", "huggingface"),
    "GoogleGenerativeAIEmbeddings": ("embedding", "gemini"),
    "BM25Okapi": ("lexical_search", "bm25"),
    "BM25Plus": ("lexical_search", "bm25"),
    "BM25L": ("lexical_search", "bm25"),
    "BM25": ("lexical_search", "bm25"),
    "QdrantClient": ("vector_search", "qdrant"),
    "AsyncQdrantClient": ("vector_search", "qdrant"),
    "IndexFlatL2": ("vector_search", "faiss"),
    "IndexFlatIP": ("vector_search", "faiss"),
    "IndexHNSWFlat": ("vector_search", "faiss"),
    "PersistentClient": ("vector_search", "chroma"),
    "Pinecone": ("vector_search", "pinecone"),
    "OpenAI": ("llm_client", "openai"),
    "AsyncOpenAI": ("llm_client", "openai"),
    "AzureOpenAI": ("llm_client", "azure_openai"),
    "Anthropic": ("llm_client", "anthropic"),
    "AsyncAnthropic": ("llm_client", "anthropic"),
    "ChatOpenAI": ("llm_client", "openai"),
    "ChatAnthropic": ("llm_client", "anthropic"),
    "ChatGoogleGenerativeAI": ("llm_client", "gemini"),
    "GenerativeModel": ("llm_client", "gemini"),
    "Groq": ("llm_client", "groq"),
    "AsyncGroq": ("llm_client", "groq"),
    "Trainer": ("training", "huggingface_transformers"),
    "SFTTrainer": ("training", "trl"),
    "AutoModelForCausalLM": ("inference", "huggingface_transformers"),
    "AutoModel": ("inference", "huggingface_transformers"),
    "pipeline": ("inference", "huggingface_transformers"),
    "LLM": ("inference", "vllm"),
}
# constructors whose name is ambiguous: only count them when imported from this module
_AI_CTOR_ORIGIN: dict[str, str] = {
    "pipeline": "transformers",
    "LLM": "vllm",
    "Pinecone": "pinecone",
    "BM25": "bm25s",
    "Trainer": "transformers",
    "AutoModel": "transformers",
    "AutoModelForCausalLM": "transformers",
    "Groq": "groq",
    "AsyncGroq": "groq",
    "OpenAI": "openai",
    "AsyncOpenAI": "openai",
    "Anthropic": "anthropic",
    "AsyncAnthropic": "anthropic",
    "PersistentClient": "chromadb",
    "GenerativeModel": "google",
    "TextEmbedding": "fastembed",
}
_TECH_IMPORTS: dict[str, tuple[str, ...]] = {
    "anthropic": ("anthropic",),
    "gemini": ("google.genai", "google.generativeai", "vertexai", "google.cloud.aiplatform"),
    "pytorch": ("torch",),
}
_LLM_CLIENT_TECH_BY_IMPORT = (
    "openai",
    "anthropic",
    "groq",
    "mistralai",
    "together",
    "litellm",
    "cohere",
    "ollama",
    "google.genai",
    "google.generativeai",
)
_TECH_NAME = {"google.genai": "gemini", "google.generativeai": "gemini", "mistralai": "mistral"}
_RRF_NAME = re.compile(r"(reciprocal_rank_fusion|(^|_)rrf($|_)|rank_fusion)", re.I)
_CHUNK_NAME = re.compile(
    r"^(split|chunk)(_?(text|texts|document|documents|into_chunks|markdown))?$|^(make|create)_chunks?$", re.I
)


def dotted(node: ast.AST | None) -> list[str] | None:
    """`a.b.c` → ["a", "b", "c"]; anything that is not a pure Name/Attribute chain → None."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return parts[::-1]
    return None


def _unparse(node: ast.AST | None, limit: int = MAX_EXPR_CHARS) -> str:
    if node is None:
        return ""
    try:
        text = ast.unparse(node)
    except (ValueError, RecursionError):  # pragma: no cover - malformed synthetic trees only
        return ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _const_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _kwarg(call: ast.Call, *names: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg in names:
            return kw.value
    return None


def _str_list(node: ast.AST | None) -> list[str]:
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        return [v for e in node.elts if (v := _const_str(e)) is not None]
    single = _const_str(node)
    return [single] if single is not None else []


def _truncate(text: str | None, limit: int = MAX_DOCSTRING_CHARS) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _annotation_type(node: ast.AST | None) -> list[str] | None:
    """Best-effort class reference inside an annotation: `Optional[X]`, `X | None`, `list[X]`, `"X"` → X."""
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        for side in (node.left, node.right):
            if not (isinstance(side, ast.Constant) and side.value is None):
                return _annotation_type(side)
        return None
    if isinstance(node, ast.Subscript):
        outer = dotted(node.value)
        inner = node.slice
        if outer and outer[-1] in (
            "Optional",
            "Annotated",
            "Mapped",
            "list",
            "List",
            "Sequence",
            "Iterable",
            "set",
            "Set",
            "tuple",
            "Awaitable",
            "Coroutine",
            "AsyncIterator",
            "Iterator",
            "Type",
            "type",
        ):
            if isinstance(inner, ast.Tuple) and inner.elts:
                inner = inner.elts[0] if outer[-1] != "Coroutine" else inner.elts[-1]
            return _annotation_type(inner)
        if outer and outer[-1] == "Union" and isinstance(inner, ast.Tuple):
            for elt in inner.elts:
                if not (isinstance(elt, ast.Constant) and elt.value is None):
                    return _annotation_type(elt)
        return outer
    parts = dotted(node)
    if parts and parts[-1] in ("None", "Any", "str", "int", "float", "bool", "bytes", "dict", "object"):
        return None
    return parts


def _is_classlike(parts: Sequence[str] | None) -> bool:
    return bool(parts) and parts is not None and parts[-1][:1].isupper()


@dataclass
class _Scope:
    kind: Literal["module", "class", "function"]
    qualname: str | None
    class_qualname: str | None = None  # enclosing class (for methods)
    types: dict[str, list[str]] = field(default_factory=dict)  # local variable -> type expression
    parent: _Scope | None = None

    def lookup(self, name: str) -> list[str] | None:
        scope: _Scope | None = self
        while scope is not None:
            if scope.kind == "function" and name in scope.types:
                return scope.types[name]
            scope = scope.parent
        return None


class PythonAnalyzer:
    """`LanguageAnalyzer` for `.py`/`.pyi` files."""

    name = "python"

    def supports(self, file: InventoryFile) -> bool:
        return file.is_text and file.ext in ("py", "pyi")

    def analyze(self, file: InventoryFile, text: str, ctx: AnalysisContext) -> FileAnalysis:
        fa = FileAnalysis(path=file.path, language="python", analyzer=self.name)
        try:
            tree = ast.parse(text, filename=file.path, type_comments=False)
        except SyntaxError as exc:
            fa.errors.append(f"syntax_error: line {exc.lineno or 0}: {exc.msg}")
            return fa
        except (ValueError, RecursionError, MemoryError) as exc:
            fa.errors.append(f"parse_error: {type(exc).__name__}")
            return fa
        extractor = _Extractor(file.path, ctx, fa)
        try:
            extractor.run(tree)
        except RecursionError:
            fa.errors.append("analysis_truncated: nesting too deep")
        extractor.finish()
        return fa


class _Extractor:
    def __init__(self, path: str, ctx: AnalysisContext, fa: FileAnalysis) -> None:
        self.path = path
        self.ctx = ctx
        self.fa = fa
        self.bindings: dict[str, Binding] = {}
        self.imported_modules: dict[str, str] = {}  # `import a.b.c` full dotted name -> repository file
        self.imports_seen: set[str] = set()  # absolute module names imported anywhere in the file
        self.top_level: dict[str, str] = {}  # module-level def/class/assignment name -> kind
        self.symbol_qualnames: set[str] = set()
        self.return_types: dict[str, list[str]] = {}  # function qualname -> returned class expression
        self.attr_types: dict[str, dict[str, list[str]]] = {}  # class qualname -> self attribute -> type
        self.routers: dict[str, RouterDef] = {}
        self.local_schema_kinds: dict[str, str] = {}  # class name -> schema kind (in-file inheritance)
        self.sqla_bases: set[str] = set()
        self.django_models = False
        self.dramatiq_actors: set[str] = set()
        self.all_names: list[str] | None = None
        self.app_objects: set[str] = set()
        self.calls: list[CallRef] = []
        self.ai_keys: set[tuple[str, str, str | None]] = set()

    # ------------------------------------------------------------------ driver

    def run(self, tree: ast.Module) -> None:
        self.fa.docstring = _truncate(ast.get_docstring(tree, clean=True))
        self._collect_imports(tree)
        self._import_ai_hints()
        self._prescan(tree)
        self._walk(tree.body, _Scope("module", None))

    def finish(self) -> None:
        fa = self.fa
        fa.bindings = dict(sorted(self.bindings.items()))
        self.calls.sort(key=lambda c: (c.line, c.parts))
        fa.calls = self.calls[:MAX_CALLS_PER_FILE]
        if len(self.calls) > MAX_CALLS_PER_FILE:
            fa.errors.append(f"calls_truncated: {len(self.calls)} > {MAX_CALLS_PER_FILE}")
        fa.routers = sorted(self.routers.values(), key=lambda r: (r.line, r.var))
        fa.app_objects = sorted(self.app_objects)
        if self.all_names is not None:
            fa.exports = list(dict.fromkeys(self.all_names))
        else:
            fa.exports = [n for n in self.top_level if not n.startswith("_")]
        exported = set(fa.exports)
        for sym in fa.symbols:
            sym.exported = sym.parent is None and sym.name in exported
        fa.symbols.sort(key=lambda s: (s.start_line, s.qualname))
        fa.routes.sort(key=lambda r: (r.line, r.method, r.path))

    # ------------------------------------------------------------------ imports

    def _collect_imports(self, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._import_module(alias.name, alias.asname, node.lineno)
            elif isinstance(node, ast.ImportFrom):
                self._import_from(node)

    def _external_package(self, module: str) -> str | None:
        top = module.split(".", 1)[0]
        return None if top in _STDLIB or not top else top

    def _import_module(self, module: str, asname: str | None, line: int) -> None:
        resolved = self.ctx.resolve_python_module(module)
        pkg_dir = None if resolved else self.ctx.python_package_dir(module)
        internal = resolved is not None or pkg_dir is not None
        self.imports_seen.add(module)
        self.fa.imports.append(
            ImportRef(
                module=module,
                line=line,
                alias=asname,
                resolved_path=resolved,
                resolved_dir=pkg_dir,
                external=not internal,
                package=None if internal else self._external_package(module),
            )
        )
        if resolved:
            self.imported_modules[module] = resolved
        if asname:
            self.bindings[asname] = (
                Binding(kind="module", path=resolved) if internal else Binding(kind="module", external=module)
            )
        else:
            top = module.split(".", 1)[0]
            top_file = self.ctx.resolve_python_module(top)
            top_internal = top_file is not None or self.ctx.python_package_dir(top) is not None
            self.bindings.setdefault(
                top,
                Binding(kind="module", path=top_file)
                if top_internal
                else Binding(kind="module", external=top),
            )

    def _import_from(self, node: ast.ImportFrom) -> None:
        level = node.level or 0
        module = (
            self.ctx.absolute_python_module(self.path, level, node.module) if level else (node.module or "")
        )
        if module == "__future__":
            return
        self.imports_seen.add(module)
        target_file = self.ctx.resolve_python_module(module) if module else None
        target_dir = None if target_file else (self.ctx.python_package_dir(module) if module else None)
        internal = target_file is not None or target_dir is not None or level > 0
        remaining: list[str] = []
        for alias in node.names:
            if alias.name == "*":
                remaining.append("*")
                continue
            local = alias.asname or alias.name
            sub = f"{module}.{alias.name}" if module else alias.name
            sub_file = self.ctx.resolve_python_module(sub) if internal else None
            if sub_file is not None:
                self.imports_seen.add(sub)
                self.imported_modules.setdefault(sub, sub_file)
                self.fa.imports.append(
                    ImportRef(
                        module=sub,
                        line=node.lineno,
                        alias=alias.asname,
                        resolved_path=sub_file,
                        external=False,
                    )
                )
                self.bindings[local] = Binding(kind="module", path=sub_file)
                continue
            remaining.append(alias.name)
            if target_file is not None:
                self.bindings[local] = Binding(kind="symbol", path=target_file, attr=alias.name)
            elif not internal:
                self.bindings[local] = Binding(kind="symbol", external=module, attr=alias.name)
        if remaining:
            self.fa.imports.append(
                ImportRef(
                    module=module,
                    names=remaining,
                    line=node.lineno,
                    resolved_path=target_file,
                    resolved_dir=target_dir,
                    external=not internal,
                    package=None if internal else self._external_package(module),
                )
            )

    def _imports(self, prefix: str) -> bool:
        return any(m == prefix or m.startswith(prefix + ".") for m in self.imports_seen)

    def _origin(self, name: str) -> str | None:
        """External module a local name was imported from (`APIRouter` → `fastapi`), if any."""
        binding = self.bindings.get(name)
        return binding.external if binding is not None else None

    def _name_from(self, parts: Sequence[str], module: str | None) -> bool:
        """Is the callee `parts` (e.g. ["APIRouter"] or ["fastapi", "APIRouter"]) plausibly from `module`?"""
        if module is None:
            return True
        origin = self._origin(parts[0])
        if origin is not None:
            return origin == module or origin.startswith(module + ".")
        if parts[0] in self.top_level:  # a local class with the same name
            return False
        return self._imports(module)

    # ------------------------------------------------------------------ pre-scan (types, routers, bases)

    def _prescan(self, tree: ast.Module) -> None:
        for stmt in tree.body:
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                self.top_level[stmt.name] = "class" if isinstance(stmt, ast.ClassDef) else "function"
            elif isinstance(stmt, ast.Assign | ast.AnnAssign):
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        self.top_level.setdefault(target.id, "variable")
                        if target.id == "__all__":
                            self.all_names = _str_list(stmt.value)
        self._prescan_body(tree.body, prefix="", class_qualname=None)

    def _prescan_body(self, body: Sequence[ast.stmt], prefix: str, class_qualname: str | None) -> None:
        for stmt in body:
            if isinstance(stmt, ast.ClassDef):
                qual = f"{prefix}{stmt.name}"
                attrs = self.attr_types.setdefault(qual, {})
                for item in stmt.body:
                    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        t = _annotation_type(item.annotation)
                        if _is_classlike(t) and t is not None:
                            attrs.setdefault(item.target.id, t)
                    if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                        self._prescan_self_attrs(item, attrs)
                self._prescan_body(stmt.body, prefix=f"{qual}.", class_qualname=qual)
            elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                qual = f"{prefix}{stmt.name}"
                ret = _annotation_type(stmt.returns)
                if _is_classlike(ret) and ret is not None:
                    self.return_types[qual] = ret
                self._prescan_body(stmt.body, prefix=f"{qual}.", class_qualname=None)

    def _prescan_self_attrs(
        self, fn: ast.FunctionDef | ast.AsyncFunctionDef, attrs: dict[str, list[str]]
    ) -> None:
        params: dict[str, list[str]] = {}
        for arg in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]:
            t = _annotation_type(arg.annotation)
            if _is_classlike(t) and t is not None:
                params[arg.arg] = t
        for node in ast.walk(fn):
            targets: list[ast.expr] = []
            value: ast.expr | None = None
            annotation: ast.expr | None = None
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign):
                targets, value, annotation = [node.target], node.value, node.annotation
            for target in targets:
                if not (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    continue
                inferred = (
                    _annotation_type(annotation)
                    if annotation is not None
                    else self._value_type(value, params)
                )
                if _is_classlike(inferred) and inferred is not None:
                    attrs.setdefault(target.attr, inferred)

    def _value_type(
        self, value: ast.expr | None, params: dict[str, list[str]] | None = None
    ) -> list[str] | None:
        if isinstance(value, ast.Await):
            value = value.value
        if isinstance(value, ast.Call):
            parts = dotted(value.func)
            if _is_classlike(parts):
                return parts
            return None
        if isinstance(value, ast.Name) and params and value.id in params:
            return params[value.id]
        if isinstance(value, ast.ListComp | ast.SetComp | ast.GeneratorExp) and isinstance(
            value.elt, ast.Call
        ):
            parts = dotted(value.elt.func)
            return parts if _is_classlike(parts) else None
        if isinstance(value, ast.List | ast.Tuple) and value.elts and isinstance(value.elts[0], ast.Call):
            parts = dotted(value.elts[0].func)
            return parts if _is_classlike(parts) else None
        return None

    # ------------------------------------------------------------------ walking

    def _walk(self, body: Sequence[ast.stmt], scope: _Scope) -> None:
        for stmt in body:
            self._stmt(stmt, scope)

    def _qual(self, scope: _Scope, name: str) -> str:
        return f"{scope.qualname}.{name}" if scope.qualname else name

    def _stmt(self, stmt: ast.stmt, scope: _Scope) -> None:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            self._function(stmt, scope)
        elif isinstance(stmt, ast.ClassDef):
            self._class(stmt, scope)
        elif isinstance(stmt, ast.Assign | ast.AnnAssign | ast.AugAssign):
            self._assign(stmt, scope)
        elif isinstance(stmt, ast.Import | ast.ImportFrom):
            return
        elif isinstance(stmt, ast.If):
            if scope.kind == "module" and _is_main_guard(stmt.test):
                self.app_objects.add("main_guard")
            self._expr(stmt.test, scope)
            self._walk(stmt.body, scope)
            self._walk(stmt.orelse, scope)
        elif isinstance(stmt, ast.For | ast.AsyncFor | ast.While):
            self._expr(stmt.iter if isinstance(stmt, ast.For | ast.AsyncFor) else stmt.test, scope)
            self._walk(stmt.body, scope)
            self._walk(stmt.orelse, scope)
        elif isinstance(stmt, ast.With | ast.AsyncWith):
            for item in stmt.items:
                self._expr(item.context_expr, scope)
                if isinstance(item.optional_vars, ast.Name) and scope.kind == "function":
                    t = self._value_type(item.context_expr)
                    if t is not None:
                        scope.types[item.optional_vars.id] = t
            self._walk(stmt.body, scope)
        elif isinstance(stmt, ast.Try | ast.TryStar):
            self._walk(stmt.body, scope)
            for handler in stmt.handlers:
                self._walk(handler.body, scope)
            self._walk(stmt.orelse, scope)
            self._walk(stmt.finalbody, scope)
        elif isinstance(stmt, ast.Match):
            self._expr(stmt.subject, scope)
            for case in stmt.cases:
                self._walk(case.body, scope)
        else:
            for child in ast.iter_child_nodes(stmt):
                if isinstance(child, ast.expr):
                    self._expr(child, scope)

    # ------------------------------------------------------------------ definitions

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, scope: _Scope) -> None:
        qual = self._qual(scope, node.name)
        is_method = scope.kind == "class"
        kind: SymbolKind = "method" if is_method else "function"
        decorators = [_unparse(d) for d in node.decorator_list]
        start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        signature = f"({_unparse(node.args, 400)})" + (
            f" -> {_unparse(node.returns)}" if node.returns else ""
        )
        self._add_symbol(
            SymbolDef(
                name=node.name,
                qualname=qual,
                kind=kind,
                start_line=start,
                end_line=node.end_lineno or node.lineno,
                parent=scope.qualname,
                signature=signature,
                docstring=_truncate(ast.get_docstring(node, clean=True)),
                decorators=decorators,
                is_async=isinstance(node, ast.AsyncFunctionDef),
            )
        )
        for dec in node.decorator_list:
            self._decorator(dec, node, qual, scope)
        self._name_hints(node.name, qual)
        inner = _Scope(
            "function",
            qual,
            class_qualname=scope.qualname if is_method else scope.class_qualname,
            parent=scope,
        )
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            t = _annotation_type(arg.annotation)
            if _is_classlike(t) and t is not None:
                inner.types[arg.arg] = t
        for default in [*node.args.defaults, *(d for d in node.args.kw_defaults if d is not None)]:
            self._expr(default, inner)
        self._walk(node.body, inner)

    def _class(self, node: ast.ClassDef, scope: _Scope) -> None:
        qual = self._qual(scope, node.name)
        bases = [_unparse(b) for b in node.bases]
        start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        self._add_symbol(
            SymbolDef(
                name=node.name,
                qualname=qual,
                kind="class",
                start_line=start,
                end_line=node.end_lineno or node.lineno,
                parent=scope.qualname,
                docstring=_truncate(ast.get_docstring(node, clean=True)),
                decorators=[_unparse(d) for d in node.decorator_list],
                bases=bases,
            )
        )
        for base in node.bases:
            self._expr(base, scope)
        self._schema(node, qual)
        self._arq_worker(node, qual)
        self._name_hints(node.name, qual)
        self._walk(node.body, _Scope("class", qual, class_qualname=qual, parent=scope))

    def _add_symbol(self, sym: SymbolDef) -> None:
        if sym.qualname in self.symbol_qualnames:  # redefinition (e.g. `if TYPE_CHECKING` branches)
            return
        self.symbol_qualnames.add(sym.qualname)
        self.fa.symbols.append(sym)

    def _assign(self, stmt: ast.Assign | ast.AnnAssign | ast.AugAssign, scope: _Scope) -> None:
        value = stmt.value
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        names = [t for t in targets if isinstance(t, ast.Name)]
        if isinstance(stmt, ast.AnnAssign) and scope.kind == "function" and isinstance(stmt.target, ast.Name):
            t = _annotation_type(stmt.annotation)
            if _is_classlike(t) and t is not None:
                scope.types[stmt.target.id] = t
        if scope.kind == "module" and not isinstance(stmt, ast.AugAssign):
            for target in names:
                if target.id.startswith("__") and target.id.endswith("__"):
                    continue
                self._add_symbol(
                    SymbolDef(
                        name=target.id,
                        qualname=target.id,
                        kind="variable",
                        start_line=stmt.lineno,
                        end_line=stmt.end_lineno or stmt.lineno,
                        signature=_unparse(stmt.annotation) if isinstance(stmt, ast.AnnAssign) else None,
                    )
                )
        if value is None:
            return
        if scope.kind == "function":
            inferred = self._inferred_type(value, scope)
            for target in names:
                if inferred is not None:
                    scope.types[target.id] = inferred
        if isinstance(value, ast.Call):
            for tgt in targets:
                var = ".".join(dotted(tgt) or [])
                if var:
                    self._maybe_router(var, value, stmt.lineno)
            callee = dotted(value.func)
            if callee and callee[-1] in _SQLA_BASE_FACTORIES:
                self.sqla_bases.update(t.id for t in names)
        if scope.kind == "module":
            for target in names:
                if target.id in ("INSTALLED_APPS", "ROOT_URLCONF", "MIDDLEWARE"):
                    self.app_objects.add("django_settings")
        self._expr(value, scope)

    def _inferred_type(self, value: ast.expr, scope: _Scope) -> list[str] | None:
        direct = self._value_type(value)
        if direct is not None:
            return direct
        inner = value.value if isinstance(value, ast.Await) else value
        if isinstance(inner, ast.Call):
            parts = dotted(inner.func)
            if parts:
                target = self._pre_resolve(parts, scope)
                if target is not None and target[0] == self.path and target[1] in self.return_types:
                    return self.return_types[target[1]]
        return None

    def _maybe_router(self, var: str, call: ast.Call, line: int) -> None:
        parts = dotted(call.func)
        if not parts:
            return
        name = parts[-1]
        spec = _ROUTER_CTORS.get(name)
        if spec is not None and self._name_from(parts, spec[3]):
            kind, app_object, prefix_kw, _module = spec
            prefix = _const_str(_kwarg(call, prefix_kw)) if prefix_kw else None
            self.routers.setdefault(var, RouterDef(var=var, kind=kind, prefix=prefix or "", line=line))
            if app_object:
                self.app_objects.add(app_object)

    # ------------------------------------------------------------------ decorators: routes, tasks, cli

    def _decorator(
        self, dec: ast.expr, fn: ast.FunctionDef | ast.AsyncFunctionDef, qual: str, scope: _Scope
    ) -> None:
        call = dec if isinstance(dec, ast.Call) else None
        parts = dotted(call.func if call is not None else dec)
        if not parts:
            if call is not None:
                self._expr(call, scope)
            return
        last = parts[-1]
        if call is not None and last in _ROUTE_DECORATORS:
            self._decorator_route(parts, call, fn, qual, dec.lineno)
        if last in ("task", "shared_task", "periodic_task", "actor", "job", "db_task", "db_periodic_task"):
            self._decorator_task(parts, call, fn, qual)
        if parts[0] == "click" or (last in ("command", "group") and self._imports("click")):
            self.app_objects.add("click")
        if last == "command" and parts[0] != "click" and self._imports("typer"):
            self.app_objects.add("typer")
        if last == "tool" and (
            self._imports("langchain_core")
            or self._imports("langchain")
            or self._imports("mcp")
            or self._imports("fastmcp")
            or self._imports("smolagents")
            or self._imports("crewai")
        ):
            self._ai_hint("agent", "tool", dec.lineno, qual)
        if call is not None:
            for arg in [*call.args, *(k.value for k in call.keywords)]:
                self._expr(arg, scope)

    def _route_framework(self, var: str) -> str | None:
        router = self.routers.get(var)
        if router is not None:
            return _ROUTER_FRAMEWORK.get(router.kind)
        for module, framework in _WEB_IMPORT_FRAMEWORKS:
            if self._imports(module):
                return framework
        return None

    def _decorator_route(
        self,
        parts: list[str],
        call: ast.Call,
        fn: ast.FunctionDef | ast.AsyncFunctionDef,
        qual: str,
        line: int,
    ) -> None:
        method_name = parts[-1]
        if len(parts) == 1:  # Litestar / Sanic-style bare `@get("/x")`
            if not (self._imports("litestar") and method_name in _HTTP_METHODS):
                return
            var: str | None = None
            framework: str | None = "litestar"
        else:
            var = ".".join(parts[:-1])
            framework = self._route_framework(var)
            if var not in self.routers and (framework is None or parts[-2] not in _COMMON_ROUTER_NAMES):
                return
        path_node = call.args[0] if call.args else _kwarg(call, "path", "rule", "uri")
        if path_node is None:
            return
        path = _const_str(path_node)
        if path is None:
            if isinstance(path_node, ast.List | ast.Tuple):
                paths = _str_list(path_node)
                path = paths[0] if paths else _unparse(path_node)
            else:
                path = _unparse(path_node)
        if method_name in ("route", "api_route"):
            methods = [m.upper() for m in _str_list(_kwarg(call, "methods"))] or ["GET"]
        elif method_name.startswith("websocket"):
            methods = ["WEBSOCKET"]
        else:
            methods = [method_name.upper()]
        for method in methods:
            self.fa.routes.append(
                RouteDef(
                    method=method,
                    path=path,
                    line=line,
                    framework=framework or "unknown",
                    router=var,
                    handler=qual,
                )
            )

    def _task_technology(self, parts: Sequence[str]) -> str:
        if parts[-1] == "actor":
            return "dramatiq"
        if parts[-1] == "job":
            return "rq"
        origin = self._origin(parts[0]) or ""
        for module, tech in _TASK_TECH_BY_IMPORT:
            if origin == module or origin.startswith(module + "."):
                return tech
        # receiver created in this file or imported from the repository: infer from what the file imports
        for module, tech in _TASK_TECH_BY_IMPORT:
            if self._imports(module):
                return tech
        return "celery"

    def _decorator_task(
        self, parts: list[str], call: ast.Call | None, fn: ast.FunctionDef | ast.AsyncFunctionDef, qual: str
    ) -> None:
        last = parts[-1]
        if last == "job" and not self._imports("rq"):
            return
        if last == "actor" and not self._imports("dramatiq"):
            return
        if len(parts) == 1 and last not in ("actor", "job"):
            # bare `@task` / `@shared_task`: only when imported from a task-queue library (not Prefect/Invoke)
            origin = self._origin(last) or ""
            if not origin.startswith(("celery", "huey", "taskiq")):
                return
        tech = self._task_technology(parts)
        explicit = _const_str(_kwarg(call, "name", "actor_name")) if call is not None else None
        self.fa.tasks.append(TaskDef(name=explicit or fn.name, technology=tech, line=fn.lineno, symbol=qual))
        if tech == "dramatiq":
            self.dramatiq_actors.add(fn.name)
            self.app_objects.add("dramatiq")
        elif tech in ("celery", "huey", "taskiq"):
            self.app_objects.add(tech)

    def _arq_worker(self, node: ast.ClassDef, qual: str) -> None:
        if not (self._imports("arq") or "Worker" in node.name):
            return
        found = False
        for item in node.body:
            if not (isinstance(item, ast.Assign | ast.AnnAssign)):
                continue
            targets = item.targets if isinstance(item, ast.Assign) else [item.target]
            names = {t.id for t in targets if isinstance(t, ast.Name)}
            if not names & {"functions", "cron_jobs"} or not isinstance(item.value, ast.List | ast.Tuple):
                continue
            for elt in item.value.elts:
                fn_node: ast.expr | None = elt
                explicit: str | None = None
                if isinstance(elt, ast.Call):
                    fn_node = elt.args[0] if elt.args else _kwarg(elt, "coroutine", "func")
                    explicit = _const_str(_kwarg(elt, "name"))
                parts = dotted(fn_node)
                if not parts and explicit is None:
                    continue
                name = explicit or (parts[-1] if parts else "")
                symbol = (
                    parts[0]
                    if parts and len(parts) == 1 and self.top_level.get(parts[0]) == "function"
                    else None
                )
                self.fa.tasks.append(TaskDef(name=name, technology="arq", line=elt.lineno, symbol=symbol))
                found = True
        if found:
            self.app_objects.add("arq_worker")

    # ------------------------------------------------------------------ schemas

    def _schema(self, node: ast.ClassDef, qual: str) -> None:
        base_parts = [dotted(b.value if isinstance(b, ast.Subscript) else b) for b in node.bases]
        base_last = {p[-1] for p in base_parts if p}
        decorators = {
            ".".join(p)
            for d in node.decorator_list
            if (p := dotted(d.func if isinstance(d, ast.Call) else d))
        }
        body_names = {
            t.id for s in node.body if isinstance(s, ast.Assign) for t in s.targets if isinstance(t, ast.Name)
        }
        if base_last & _SQLA_BASE_CLASSES and not (body_names & {"__tablename__", "__table__"}):
            self.sqla_bases.add(node.name)
            return
        if any(
            isinstance(s, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "__abstract__" for t in s.targets)
            for s in node.body
        ):
            self.sqla_bases.add(node.name)
            return

        kind: str | None = None
        orm = False
        table_kw = _kwarg_class(node, "table")
        if "__tablename__" in body_names or "__table__" in body_names:
            kind, orm = "orm_model", True
        elif base_last & {"SQLModel"}:
            kind = (
                "orm_model" if isinstance(table_kw, ast.Constant) and table_kw.value is True else "pydantic"
            )
            orm = kind == "orm_model"
        elif base_last & self.sqla_bases:
            kind, orm = "orm_model", True
        elif any(p and len(p) >= 2 and p[-1] == "Model" and p[-2] == "models" for p in base_parts) or (
            "Model" in base_last and self._imports("django")
        ):
            kind, orm = "orm_model", True
            self.django_models = True
        elif base_last & _PYDANTIC_BASES and (
            self._imports("pydantic") or self._imports("pydantic_settings") or self._imports("fastapi")
        ):
            kind = "pydantic"
        elif decorators & _DATACLASS_DECORATORS or any(d.endswith(".dataclass") for d in decorators):
            kind = "dataclass"
        elif base_last & _OTHER_SCHEMA_BASES:
            kind = "other"
        else:
            local = [self.local_schema_kinds[b] for b in base_last if b in self.local_schema_kinds]
            if local:
                kind = local[0]
                orm = kind == "orm_model"
            elif any(p and self._binding_is_internal(p[0]) for p in base_parts):
                kind = CANDIDATE_SCHEMA_KIND
        if kind is None:
            return
        fields = self._schema_fields(node, orm=orm)
        if kind == CANDIDATE_SCHEMA_KIND and not fields:
            return
        self.local_schema_kinds[node.name] = kind
        table = _const_str(_class_assign(node, "__tablename__"))
        if table is None:
            table = self._django_db_table(node)
        self.fa.schemas.append(
            SchemaDef(
                name=node.name,
                schema_kind=kind,
                start_line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                fields=fields,
                symbol=qual,
                table=table,
            )
        )

    def _binding_is_internal(self, name: str) -> bool:
        binding = self.bindings.get(name)
        return binding is not None and binding.path is not None

    def _django_db_table(self, node: ast.ClassDef) -> str | None:
        for item in node.body:
            if isinstance(item, ast.ClassDef) and item.name == "Meta":
                return _const_str(_class_assign(item, "db_table"))
        return None

    def _schema_fields(self, node: ast.ClassDef, *, orm: bool) -> list[FieldDef]:
        fields: list[FieldDef] = []
        local_schemas = set(self.local_schema_kinds)
        for item in node.body:
            name: str | None = None
            annotation: ast.expr | None = None
            value: ast.expr | None = None
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                name, annotation, value = item.target.id, item.annotation, item.value
                ann = _unparse(annotation)
                if ann.startswith(("ClassVar", "typing.ClassVar", "Final")):
                    continue
            elif (
                isinstance(item, ast.Assign)
                and len(item.targets) == 1
                and isinstance(item.targets[0], ast.Name)
            ):
                if not isinstance(item.value, ast.Call):
                    continue
                name, value = item.targets[0].id, item.value
            if name is None or name in _SKIP_FIELD_NAMES or (name.isupper() and not orm):
                continue
            fields.append(self._field(name, annotation, value, local_schemas))
        return fields

    def _field(
        self, name: str, annotation: ast.expr | None, value: ast.expr | None, local_schemas: set[str]
    ) -> FieldDef:
        attrs: list[str] = []
        relation: str | None = None
        type_text: str | None = None
        if annotation is not None:
            ann_parts = dotted(annotation.value) if isinstance(annotation, ast.Subscript) else None
            if ann_parts and ann_parts[-1] == "Mapped" and isinstance(annotation, ast.Subscript):
                type_text = _unparse(annotation.slice)
            else:
                type_text = _unparse(annotation)
            text = type_text or ""
            if "Optional[" in text or "| None" in text or "None |" in text:
                attrs.append("optional")
            if re.search(r"\b(list|List|Sequence|set|Set)\[", text):
                attrs.append("many")
            for schema_name in local_schemas:
                if schema_name != name and re.search(rf"\b{re.escape(schema_name)}\b", text):
                    relation = schema_name
                    break
        if isinstance(value, ast.Call):
            callee = dotted(value.func) or []
            call_name = callee[-1] if callee else ""
            if call_name in ("relationship", "Relationship"):
                attrs.append("relationship")
                target = _const_str(value.args[0]) if value.args else None
                target_type = _annotation_type(annotation) if annotation is not None else None
                relation = target or (target_type[-1] if target_type else relation)
            elif call_name in ("ForeignKey", "OneToOneField", "ManyToManyField"):
                attrs.append(
                    {"ForeignKey": "fk", "OneToOneField": "one_to_one", "ManyToManyField": "many_to_many"}[
                        call_name
                    ]
                )
                target_node = value.args[0] if value.args else _kwarg(value, "to")
                relation = _const_str(target_node) or ".".join(dotted(target_node) or []) or relation
                type_text = type_text or call_name
            elif (
                call_name in _SQLA_COLUMN_CALLS
                or (callee[:1] == ["models"] and call_name.endswith("Field"))
                or call_name.endswith("Field")
            ):
                if type_text is None:
                    type_text = call_name if call_name.endswith("Field") and call_name != "Field" else None
            for arg in value.args:
                if isinstance(arg, ast.Call) and (dotted(arg.func) or [""])[-1] == "ForeignKey":
                    attrs.append("fk")
                    target = _const_str(arg.args[0]) if arg.args else None
                    if target:
                        relation = target.split(".", 1)[0]
                elif type_text is None and call_name in ("Column", "mapped_column"):
                    candidate = dotted(arg.func) if isinstance(arg, ast.Call) else dotted(arg)
                    if candidate and candidate[-1][:1].isupper():
                        type_text = _unparse(arg)
            for kw in value.keywords:
                if kw.arg is None:
                    continue
                const = kw.value.value if isinstance(kw.value, ast.Constant) else None
                if (
                    kw.arg in ("primary_key", "unique", "index", "autoincrement", "db_index")
                    and const is True
                ):
                    attrs.append("index" if kw.arg == "db_index" else kw.arg)
                elif kw.arg in ("nullable", "null") and const is not None:
                    attrs.append("nullable" if const else "not_null")
                elif kw.arg in ("default", "server_default", "default_factory", "auto_now", "auto_now_add"):
                    attrs.append("default")
                elif kw.arg in ("back_populates", "backref", "related_name"):
                    attrs.append(f"{kw.arg}={const}" if isinstance(const, str) else kw.arg)
                elif kw.arg in ("on_delete", "ondelete", "cascade"):
                    attrs.append(f"{kw.arg}={_unparse(kw.value, 40)}")
                elif isinstance(const, int | float | str | bool) and kw.arg in (
                    "min_length",
                    "max_length",
                    "ge",
                    "le",
                    "gt",
                    "lt",
                    "pattern",
                    "alias",
                    "max_digits",
                    "decimal_places",
                ):
                    attrs.append(f"{kw.arg}={const}")
            if (
                call_name == "Field"
                and value.args
                and not (isinstance(value.args[0], ast.Constant) and value.args[0].value is Ellipsis)
            ):
                attrs.append("default")
        elif value is not None:
            attrs.append("default")
            if isinstance(value, ast.Constant) and value.value is None and "optional" not in attrs:
                attrs.append("optional")
        return FieldDef(name=name, type=type_text, relation=relation, attributes=list(dict.fromkeys(attrs)))

    # ------------------------------------------------------------------ expressions and calls

    def _expr(self, node: ast.expr, scope: _Scope) -> None:
        for sub in _walk_expr(node):
            if isinstance(sub, ast.Call):
                self._call(sub, scope)

    def _call(self, call: ast.Call, scope: _Scope) -> None:
        parts = dotted(call.func)
        if not parts:
            return
        caller = scope.qualname
        line = call.lineno
        ref = CallRef(caller=caller, parts=parts, line=line)
        resolved = self._pre_resolve(parts, scope)
        if resolved is not None:
            ref.resolved_path, ref.resolved_qualname, ref.confidence = resolved
        self.calls.append(ref)

        last = parts[-1]
        if last in ("Depends", "Security") and call.args:
            dep = dotted(call.args[0])
            if dep:
                dep_ref = CallRef(caller=caller, parts=dep, line=line)
                dep_resolved = self._pre_resolve(dep, scope)
                if dep_resolved is not None:
                    dep_ref.resolved_path, dep_ref.resolved_qualname, dep_ref.confidence = dep_resolved
                self.calls.append(dep_ref)
        self._app_object_call(parts)
        self._mount_or_route_call(parts, call, scope)
        self._publish_call(parts, call, caller)
        self._data_access_call(parts, call, scope)
        self._ai_call(parts, call, caller)

    def _pre_resolve(self, parts: Sequence[str], scope: _Scope) -> tuple[str, str, float] | None:
        head = parts[0]
        cls = scope.class_qualname
        if head in ("self", "cls") and cls:
            if len(parts) == 2:
                return self.path, f"{cls}.{parts[1]}", 0.85
            if len(parts) >= 3:
                attr_type = self.attr_types.get(cls, {}).get(parts[1])
                if attr_type is not None:
                    hit = self._resolve_parts([*attr_type, *parts[2:]])
                    return (hit[0], hit[1], 0.7) if hit else None
            return None
        local_type = scope.lookup(head)
        if local_type is not None and len(parts) >= 2:
            hit = self._resolve_parts([*local_type, *parts[1:]])
            return (hit[0], hit[1], 0.7) if hit else None
        hit = self._resolve_parts(parts)
        return (hit[0], hit[1], 0.9) if hit else None

    def _resolve_parts(self, parts: Sequence[str]) -> tuple[str, str] | None:
        for i in range(len(parts), 0, -1):
            module = ".".join(parts[:i])
            if module in self.imported_modules and i > 1:
                rest = parts[i:]
                return (self.imported_modules[module], ".".join(rest)) if rest else None
        head = parts[0]
        binding = self.bindings.get(head)
        if binding is not None:
            if binding.path is None:
                return None
            if binding.kind == "module":
                rest = parts[1:]
                return (binding.path, ".".join(rest)) if rest else None
            return binding.path, ".".join([binding.attr or head, *parts[1:]])
        if head in self.top_level:
            return self.path, ".".join(parts)
        return None

    def _app_object_call(self, parts: Sequence[str]) -> None:
        spec = _APP_OBJECT_CALLS.get(parts[-1])
        if spec is None:
            return
        app_object, module = spec
        if parts[-1] == "run" and not (len(parts) == 2 and parts[0] == "uvicorn"):
            return
        if module is None or self._name_from(parts, module):
            self.app_objects.add(app_object)

    def _mount_or_route_call(self, parts: Sequence[str], call: ast.Call, scope: _Scope) -> None:
        last = parts[-1]
        receiver = ".".join(parts[:-1]) if len(parts) > 1 else None
        if last == "include_router" and receiver and call.args:
            self.fa.mounts.append(
                MountDef(
                    parent=receiver,
                    target=dotted(call.args[0]) or [],
                    prefix=_const_str(_kwarg(call, "prefix")) or "",
                    line=call.lineno,
                )
            )
        elif last == "register_blueprint" and receiver and call.args:
            self.fa.mounts.append(
                MountDef(
                    parent=receiver,
                    target=dotted(call.args[0]) or [],
                    prefix=_const_str(_kwarg(call, "url_prefix")) or "",
                    line=call.lineno,
                )
            )
        elif last == "mount" and receiver in self.routers and len(call.args) >= 2:
            self.fa.mounts.append(
                MountDef(
                    parent=receiver,
                    target=dotted(call.args[1]) or [],
                    prefix=_const_str(call.args[0]) or "",
                    line=call.lineno,
                )
            )
        elif (
            last in ("add_api_route", "add_url_rule", "add_route", "add_api_websocket_route")
            and receiver
            and call.args
        ):
            path = _const_str(call.args[0])
            if path is None:
                return
            if last == "add_url_rule":
                handler_node = _kwarg(call, "view_func") or (call.args[2] if len(call.args) >= 3 else None)
            else:
                handler_node = call.args[1] if len(call.args) >= 2 else _kwarg(call, "endpoint", "handler")
            methods = [m.upper() for m in _str_list(_kwarg(call, "methods"))] or (
                ["WEBSOCKET"] if "websocket" in last else ["GET"]
            )
            if (
                last == "add_route"
                and len(call.args) >= 2
                and isinstance(call.args[0], ast.Constant)
                and call.args[0].value in {m.upper() for m in _HTTP_METHODS}
            ):
                methods, path, handler_node = (
                    [str(call.args[0].value)],
                    _const_str(call.args[1]) or path,
                    call.args[2] if len(call.args) >= 3 else None,
                )
            framework = self._route_framework(receiver) or "unknown"
            self._handler_route(methods, path, call.lineno, framework, receiver, handler_node)
        elif (
            last in ("path", "re_path", "url")
            and len(parts) <= 3
            and self._imports_django_urls()
            and call.args
        ):
            self._django_path(call)
        elif (
            last == "register"
            and receiver in self.routers
            and self.routers[receiver].kind == "drf_router"
            and len(call.args) >= 2
        ):
            prefix = _const_str(call.args[0]) or ""
            self._handler_route(["ANY"], prefix, call.lineno, "django_rest_framework", receiver, call.args[1])
        elif last == "Route" and self._imports("starlette") and call.args:
            path = _const_str(call.args[0])
            if path is not None:
                methods = [m.upper() for m in _str_list(_kwarg(call, "methods"))] or ["GET"]
                handler_node = call.args[1] if len(call.args) >= 2 else _kwarg(call, "endpoint")
                self._handler_route(methods, path, call.lineno, "starlette", None, handler_node)

    def _imports_django_urls(self) -> bool:
        return self._imports("django.urls") or self._imports("django.conf.urls")

    def _handler_route(
        self,
        methods: Sequence[str],
        path: str,
        line: int,
        framework: str,
        router: str | None,
        handler_node: ast.expr | None,
    ) -> None:
        if isinstance(handler_node, ast.Call):
            inner = dotted(handler_node.func)
            if inner and inner[-1] in ("as_view", "as_asgi"):
                handler_node = (
                    handler_node.func.value if isinstance(handler_node.func, ast.Attribute) else None
                )
        handler_parts = dotted(handler_node) if handler_node is not None else None
        handler: str | None = None
        handler_ref: list[str] | None = None
        if handler_parts:
            joined = ".".join(handler_parts)
            if handler_parts[0] in self.top_level and handler_parts[0] not in self.bindings:
                handler = joined
            else:
                handler_ref = handler_parts
        for method in methods:
            self.fa.routes.append(
                RouteDef(
                    method=method,
                    path=path,
                    line=line,
                    framework=framework,
                    router=router,
                    handler=handler,
                    handler_ref=handler_ref,
                )
            )

    def _django_path(self, call: ast.Call) -> None:
        route = _const_str(call.args[0])
        if route is None or len(call.args) < 2:
            return
        view = call.args[1]
        if isinstance(view, ast.Call) and (dotted(view.func) or [""])[-1] == "include":
            target = view.args[0] if view.args else None
            module = _const_str(target)
            if module is None and isinstance(target, ast.Tuple) and target.elts:
                module = _const_str(target.elts[0])
            target_parts = dotted(target) if module is None else None
            if target_parts and target_parts[-1] == "urls":
                target_parts = target_parts[:-1]
            self.fa.mounts.append(
                MountDef(
                    parent="urlpatterns",
                    target=target_parts or [],
                    prefix=route,
                    line=call.lineno,
                    target_module=module,
                )
            )
            return
        self._handler_route(["ANY"], route, call.lineno, "django", "urlpatterns", view)
        self.app_objects.add("django")

    def _publish_call(self, parts: Sequence[str], call: ast.Call, caller: str | None) -> None:
        last = parts[-1]
        tech: str | None = None
        task: str | None = None
        if last in ("delay", "apply_async") and len(parts) >= 2:
            tech, task = "celery", parts[-2]
        elif last == "send_task" and call.args:
            tech, task = "celery", _const_str(call.args[0])
        elif last == "enqueue_job" and call.args:
            tech, task = "arq", _const_str(call.args[0])
        elif (
            last in ("enqueue", "enqueue_call", "enqueue_in", "enqueue_at")
            and len(parts) >= 2
            and (self._imports("rq") or "queue" in parts[-2].lower() or parts[-2] in ("q",))
        ):
            fn_node = _kwarg(call, "func", "f")
            if fn_node is None and call.args:
                fn_node = (
                    call.args[1]
                    if last in ("enqueue_in", "enqueue_at") and len(call.args) >= 2
                    else call.args[0]
                )
            name = _const_str(fn_node) or ".".join(dotted(fn_node) or [])
            if name:
                tech, task = "rq", name.rsplit(".", 1)[-1]
        elif (
            last in ("send", "send_with_options")
            and len(parts) >= 2
            and (
                parts[-2] in self.dramatiq_actors
                or (self._imports("dramatiq") and self._binding_is_internal(parts[-2]))
            )
        ):
            tech, task = "dramatiq", parts[-2]
        elif last == "kiq" and len(parts) >= 2:
            tech, task = "taskiq", parts[-2]
        elif (
            last in ("send", "send_and_wait", "produce")
            and call.args
            and (self._imports("kafka") or self._imports("aiokafka") or self._imports("confluent_kafka"))
        ):
            tech, task = "kafka", _const_str(call.args[0]) or _const_str(_kwarg(call, "topic"))
        elif last == "basic_publish" and (self._imports("pika") or self._imports("aio_pika")):
            tech, task = (
                "rabbitmq",
                _const_str(_kwarg(call, "routing_key")) or _const_str(_kwarg(call, "exchange")),
            )
        elif last == "publish" and call.args and self._imports("google.cloud.pubsub"):
            tech, task = "pubsub", _const_str(call.args[0]) or ".".join(dotted(call.args[0]) or [])
        elif last == "publish" and call.args and (self._imports("redis") or self._imports("aioredis")):
            tech, task = "redis_pubsub", _const_str(call.args[0])
        if tech and task:
            self.fa.publishes.append(PublishRef(caller=caller, task=task, technology=tech, line=call.lineno))

    def _orm_name(self) -> str:
        return "sqlmodel" if self._imports("sqlmodel") else "sqlalchemy"

    def _model_arg(self, node: ast.expr | None, scope: _Scope) -> str | None:
        if node is None:
            return None
        if isinstance(node, ast.Call):
            parts = dotted(node.func)
            return parts[-1] if _is_classlike(parts) and parts is not None else None
        parts = dotted(node)
        if not parts:
            return None
        if parts[0][:1].isupper():
            return parts[0] if len(parts) <= 2 else parts[-2]
        inferred = scope.lookup(parts[0]) if len(parts) == 1 else None
        return inferred[-1] if inferred else None

    def _data_access_call(self, parts: Sequence[str], call: ast.Call, scope: _Scope) -> None:
        last = parts[-1]
        caller = scope.qualname
        line = call.lineno
        first = call.args[0] if call.args else None

        def add(model: str | None, mode: Literal["read", "write"], orm: str) -> None:
            if model:
                self.fa.data_access.append(
                    DataAccess(caller=caller, model=model, mode=mode, line=line, orm=orm)
                )

        # Django: Model.objects.<method>(...)
        if len(parts) >= 3 and parts[-2] in ("objects", "_default_manager") and parts[-3][:1].isupper():
            if last in _DJANGO_READ:
                add(parts[-3], "read", "django")
            elif last in _DJANGO_WRITE:
                add(parts[-3], "write", "django")
            return
        # SQLAlchemy core constructs: select(Model), insert(Model), update(Model), delete(Model)
        if len(parts) == 1 and last in ("select", "insert", "update", "delete"):
            origin = self._origin(last) or ""
            if origin.startswith(("sqlalchemy", "sqlmodel")):
                for arg in call.args[:1] if last != "select" else call.args:
                    add(
                        self._model_arg(arg, scope), "read" if last == "select" else "write", self._orm_name()
                    )
            return
        if (
            len(parts) == 2
            and parts[0] in ("sa", "sqlalchemy")
            and last in ("select", "insert", "update", "delete")
        ):
            for arg in call.args[:1] if last != "select" else call.args:
                add(self._model_arg(arg, scope), "read" if last == "select" else "write", "sqlalchemy")
            return
        receiver = parts[-2].lower() if len(parts) >= 2 else ""
        receiver_type = scope.lookup(parts[-2]) if len(parts) == 2 else None
        session_like = (
            "session" in receiver
            or receiver in ("db", "sess", "s", "uow", "conn", "connection", "tx")
            or bool(receiver_type and receiver_type[-1].endswith("Session"))
        )
        if not session_like and len(parts) >= 3 and parts[0] == "self":
            attr_type = self.attr_types.get(scope.class_qualname or "", {}).get(parts[1])
            session_like = bool(attr_type and attr_type[-1].endswith("Session"))
        if (
            session_like
            and first is not None
            and (
                last == "query"
                or (last in ("get", "get_one", "scalar", "scalars") and _is_classlike(dotted(first)))
            )
        ):
            add(self._model_arg(first, scope), "read", self._orm_name())
        elif session_like and last in _SESSION_WRITE and first is not None:
            add(self._model_arg(first, scope), "write", self._orm_name())
        if last in _SQL_EXEC and first is not None:
            sql = _const_str(first)
            if (
                sql is None
                and isinstance(first, ast.Call)
                and (dotted(first.func) or [""])[-1] == "text"
                and first.args
            ):
                sql = _const_str(first.args[0])
            if sql and _SQL_START.match(sql):
                for table, mode in _sql_tables(sql):
                    add(table, mode, "sql")

    def _ai_call(self, parts: Sequence[str], call: ast.Call, caller: str | None) -> None:
        ctor = _AI_CTOR_PATTERNS.get(parts[-1])
        if ctor is not None:
            origin = self._origin(parts[0]) or ""
            required = _AI_CTOR_ORIGIN.get(parts[-1])
            if origin and (required is None or origin == required or origin.startswith(required + ".")):
                self._ai_hint(ctor[0], ctor[1], call.lineno, caller)
            return
        for tail, kind, tech in _AI_CALL_PATTERNS:
            if len(parts) > len(tail) and tuple(parts[-len(tail) :]) == tail:
                resolved_tech = tech or self._llm_tech()
                if resolved_tech is None:
                    continue
                if tech is not None and not any(self._imports(m) for m in _TECH_IMPORTS.get(tech, (tech,))):
                    continue
                if tail == ("rerank",) and not (
                    self._imports("cohere") or self._imports("rerankers") or self._imports("flashrank")
                ):
                    continue
                self._ai_hint(kind, resolved_tech, call.lineno, caller)
                return
        if (
            parts[-1] in ("fit", "fit_transform", "train")
            and len(parts) >= 2
            and (
                self._imports("sklearn")
                or self._imports("xgboost")
                or self._imports("lightgbm")
                or self._imports("keras")
                or self._imports("tensorflow")
            )
        ):
            self._ai_hint("training", self._ml_tech(), call.lineno, caller)

    def _llm_tech(self) -> str | None:
        for module in _LLM_CLIENT_TECH_BY_IMPORT:
            if self._imports(module):
                return _TECH_NAME.get(module, module)
        for module in ("langchain", "langchain_core", "langchain_openai", "llama_index"):
            if self._imports(module):
                return "langchain" if module.startswith("langchain") else "llama_index"
        return None

    def _ml_tech(self) -> str:
        for module, tech in (
            ("sklearn", "scikit_learn"),
            ("xgboost", "xgboost"),
            ("lightgbm", "lightgbm"),
            ("keras", "keras"),
            ("tensorflow", "tensorflow"),
        ):
            if self._imports(module):
                return tech
        return "unknown"

    def _name_hints(self, name: str, qual: str) -> None:
        if _RRF_NAME.search(name):
            self._ai_hint("fusion", "rrf", 0, qual)
        elif _CHUNK_NAME.match(name) and (
            "chunk" in self.path.lower() or "split" in self.path.lower() or "ingest" in self.path.lower()
        ):
            self._ai_hint("chunking", "custom", 0, qual)

    def _ai_hint(self, kind: str, tech: str, line: int, symbol: str | None) -> None:
        key = (kind, tech, symbol)
        if key in self.ai_keys:
            return
        self.ai_keys.add(key)
        if line == 0:
            sym = next((s for s in self.fa.symbols if s.qualname == symbol), None)
            line = sym.start_line if sym else 0
        self.fa.ai_hints.append(AIHint(component_kind=kind, technology=tech, line=line, symbol=symbol))

    def _import_ai_hints(self) -> None:
        for ref in self.fa.imports:
            if not ref.external:
                continue
            rule = match_ai_import(ref.module)
            if rule is None:
                for name in ref.names:
                    rule = match_ai_import(f"{ref.module}.{name}")
                    if rule is not None:
                        break
            if rule is not None:
                self._ai_hint(rule.component_kind, rule.technology, ref.line, None)


def _kwarg_class(node: ast.ClassDef, name: str) -> ast.expr | None:
    for kw in node.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _class_assign(node: ast.ClassDef, name: str) -> ast.expr | None:
    for item in node.body:
        if isinstance(item, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in item.targets
        ):
            return item.value
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and item.target.id == name:
            return item.value
    return None


def _is_main_guard(test: ast.expr) -> bool:
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and len(test.comparators) == 1
        and _const_str(test.comparators[0]) == "__main__"
    )


def _walk_expr(node: ast.AST) -> Iterator[ast.AST]:
    """`ast.walk` for one expression, in source order (depth-first)."""
    stack: list[ast.AST] = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(list(ast.iter_child_nodes(current))))


def _sql_tables(sql: str) -> Iterable[tuple[str, Literal["read", "write"]]]:
    seen: set[str] = set()
    for match in _SQL_TABLES.finditer(sql):
        name = match.group("name").split(".")[-1]
        keyword = re.sub(r"\s+", " ", match.group("kw").lower())
        if name.lower() in _SQL_NOT_TABLES or name in seen:
            continue
        seen.add(name)
        yield name, "write" if keyword in _SQL_WRITE_KW else "read"
