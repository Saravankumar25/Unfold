"""Code-aware chunker (LLD §10, ESD §11.1). Never fixed-character chunking.

Units, per file:
- source with symbols → one chunk per top-level function / class / method (nested functions stay inside their
  parent). A class that has methods yields a *header* chunk (signature + docstring + member list); a class without
  methods (dataclass / pydantic / ORM model) is kept whole. Endpoint handlers are tagged `endpoint`, model classes
  `schema`. Module-level code between symbols becomes `window` chunks.
- config → YAML/JSON top-level keys (compose: one chunk per service), TOML tables, Terraform blocks, Dockerfile
  stages.
- Markdown / reStructuredText → one chunk per heading section.
- anything else → 60-line windows with 10 lines overlap.
Oversized units are split to ≤120 lines. Tier-4 and non-text files are skipped.

Chunk text = `path :: qualname` header line + REDACTED body; trust labels come from the injection scanner.
"""

from __future__ import annotations

import math
import posixpath
import re
from collections import Counter
from dataclasses import dataclass, field

from unfold.core.ids import chunk_id, content_hash
from unfold.domain.enums import ContentType
from unfold.indexing.models import Chunk
from unfold.project_ir.schema import FileIR, ProjectIR, SymbolIR
from unfold.security import injection
from unfold.security.secrets import redact

WINDOW_LINES = 60
WINDOW_OVERLAP = 10
MAX_UNIT_LINES = 120
MIN_GAP_LINES = 3  # module-level code between symbols shorter than this (non-blank) is not chunked
MAX_MEMBERS_LISTED = 60

_DOC_EXT = {".md", ".mdx", ".markdown", ".rst", ".txt", ".adoc"}
_YAML_EXT = {".yml", ".yaml"}
_CONFIG_EXT = _YAML_EXT | {".toml", ".json", ".ini", ".cfg", ".conf", ".env", ".properties", ".tf", ".hcl"}
_INFRA_ROLES = {"container", "ci", "iac", "infra", "deployment"}
_CONFIG_ROLES = {"config", "manifest"}
_ROLE_BONUS = {
    "entrypoint": 0.15,
    "route": 0.15,
    "schema": 0.12,
    "model_def": 0.12,
    "migration": 0.02,
    "service": 0.1,
    "manifest": 0.08,
    "container": 0.08,
    "iac": 0.06,
    "ci": 0.04,
    "component": 0.05,
    "config": 0.03,
    "source": 0.0,
    "docs": 0.0,
    "test": -0.08,
}
_TIER_WEIGHT = {0: 1.0, 1: 0.8, 2: 0.55, 3: 0.35, 4: 0.1}
_KIND_BONUS = {"endpoint": 0.1, "schema": 0.08, "class": 0.04, "config": 0.02}

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_RST_UNDERLINE = re.compile(r"^([=\-~^\"'`#*+])\1{2,}\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_YAML_TOP_KEY = re.compile(r"""^(?P<key>[A-Za-z0-9_."'\-]+)\s*:(\s|$)""")
_TOML_TABLE = re.compile(r"^\s*\[\[?(?P<key>[^\]]+)\]\]?\s*$")
_TF_BLOCK = re.compile(
    r"^(?P<kind>resource|module|data|variable|output|provider|locals|terraform)\b(?P<rest>[^{]*)\{?"
)
_DOCKER_FROM = re.compile(r"^\s*FROM\s+(?P<image>\S+)(?:\s+AS\s+(?P<stage>\S+))?", re.I)


@dataclass
class _Unit:
    start: int  # 1-based inclusive
    end: int
    kind: str
    name: str  # qualname / section / key shown in the header
    symbol: SymbolIR | None = None
    node_ids: list[str] = field(default_factory=list)
    extra_text: str = ""  # appended synthetic text (class member list)


def _ext(path: str) -> str:
    base = posixpath.basename(path).lower()
    return "" if "." not in base else base[base.rindex(".") :]


def _is_dockerfile(path: str) -> bool:
    base = posixpath.basename(path).lower()
    return base == "dockerfile" or base.startswith("dockerfile.") or base.endswith(".dockerfile")


def _is_compose(path: str) -> bool:
    base = posixpath.basename(path).lower()
    return base.startswith(("docker-compose", "compose")) and _ext(path) in _YAML_EXT


def content_type_for(f: FileIR) -> ContentType:
    ext = _ext(f.path)
    role = (f.role or "").lower()
    if role == "test":
        return ContentType.TEST
    if role == "docs" or ext in _DOC_EXT:
        return ContentType.DOCS
    if role in _INFRA_ROLES or _is_dockerfile(f.path) or _is_compose(f.path) or ext in {".tf", ".hcl"}:
        return ContentType.INFRA
    if role in _CONFIG_ROLES or ext in _CONFIG_EXT:
        return ContentType.CONFIG
    return ContentType.SOURCE


def _importance(f: FileIR, kind: str, fan_in: int) -> float:
    base = 0.55 * _TIER_WEIGHT.get(f.tier, 0.5)
    fan = 0.25 * min(1.0, math.log1p(fan_in) / math.log1p(20))
    score = base + _ROLE_BONUS.get((f.role or "source").lower(), 0.0) + _KIND_BONUS.get(kind, 0.0) + fan
    return round(max(0.0, min(1.0, score)), 4)


def _fan_in(ir: ProjectIR) -> Counter[str]:
    sym_file = {s.id: s.file_id for s in ir.symbols}
    counts: Counter[str] = Counter()
    for r in ir.relationships:
        if r.type in ("IMPORTS", "CALLS") and r.source_id != r.target_id:
            counts[r.target_id] += 1
            if r.target_id in sym_file:
                counts[sym_file[r.target_id]] += 1
    return counts


# --- unit extraction per file type -------------------------------------------------------------------------------


def _windows(n_lines: int, start: int = 1, end: int | None = None) -> list[tuple[int, int]]:
    end = n_lines if end is None else end
    out: list[tuple[int, int]] = []
    s = start
    while s <= end:
        e = min(end, s + WINDOW_LINES - 1)
        out.append((s, e))
        if e >= end:
            break
        s = e - WINDOW_OVERLAP + 1
    return out


def _markdown_units(lines: list[str], rst: bool) -> list[_Unit]:
    heads: list[tuple[int, int, str]] = []  # (line, level, title)
    in_fence = False
    for i, line in enumerate(lines, start=1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _MD_HEADING.match(line)
        if m:
            heads.append((i, len(m.group(1)), m.group(2).strip()))
        elif (
            rst
            and i > 1
            and _RST_UNDERLINE.match(line)
            and lines[i - 2].strip()
            and len(line.strip()) >= len(lines[i - 2].strip())
        ):
            heads.append((i - 1, 1 if line.strip()[0] in "=#" else 2, lines[i - 2].strip()))
    units: list[_Unit] = []
    if not heads or heads[0][0] > 1:
        first = heads[0][0] - 1 if heads else len(lines)
        if any(x.strip() for x in lines[:first]):
            units.append(_Unit(1, first, "docs_section", "(preamble)"))
    stack: list[tuple[int, str]] = []
    for idx, (hline, level, title) in enumerate(heads):
        end = heads[idx + 1][0] - 1 if idx + 1 < len(heads) else len(lines)
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        units.append(_Unit(hline, max(hline, end), "docs_section", " > ".join(t for _, t in stack)))
    return units


def _yaml_units(lines: list[str], compose: bool) -> list[_Unit]:
    tops = [
        (i, m.group("key").strip("\"'"))
        for i, line in enumerate(lines, start=1)
        if (m := _YAML_TOP_KEY.match(line)) and not line.startswith((" ", "\t", "#", "-"))
    ]
    units: list[_Unit] = []
    for idx, (start, key) in enumerate(tops):
        end = tops[idx + 1][0] - 1 if idx + 1 < len(tops) else len(lines)
        while end > start and not lines[end - 1].strip():
            end -= 1
        if compose and key == "services":
            units.extend(_yaml_children(lines, start, end, "services"))
        else:
            units.append(_Unit(start, end, "config", key))
    if (
        tops
        and tops[0][0] > 1
        and any(x.strip() and not x.lstrip().startswith("#") for x in lines[: tops[0][0] - 1])
    ):
        units.insert(0, _Unit(1, tops[0][0] - 1, "config", "(preamble)"))
    return units


def _yaml_children(lines: list[str], start: int, end: int, parent: str) -> list[_Unit]:
    child_re = re.compile(r"^(?P<ind>[ ]{1,8})(?P<key>[A-Za-z0-9_.\"'\-]+)\s*:(\s|$)")
    indent: int | None = None
    kids: list[tuple[int, str]] = []
    for i in range(start + 1, end + 1):
        m = child_re.match(lines[i - 1])
        if not m:
            continue
        ind = len(m.group("ind"))
        if indent is None:
            indent = ind
        if ind == indent:
            kids.append((i, m.group("key").strip("\"'")))
    if not kids:
        return [_Unit(start, end, "config", parent)]
    units = []
    for idx, (s, key) in enumerate(kids):
        e = kids[idx + 1][0] - 1 if idx + 1 < len(kids) else end
        units.append(_Unit(s if idx else start, e, "config", f"{parent}.{key}"))
    return units


def _json_units(lines: list[str]) -> list[_Unit]:
    key_re = re.compile(r'^(?P<ind>\s*)"(?P<key>[^"]+)"\s*:')
    indent: int | None = None
    keys: list[tuple[int, str]] = []
    for i, line in enumerate(lines, start=1):
        m = key_re.match(line)
        if not m:
            continue
        ind = len(m.group("ind"))
        if indent is None:
            indent = ind
        if ind == indent:
            keys.append((i, m.group("key")))
    units = []
    for idx, (s, key) in enumerate(keys):
        e = keys[idx + 1][0] - 1 if idx + 1 < len(keys) else len(lines)
        units.append(_Unit(1 if idx == 0 else s, e, "config", key))
    return units


def _toml_units(lines: list[str]) -> list[_Unit]:
    tables = [
        (i, m.group("key").strip()) for i, line in enumerate(lines, start=1) if (m := _TOML_TABLE.match(line))
    ]
    units: list[_Unit] = []
    first = tables[0][0] if tables else len(lines) + 1
    if any(x.strip() and not x.lstrip().startswith("#") for x in lines[: first - 1]):
        units.append(_Unit(1, first - 1, "config", "(root)"))
    for idx, (s, key) in enumerate(tables):
        e = tables[idx + 1][0] - 1 if idx + 1 < len(tables) else len(lines)
        units.append(_Unit(s, e, "config", key))
    return units


def _terraform_units(lines: list[str]) -> list[_Unit]:
    blocks = []
    for i, line in enumerate(lines, start=1):
        m = _TF_BLOCK.match(line)
        if m:
            labels = re.findall(r'"([^"]+)"', m.group("rest"))
            blocks.append((i, ".".join([m.group("kind"), *labels])))
    units = []
    for idx, (s, key) in enumerate(blocks):
        e = blocks[idx + 1][0] - 1 if idx + 1 < len(blocks) else len(lines)
        units.append(_Unit(s, e, "config", key))
    return units


def _dockerfile_units(lines: list[str]) -> list[_Unit]:
    stages = []
    for i, line in enumerate(lines, start=1):
        m = _DOCKER_FROM.match(line)
        if m:
            stages.append((i, m.group("stage") or m.group("image")))
    if len(stages) <= 1:
        return [_Unit(1, len(lines), "config", f"stage {stages[0][1]}" if stages else "Dockerfile")]
    units = []
    for idx, (s, name) in enumerate(stages):
        e = stages[idx + 1][0] - 1 if idx + 1 < len(stages) else len(lines)
        units.append(_Unit(1 if idx == 0 else s, e, "config", f"stage {name}"))
    return units


def _config_units(f: FileIR, lines: list[str]) -> list[_Unit] | None:
    ext = _ext(f.path)
    if _is_dockerfile(f.path):
        return _dockerfile_units(lines)
    if ext in _YAML_EXT:
        return _yaml_units(lines, _is_compose(f.path))
    if ext == ".json":
        return _json_units(lines)
    if ext == ".toml":
        return _toml_units(lines)
    if ext in {".tf", ".hcl"}:
        return _terraform_units(lines)
    return None


def _symbol_units(
    ir_symbols: list[SymbolIR],
    n_lines: int,
    endpoint_by_handler: dict[str, list[str]],
    schema_by_symbol: dict[str, list[str]],
) -> list[_Unit]:
    by_id = {s.id: s for s in ir_symbols}
    children: dict[str, list[SymbolIR]] = {}
    for s in ir_symbols:
        if s.parent_id:
            children.setdefault(s.parent_id, []).append(s)
    units: list[_Unit] = []
    for s in sorted(ir_symbols, key=lambda x: (x.start_line or 0, x.qualname)):
        if s.kind == "variable" or not s.start_line or not s.end_line:
            continue
        parent = by_id.get(s.parent_id) if s.parent_id else None
        if parent is not None and parent.kind not in ("class", "interface"):
            continue  # nested function: stays in its parent's chunk
        start, end = max(1, s.start_line), min(n_lines, max(s.start_line, s.end_line))
        if start > n_lines:
            continue
        kind: str = s.kind if s.kind in ("function", "method", "class", "component") else "class"
        node_ids = [s.id]
        if s.id in endpoint_by_handler:
            kind = "endpoint"
            node_ids += endpoint_by_handler[s.id]
        if s.id in schema_by_symbol:
            kind = "schema" if kind != "endpoint" else kind
            node_ids += schema_by_symbol[s.id]
        members = sorted(
            (c for c in children.get(s.id, []) if c.kind in ("method", "function") and c.start_line),
            key=lambda c: c.start_line or 0,
        )
        extra = ""
        if s.kind in ("class", "interface") and members:
            first_member = members[0].start_line or end
            header_end = max(start, min(end, first_member - 1))
            listing = ", ".join((m.signature or m.name).strip()[:120] for m in members[:MAX_MEMBERS_LISTED])
            extra = f"# members: {listing}"
            if len(members) > MAX_MEMBERS_LISTED:
                extra += f" (+{len(members) - MAX_MEMBERS_LISTED} more)"
            end = header_end
        units.append(_Unit(start, end, kind, s.qualname, symbol=s, node_ids=node_ids, extra_text=extra))
    return units


def _gap_units(units: list[_Unit], lines: list[str]) -> list[_Unit]:
    covered = [False] * (len(lines) + 1)
    for u in units:
        lo = u.start
        # Class header units only cover their header; their members are covered by method units.
        for i in range(lo, min(len(lines), u.end) + 1):
            covered[i] = True
        if u.symbol is not None and u.symbol.end_line:
            for i in range(lo, min(len(lines), u.symbol.end_line) + 1):
                covered[i] = True
    gaps: list[_Unit] = []
    i = 1
    while i <= len(lines):
        if covered[i]:
            i += 1
            continue
        j = i
        while j + 1 <= len(lines) and not covered[j + 1]:
            j += 1
        if sum(1 for x in lines[i - 1 : j] if x.strip()) >= MIN_GAP_LINES:
            gaps.extend(_Unit(s, e, "window", f"lines {s}-{e}") for s, e in _windows(len(lines), i, j))
        i = j + 1
    return gaps


def _split_oversized(unit: _Unit) -> list[_Unit]:
    length = unit.end - unit.start + 1
    if length <= MAX_UNIT_LINES:
        return [unit]
    parts = math.ceil(length / MAX_UNIT_LINES)
    size = math.ceil(length / parts)
    out = []
    for p in range(parts):
        s = unit.start + p * size
        e = min(unit.end, s + size - 1)
        out.append(
            _Unit(
                s,
                e,
                unit.kind,
                f"{unit.name} (part {p + 1}/{parts})",
                symbol=unit.symbol,
                node_ids=unit.node_ids,
                extra_text=unit.extra_text if p == 0 else "",
            )
        )
    return out


# --- public API --------------------------------------------------------------------------------------------------


def chunk_file(
    ir: ProjectIR,
    f: FileIR,
    text: str,
    *,
    fan_in: Counter[str] | None = None,
    endpoint_by_handler: dict[str, list[str]] | None = None,
    schema_by_symbol: dict[str, list[str]] | None = None,
    symbols: list[SymbolIR] | None = None,
) -> list[Chunk]:
    if f.tier >= 4 or not f.is_text or not text.strip():
        return []
    lines = text.splitlines()
    n = len(lines)
    fan = fan_in if fan_in is not None else _fan_in(ir)
    eph = endpoint_by_handler or {}
    sbs = schema_by_symbol or {}
    file_symbols = symbols if symbols is not None else [s for s in ir.symbols if s.file_id == f.id]
    ext = _ext(f.path)

    units: list[_Unit] = []
    if file_symbols:
        units = _symbol_units(file_symbols, n, eph, sbs)
        if units:
            units += _gap_units(units, lines)
    if not units:
        if ext in _DOC_EXT - {".txt"}:
            units = _markdown_units(lines, rst=ext == ".rst")
        else:
            units = _config_units(f, lines) or []
    if not units:
        units = [_Unit(s, e, "window", f"lines {s}-{e}") for s, e in _windows(n)]
    # Endpoints / schemas defined without a symbol (e.g. SQL, Prisma, route tables) get their own chunks.
    sym_ids = {s.id for s in file_symbols}
    for e in ir.endpoints:
        if e.path == f.path and e.start_line and not (e.handler_id and e.handler_id in sym_ids):
            units.append(
                _Unit(
                    e.start_line,
                    min(n, e.end_line or e.start_line),
                    "endpoint",
                    f"{e.method} {e.route}",
                    node_ids=[e.id],
                )
            )
    for sc in ir.schemas:
        if sc.path == f.path and sc.start_line and not (sc.symbol_id and sc.symbol_id in sym_ids):
            units.append(
                _Unit(
                    sc.start_line, min(n, sc.end_line or sc.start_line), "schema", sc.name, node_ids=[sc.id]
                )
            )

    ctype = content_type_for(f)
    chunks: list[Chunk] = []
    seen: set[str] = set()
    for unit in sorted(
        (p for u in units for p in _split_oversized(u)), key=lambda u: (u.start, u.end, u.kind)
    ):
        if unit.start < 1 or unit.start > n or unit.end < unit.start:
            continue
        body = "\n".join(lines[unit.start - 1 : unit.end])
        if unit.extra_text:
            body = f"{body}\n{unit.extra_text}"
        if not body.strip():
            continue
        cid = chunk_id(ir.project.project_id, f.path, unit.start, unit.end, unit.kind)
        if cid in seen:
            continue
        seen.add(cid)
        red = redact(body)
        signals = injection.scan(red.text)
        chunk_text = f"{f.path} :: {unit.name}\n{red.text}"
        sym = unit.symbol
        fan_count = max(fan.get(sym.id, 0) if sym else 0, fan.get(f.id, 0) // 2)
        chunks.append(
            Chunk(
                id=cid,
                project_id=ir.project.project_id,
                path=f.path,
                start_line=unit.start,
                end_line=unit.end,
                kind=unit.kind,
                symbol_id=sym.id if sym else (unit.node_ids[0] if unit.node_ids else None),
                symbol_name=sym.qualname if sym else (unit.name if unit.kind != "window" else None),
                language=f.language,
                content_type=ctype,
                importance=_importance(f, unit.kind, fan_count),
                graph_node_ids=list(dict.fromkeys([*unit.node_ids, f.id])),
                trust={
                    "label": injection.trust_label(signals),
                    "signals": sorted({s.rule for s in signals}),
                    "redactions": len(red.findings),
                },
                text=chunk_text,
                content_hash=content_hash(chunk_text),
            )
        )
    return chunks


def chunk_project(ir: ProjectIR, file_texts: dict[str, str]) -> list[Chunk]:
    """Chunks for every indexable file of the IR whose text is available (`path → text`)."""
    fan = _fan_in(ir)
    endpoint_by_handler: dict[str, list[str]] = {}
    for e in ir.endpoints:
        if e.handler_id:
            endpoint_by_handler.setdefault(e.handler_id, []).append(e.id)
    schema_by_symbol: dict[str, list[str]] = {}
    for sc in ir.schemas:
        if sc.symbol_id:
            schema_by_symbol.setdefault(sc.symbol_id, []).append(sc.id)
    symbols_by_file: dict[str, list[SymbolIR]] = {}
    for s in ir.symbols:
        symbols_by_file.setdefault(s.file_id, []).append(s)

    out: list[Chunk] = []
    seen: set[str] = set()
    for f in sorted(ir.files, key=lambda x: x.path):
        text = file_texts.get(f.path)
        if text is None:
            continue
        for ch in chunk_file(
            ir,
            f,
            text,
            fan_in=fan,
            endpoint_by_handler=endpoint_by_handler,
            schema_by_symbol=schema_by_symbol,
            symbols=symbols_by_file.get(f.id, []),
        ):
            if ch.id not in seen:
                seen.add(ch.id)
                out.append(ch)
    return out
