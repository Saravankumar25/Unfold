"""Deterministic guide verification (investigation engine §5 Verifier, task 3.7). No model, no I/O.

Every location a chapter points at is resolved against the snapshot and the `RepoMap`:

- `path_exists`, `line_in_bounds`: `[E#]` labels, `path:L12-L40` mentions and code-block paths;
- `symbol_exists`: the symbol attached to cited evidence is in the `RepoMap` (files the analyzers found no
  symbols in, such as docs and config, are exempt);
- `excerpt_matches`: every quoted line of a code block is a line of the cited file (whitespace-normalised;
  dropped lines are allowed);
- `edge_exists`: a claim naming two graph nodes needs an edge between them with `proof != inferred`;
- `claim_supported`: `grade_claims` left a project fact UNSUPPORTED or INFERRED.

A block id is `b<index>` into `content.blocks`. The same primitives back `evaluation.rubric` so the verifier and
the offline scorer agree on what a resolved citation is.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from typing import TYPE_CHECKING

from unfold.core.errors import GenerationError
from unfold.domain.enums import ClaimKind, ClaimStatus, Proof
from unfold.guide.models import (
    Block,
    BulletsBlock,
    CalloutBlock,
    CodeBlock,
    GlossaryBlock,
    HeadingBlock,
    ParagraphBlock,
    QABlock,
    SectionContent,
    TableBlock,
)
from unfold.guide.verification_models import (
    MAX_REVISION_ROUNDS,
    SEMANTIC_DIMENSIONS,
    CheckName,
    CheckResult,
    SemanticCritique,
    Verdict,
    VerificationResult,
)

if TYPE_CHECKING:
    from unfold.domain.models import ClaimRecord
    from unfold.indexing.models import Chunk
    from unfold.understand.repomap import RepoMap

LABEL = re.compile(r"\[(E\d+)\]")
PATHLINE = re.compile(r"(?<![\w/.-])([\w./-]+\.(\w{1,6})):L?(\d+)(?:-L?(\d+))?")
_CODE_EXT = frozenset(
    {"py", "js", "ts", "tsx", "jsx", "go", "rs", "java", "rb", "php", "cs", "c", "cc", "cpp", "h", "hpp"}
    | {"tf", "yml", "yaml", "json", "toml", "md", "sql", "sh", "kt", "swift"}
)
_WORD = re.compile(r"[a-z][a-z0-9]{2,}")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")  # getDb -> get Db, so it meets `get_db`
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_STOP = frozenset(
    {
        "the",
        "and",
        "with",
        "for",
        "that",
        "this",
        "are",
        "its",
        "from",
        "into",
        "using",
        "uses",
        "used",
        "has",
    }
    | {"have", "can", "will", "not", "but", "all", "any", "each", "which", "when", "where", "then", "than"}
    | {"also", "only", "over", "under", "here", "there", "their", "they", "them", "was", "were", "been"}
)
CLAIM_MATCH = 0.45  # Dice overlap a sentence needs with a claim to be the one that made it


# --- primitives shared with evaluation.rubric --------------------------------------------------------------------


def norm(s: str) -> str:
    return " ".join(s.split())


def in_bounds(start: int | None, end: int | None, total: int | None) -> bool:
    return total is not None and start is not None and end is not None and 1 <= start <= end <= total


def code_end_line(code: str, start: int) -> int:
    return start + max(len(code.strip("\n").splitlines()), 1) - 1


def quoted_lines_present(code: str, have: Collection[str]) -> bool:
    """Every non-blank quoted line (whitespace-normalised) is in `have`; at least one line is quoted."""
    quoted = [norm(x) for x in code.splitlines() if x.strip()]
    return bool(quoted) and all(q in have for q in quoted)


def path_line_mentions(text: str, known: Collection[str]) -> list[tuple[str, int, int]]:
    """`path:12-40` mentions that are checkable: the path exists, or it looks like a repository code path."""
    out: list[tuple[str, int, int]] = []
    for path, ext, a, b in PATHLINE.findall(text):
        if path in known or ("/" in path and ext in _CODE_EXT):
            out.append((path, int(a), int(b or a)))
    return out


def block_texts(block: Block) -> list[str]:
    """Prose strings of a block (code excluded)."""
    if isinstance(block, HeadingBlock | ParagraphBlock | CalloutBlock):
        return [block.text]
    if isinstance(block, BulletsBlock):
        return list(block.items)
    if isinstance(block, TableBlock):
        return [*block.headers, *(cell for row in block.rows for cell in row)]
    if isinstance(block, QABlock):
        return [block.question, block.answer]
    if isinstance(block, GlossaryBlock):
        return [f"{g.term}: {g.definition}" for g in block.items]
    return []


def text_from_chunks(chunks: Sequence[Chunk], lines: int) -> str:
    """A file's text as its chunks hold it: one entry per file line, blank where no chunk covers. Chunk text is
    a header line then the body, which starts at `start_line`, so line counts and quoted excerpts can be checked
    against the snapshot a guide was written from (the original files are never kept)."""
    out = [""] * max(lines, 1)
    for c in chunks:
        body = c.text.split("\n", 1)[1] if "\n" in c.text else ""
        for off, line in enumerate(body.splitlines()):
            if 0 <= c.start_line - 1 + off < len(out):
                out[c.start_line - 1 + off] = line
    return "\n".join(out) + "\n"


def block_id(index: int) -> str:
    return f"b{index}"


def block_index(bid: str, n_blocks: int) -> int:
    if bid[:1] == "b" and bid[1:].isdigit() and int(bid[1:]) < n_blocks:
        return int(bid[1:])
    raise GenerationError(f"Unknown block id '{bid}' (chapter has {n_blocks} blocks).")


# --- verdict ---------------------------------------------------------------------------------------------------


def decide_verdict(
    deterministic: Sequence[CheckResult], critique_blocks: Sequence[str], round_no: int, cap: int
) -> Verdict:
    """`round_no` revisions have been made so far. Deterministic failures never stand as fact: after the cap they
    are qualified or removed (`accept_with_gaps`). A critique alone only asks for revision, never for gaps."""
    failed = [r for r in deterministic if not r.ok]
    if failed:
        return "revise" if round_no < cap and any(r.block for r in failed) else "accept_with_gaps"
    return "revise" if critique_blocks and round_no < cap else "accept"


def finalize(
    deterministic: list[CheckResult],
    claims: Sequence[ClaimRecord],
    semantic: SemanticCritique | None,
    round_no: int,
    cap: int,
) -> VerificationResult:
    critique_blocks = semantic.failing_blocks if semantic else []
    failing = {r.block for r in deterministic if not r.ok and r.block} | set(critique_blocks)
    return VerificationResult(
        deterministic=deterministic,
        claims=list(claims),
        semantic={d: float(getattr(semantic, d)) for d in SEMANTIC_DIMENSIONS} if semantic else None,
        failing_blocks=sorted(failing, key=lambda b: int(b[1:])),
        verdict=decide_verdict(deterministic, critique_blocks, round_no, cap),
    )


def with_critique(
    result: VerificationResult, critique: SemanticCritique, round_no: int, cap: int = MAX_REVISION_ROUNDS
) -> VerificationResult:
    """Merge a validated critique (`critique.validate_critique`) into a deterministic result."""
    return finalize(result.deterministic, result.claims, critique, round_no, cap)


# --- checks ----------------------------------------------------------------------------------------------------


def _stem(w: str) -> str:
    """Crude on purpose: `creates`, `created` and `create_app` must meet in `creat`."""
    for suffix in ("ing", "ed", "es", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            w = w[: -len(suffix)]
            break
    return w[:-1] if w.endswith("e") and len(w) > 3 else w


def words(text: str) -> set[str]:
    """Content words of a text, stemmed, identifiers split at underscores and case changes; what claims and
    sentences are matched on."""
    return {_stem(w) for w in _WORD.findall(_CAMEL.sub(" ", text).lower().replace("_", " "))} - _STOP


def best_unit(claim: str, units: Sequence[str]) -> tuple[int, float]:
    """Index and Dice overlap (2|A&B| / (|A|+|B|) over stemmed content words) of the unit closest to `claim`;
    a paraphrase that adds a few words still lands on the sentence it restates."""
    want = words(claim)
    scores = [2 * len(want & w) / (len(want) + len(w)) if want and w else 0.0 for w in map(words, units)]
    best = max(range(len(units)), key=lambda i: scores[i], default=-1)
    return (best, scores[best]) if best >= 0 else (-1, 0.0)


def sentences(block: Block) -> list[str]:
    """The units a claim can have been made in: sentences of prose, list items, table cells."""
    return [u for t in block_texts(block) for u in _SENTENCE.split(t.strip()) if u]


def place_claims(content: SectionContent, texts: Sequence[str]) -> dict[str, str | None]:
    """The block each claim text was made in: the one with the sentence that restates it (`CLAIM_MATCH`), else
    None. The same rule `verify_section` uses to say where an unsupported claim sits."""
    units = [(f"b{i}", u) for i, block in enumerate(content.blocks) for u in sentences(block)]
    out: dict[str, str | None] = {}
    for text in texts:
        i, score = best_unit(text, [u for _, u in units])
        out[text] = units[i][0] if i >= 0 and score >= CLAIM_MATCH else None
    return out


def _mentions(text: str, name: str) -> bool:
    return bool(name) and re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text) is not None


def cited_paths(content: SectionContent) -> set[str]:
    """Paths whose text `verify_section` needs in `snapshot_files` (code-block paths: excerpts are the only check
    that reads file text)."""
    return {b.path for b in content.blocks if isinstance(b, CodeBlock) and b.path}


def verify_section(
    content: SectionContent,
    claims: Sequence[ClaimRecord],
    evidence: Mapping[str, Mapping[str, object]],
    snapshot_files: Mapping[str, str],
    repomap: RepoMap,
    *,
    round_no: int = 0,
    cap: int = MAX_REVISION_ROUNDS,
) -> VerificationResult:
    """`snapshot_files`: path -> text for at least `cited_paths(content)`; other paths are resolved from
    the `RepoMap` (existence and line counts). `claims` are `grade_claims` output; a claim with exactly two
    `graph_node_ids` is an edge claim (source, target)."""
    total = {r.path: r.end_line for r in repomap.files()}
    total.update({p: max(len(t.splitlines()), 1) for p, t in snapshot_files.items()})
    sym_paths = {s.path for s in repomap.ir.symbols}
    results: list[CheckResult] = []
    texts: dict[str, str] = {}

    def add(check: CheckName, ok: bool, subject: str, block: str | None, detail: str | None = None) -> None:
        results.append(CheckResult(check=check, ok=ok, subject=subject, block=block, detail=detail))

    def locate(bid: str, path: str, start: int | None, end: int | None, symbol: str | None = None) -> None:
        subject = f"{path}:{start}-{end}" if start is not None else path
        found = path in total
        add("path_exists", found, subject, bid, None if found else "path is not in the snapshot")
        if not found:
            return
        if start is not None:
            add(
                "line_in_bounds",
                in_bounds(start, end, total[path]),
                subject,
                bid,
                f"file has {total[path]} lines",
            )
        if symbol and path in sym_paths:
            add("symbol_exists", bool(repomap.find(symbol)), symbol, bid, f"cited at {path}")

    for i, block in enumerate(content.blocks):
        bid = block_id(i)
        prose = block_texts(block)
        texts[bid] = "\n".join(prose)
        done: set[tuple[str, int | None, int | None]] = set()
        for label in dict.fromkeys(m for t in prose for m in LABEL.findall(t)):
            ev = evidence.get(label)
            if ev is None:
                add("path_exists", False, label, bid, "evidence label is not defined for this chapter")
                continue
            s, e, sym = ev.get("start_line"), ev.get("end_line"), ev.get("symbol")
            locate(
                bid,
                str(ev.get("path", "")),
                s if isinstance(s, int) else None,
                e if isinstance(e, int) else None,
                sym if isinstance(sym, str) else None,
            )
        for path, a, b in path_line_mentions("\n".join(prose), total):
            if (path, a, b) not in done:
                done.add((path, a, b))
                locate(bid, path, a, b)
        if isinstance(block, CodeBlock) and block.path:
            start = block.start_line
            end = code_end_line(block.code, start) if start is not None else None
            locate(bid, block.path, start, end)
            if block.path in total:
                text = snapshot_files.get(block.path)
                if text is None:
                    raise GenerationError(f"verify_section needs the text of '{block.path}' for its excerpt.")
                ok = quoted_lines_present(block.code, {norm(x) for x in text.splitlines()})
                add("excerpt_matches", ok, block.path, bid, None if ok else "quoted code is not in the file")

    def place_edge(src: str, dst: str) -> str | None:
        names = [n for n in (_short(repomap, src), _short(repomap, dst)) if n]
        hits = [b for b, t in texts.items() if len(names) == 2 and all(_mentions(t, n) for n in names)]
        return hits[0] if hits else None

    placed = place_claims(content, [c.text for c in claims])

    for claim in claims:
        if len(claim.graph_node_ids) == 2:
            src, dst = claim.graph_node_ids
            na, nb = repomap.symbol(src), repomap.symbol(dst)
            if na is None or nb is None:
                ok, detail = False, "a claimed node is not in the repository map"
            else:
                rels = [e for e in repomap.out_edges(na.id) if e.dst == nb.id]
                ok = any(e.proof is not Proof.INFERRED for e in rels)
                detail = (
                    "edge present" if ok else "only inferred, never shown as fact" if rels else "no such edge"
                )
            add("edge_exists", ok, f"{src} -> {dst}", place_edge(src, dst), detail)
        if claim.kind is ClaimKind.PROJECT_FACT and claim.status in (
            ClaimStatus.UNSUPPORTED,
            ClaimStatus.INFERRED,
        ):
            add("claim_supported", False, claim.text[:120], placed[claim.text], claim.note)
    return finalize(results, claims, None, round_no, cap)


def _short(repomap: RepoMap, entity_id: str) -> str:
    ref = repomap.symbol(entity_id)
    return ref.name.rsplit(".", 1)[-1] if ref else ""
