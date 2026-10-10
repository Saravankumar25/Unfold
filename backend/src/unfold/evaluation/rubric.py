"""Guide quality rubric (design `investigation-engine.md` §7, task 3.9): deterministic scorers, no model, no network.

Each scorer is a pure function over a stored guide (`GuideSection` JSON), an `IRSummary` and the texts of the evidence
chunks, and returns `Score`: 0-4 plus the raw numbers it came from. Readability and judged depth are human fields in
the review sheet (`review_sheet`), never code. Length is reported, never scored.

    python -m unfold.evaluation.rubric <guide_dir>... [--out scores.json]      (poe eval-guide)

A guide directory holds `guide.json` ({"sections": [GuideSection...]}), `ir.json` (an `IRSummary`) and `chunks.json`
({chunk_id: {"path", "start_line", "end_line", "text"}}), exactly what `unfold.evaluation.baseline` writes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from unfold.domain.enums import ClaimKind, ClaimStatus
from unfold.guide.models import (
    BulletsBlock,
    CalloutBlock,
    CodeBlock,
    GlossaryBlock,
    GuideSection,
    HeadingBlock,
    ParagraphBlock,
    QABlock,
    TableBlock,
)
from unfold.project_ir.schema import ProjectIR

MAX_SCORE = 4.0
SHINGLE = 5  # words per n-gram for the redundancy check
_CODE_EXT = frozenset(
    {"py", "js", "ts", "tsx", "jsx", "go", "rs", "java", "rb", "php", "cs", "c", "cc", "cpp", "h", "hpp"}
    | {"tf", "yml", "yaml", "json", "toml", "md", "sql", "sh", "kt", "swift"}
)
_LABEL = re.compile(r"\[(E\d+)\]")
_PATHLINE = re.compile(r"(?<![\w/.-])([\w./-]+\.(\w{1,6})):(\d+)(?:-(\d+))?")
_SEQUENCE = re.compile(r"\b(first|then|next|after|before|finally|once|step|subsequently)\b", re.I)
_ERRORS = re.compile(
    r"\b(errors?|exceptions?|raises?|fails?|failures?|timeouts?|retry|retries|invalid|fallbacks?|degrad\w+)\b",
    re.I,
)
_STATE = re.compile(
    r"\b(state|persist\w*|stored?|caches?|cached|database|queue[sd]?|sessions?|writes?|reads?)\b", re.I
)
_GAPS = re.compile(
    r"(not (covered|shown|visible|present|documented|found in)|no evidence|cannot be determined|unclear|unknown|"
    r"not clear|could not (find|verify|determine)|evidence does not|not enough evidence|unverified)",
    re.I,
)
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TICKS = re.compile(r"`([^`\n]+)`")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


class Named(BaseModel):
    name: str
    kind: str
    aliases: list[str] = Field(default_factory=list)  # other spellings that count as naming it


class IRSummary(BaseModel):
    """What the scorers need from the IR snapshot, small enough to commit next to a stored guide."""

    project: str = ""
    commit_sha: str | None = None
    files: dict[str, int] = Field(default_factory=dict)  # path -> line count
    symbols: list[str] = Field(default_factory=list)
    components: list[Named] = Field(default_factory=list)
    entry_points: list[Named] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


class Score(BaseModel):
    score: float  # 0..4
    raw: dict[str, Any]


def _score(ratio: float, raw: dict[str, Any]) -> Score:
    return Score(score=round(MAX_SCORE * max(0.0, min(1.0, ratio)), 2), raw=raw)


def summarize_ir(ir: ProjectIR) -> IRSummary:
    """Components: services, data stores, queues and non-test modules. Entry points: endpoints and service entry files."""
    paths = {f.id: f.path for f in ir.files}
    symbols = {s.id: s for s in ir.symbols}
    comps: dict[str, Named] = {}
    for kind, items in (("service", ir.services), ("datastore", ir.data_stores), ("queue", ir.queues)):
        for e in items:
            comps.setdefault(e.name.lower(), Named(name=e.name, kind=kind))
    for m in ir.modules:
        if not m.path or re.search(r"(^|/)(tests?|migrations?|docs?)(/|$)", m.path):
            continue
        comps.setdefault(m.name.lower(), Named(name=m.path, kind="module", aliases=[m.name]))
    eps: list[Named] = []
    for ep in ir.endpoints:
        handler = symbols.get(ep.handler_id or "")
        eps.append(
            Named(
                name=f"{ep.method} {ep.route}",
                kind="endpoint",
                aliases=[ep.route, *([handler.name] if handler else [])],
            )
        )
    for svc in ir.services:
        if svc.entrypoint_file_id in paths:
            eps.append(Named(name=paths[svc.entrypoint_file_id], kind="entrypoint_file"))
    names = {n for s in ir.symbols for n in (s.name, *s.qualname.split("."))}
    return IRSummary(
        project=ir.project.name,
        commit_sha=ir.project.commit_sha,
        files={f.path: f.lines for f in ir.files},
        symbols=sorted(n for n in names if n),
        components=list(comps.values()),
        entry_points=eps,
        counts={k: int(v) for k, v in ir.stats.get("entities", {}).items()},
    )


# --- text extraction -----------------------------------------------------------------------------------------


def _units(sec: GuideSection) -> Iterator[str]:
    """Every prose string of a chapter (code blocks excluded)."""
    if sec.content is None:
        return
    yield sec.content.summary
    for b in sec.content.blocks:
        if isinstance(b, HeadingBlock | ParagraphBlock | CalloutBlock):
            yield b.text
        elif isinstance(b, BulletsBlock):
            yield from b.items
        elif isinstance(b, TableBlock):
            yield from b.headers
            for row in b.rows:
                yield from row
        elif isinstance(b, QABlock):
            yield b.question
            yield b.answer
        elif isinstance(b, GlossaryBlock):
            for g in b.items:
                yield f"{g.term}: {g.definition}"


def _codes(sec: GuideSection) -> list[CodeBlock]:
    return [b for b in sec.content.blocks if isinstance(b, CodeBlock)] if sec.content else []


def _prose(sec: GuideSection) -> str:
    return "\n".join(_units(sec))


def _chapters(sections: Sequence[GuideSection]) -> list[GuideSection]:
    return [s for s in sections if s.content is not None]


def _mentions(text: str, alias: str) -> bool:
    if not alias:
        return False
    pre = r"(?<!\w)" if re.match(r"\w", alias) else ""  # "/documents" also names "/api/v1/documents"
    post = r"(?!\w)" if re.search(r"\w$", alias) else ""
    return re.search(f"{pre}{re.escape(alias)}{post}", text, re.I) is not None


def _named(text: str, items: Sequence[Named]) -> int:
    return sum(1 for it in items if any(_mentions(text, a) for a in (it.name, *it.aliases)))


def _guide_text(sections: Sequence[GuideSection]) -> str:
    parts: list[str] = []
    for s in _chapters(sections):
        parts.append(_prose(s))
        parts.extend(f"{c.path or ''} {c.code}" for c in _codes(s))
        parts.extend(c.text for c in s.claims)
    return "\n".join(parts)


class _Lexicon:
    """Repo identifiers a sentence can name. Plain prose words only count when they look like identifiers."""

    def __init__(self, ir: IRSummary) -> None:
        self.paths = set(ir.files)
        self.base = {p.rsplit("/", 1)[-1] for p in ir.files}
        self.dirs = {p.rsplit("/", i)[0] for p in ir.files for i in range(1, p.count("/") + 1)}
        self.symbols = set(ir.symbols)
        self.distinct = {s for s in self.symbols if len(s) >= 5 and ("_" in s or re.search(r"[a-z][A-Z]", s))}

    def names(self, sentence: str) -> set[str]:
        out: set[str] = set()
        for tick in _TICKS.findall(sentence):
            t = tick.strip().rstrip("()")
            if t in self.paths or t in self.base or t in self.symbols or t.rstrip("/") in self.dirs:
                out.add(t)
                continue
            out |= {p for p in re.split(r"[.:/\s]+", t) if p and (p in self.symbols or p in self.base)}
        plain = _TICKS.sub(" ", sentence)
        out |= {t for t in _TOKEN.findall(plain) if t in self.distinct}
        out |= {w for w in re.findall(r"[\w./-]+\.\w{1,6}", plain) if w in self.paths or w in self.base}
        out |= {d for w in re.findall(r"[\w.-]+(?:/[\w.-]+)+", plain) if (d := w.rstrip("/")) in self.dirs}
        return out


def _sentences(sec: GuideSection) -> list[str]:
    out: list[str] = []
    headings = {b.text for b in sec.content.blocks if isinstance(b, HeadingBlock)} if sec.content else set()
    for u in _units(sec):
        if u in headings:  # a title is not a statement
            continue
        clean = _LABEL.sub("", u).replace("**", "").replace("*", "")
        out += [s.strip() for s in _SENTENCE.split(clean) if len(s.split()) >= 4]
    return out


# --- scorers -------------------------------------------------------------------------------------------------


def coverage(sections: Sequence[GuideSection], ir: IRSummary) -> Score:
    text = _guide_text(sections)
    comps = [c for c in ir.components if c.name]  # the repo-root module has no name to mention
    items = [*comps, *ir.entry_points]
    hit = _named(text, items)
    raw = {
        "components": len(comps),
        "components_named": _named(text, comps),
        "entry_points": len(ir.entry_points),
        "entry_points_named": _named(text, ir.entry_points),
    }
    return _score(
        hit / len(items) if items else 0.0, raw | {"share": round(hit / len(items), 4) if items else None}
    )


def groundedness(sections: Sequence[GuideSection]) -> Score:
    """Verified share of the claims made about the project (inferences and suggestions are reported, not graded)."""
    claims = [c for s in sections for c in s.claims]
    facts = [c for c in claims if c.kind is ClaimKind.PROJECT_FACT]
    verified = sum(1 for c in facts if c.status is ClaimStatus.VERIFIED)
    raw = {
        "claims": len(claims),
        "project_facts": len(facts),
        "verified": verified,
        "unsupported": sum(1 for c in facts if c.status is ClaimStatus.UNSUPPORTED),
        "uncited": sum(1 for c in facts if not c.evidence_ids),
        "inferences": sum(1 for c in claims if c.kind is ClaimKind.INFERENCE),
        "verified_ratio": round(verified / len(facts), 4) if facts else None,
    }
    return _score(verified / len(facts) if facts else 0.0, raw)


def _norm(s: str) -> str:
    return " ".join(s.split())


def _line_ok(start: int | None, end: int | None, path: str, files: Mapping[str, int]) -> bool:
    return path in files and start is not None and end is not None and 1 <= start <= end <= files[path]


def citation_accuracy(
    sections: Sequence[GuideSection], ir: IRSummary, chunks: Mapping[str, Mapping[str, Any]]
) -> Score:
    """Resolve rate of everything a chapter points at: `[E#]` labels, `path:line` mentions, quoted code excerpts.

    A quoted excerpt passes when its path exists, its lines fit the file and every quoted line is in the cited chunks.
    """
    total = path_ok = line_ok = excerpts = excerpt_ok = 0
    passed = 0
    for s in _chapters(sections):
        seen: set[tuple[str, int, int]] = set()
        for label in dict.fromkeys(m for u in _units(s) for m in _LABEL.findall(u)):
            total += 1
            ev = s.evidence.get(label)
            path = str(ev.get("path", "")) if ev else ""
            start, end = (ev.get("start_line"), ev.get("end_line")) if ev else (None, None)
            p = path in ir.files
            ln = _line_ok(
                start if isinstance(start, int) else None,
                end if isinstance(end, int) else None,
                path,
                ir.files,
            )
            path_ok += p
            line_ok += ln
            passed += p and ln
        for u in _units(s):
            for path, ext, a, b in _PATHLINE.findall(u):
                if path not in ir.files and not ("/" in path and ext in _CODE_EXT):
                    continue
                key = (path, int(a), int(b or a))
                if key in seen:
                    continue
                seen.add(key)
                total += 1
                p = path in ir.files
                ln = _line_ok(key[1], key[2], path, ir.files)
                path_ok += p
                line_ok += ln
                passed += p and ln
        for cb in _codes(s):
            if not cb.path:
                continue
            total += 1
            excerpts += 1
            p = cb.path in ir.files
            n = cb.code.count("\n") + 1
            ln = p and (
                cb.start_line is None or _line_ok(cb.start_line, cb.start_line + n - 1, cb.path, ir.files)
            )
            # Every quoted line must exist in the cited file's chunks; dropped lines (elision) are allowed.
            have = {
                _norm(ln_)
                for c in chunks.values()
                if c.get("path") == cb.path
                for ln_ in str(c.get("text", "")).splitlines()
            }
            quoted = [_norm(x) for x in cb.code.splitlines() if x.strip()]
            ex = bool(p and quoted and all(q in have for q in quoted))
            path_ok += p
            line_ok += ln
            excerpt_ok += ex
            passed += p and ln and ex
    raw = {
        "citations": total,
        "path_ok": path_ok,
        "line_ok": line_ok,
        "excerpts": excerpts,
        "excerpt_ok": excerpt_ok,
        "resolve_rate": round(passed / total, 4) if total else None,
    }
    return _score(passed / total if total else 0.0, raw)


def depth_proxy(sections: Sequence[GuideSection], ir: IRSummary) -> Score:
    """Per chapter: traced flow (sequence + >=2 repo names), error/edge handling, quoted code, state, all repo-grounded."""
    lex = _Lexicon(ir)
    chapters = _chapters(sections)
    totals = {"flow": 0, "error_edge": 0, "code": 0, "state": 0}
    for s in chapters:
        text = _prose(s)
        names = lex.names(text) | {c.path for c in _codes(s) if c.path}
        ordered = (
            any(isinstance(b, BulletsBlock) and b.ordered for b in s.content.blocks) if s.content else False
        )
        totals["flow"] += (ordered or len(_SEQUENCE.findall(text)) >= 2) and len(names) >= 2
        warn = (
            any(isinstance(b, CalloutBlock) and b.tone == "warning" for b in s.content.blocks)
            if s.content
            else False
        )
        totals["error_edge"] += (warn or bool(_ERRORS.search(text))) and len(names) >= 1
        totals["code"] += bool(_codes(s))
        totals["state"] += bool(_STATE.search(text)) and len(names) >= 1
    n = len(chapters)
    rates = {k: round(v / n, 4) if n else None for k, v in totals.items()}
    ratio = sum(totals.values()) / (4 * n) if n else 0.0
    return _score(ratio, {"chapters": n, "indicator_rates": rates, "judged_depth": "human review sheet"})


def specificity(sections: Sequence[GuideSection], ir: IRSummary) -> Score:
    lex = _Lexicon(ir)
    sents = [x for s in _chapters(sections) for x in _sentences(s)]
    hit = sum(1 for x in sents if lex.names(x))
    return _score(hit / len(sents) if sents else 0.0, {"sentences": len(sents), "naming_a_symbol": hit})


def _shingles(sec: GuideSection) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9_]+", _LABEL.sub(" ", _prose(sec)).lower())
    return {tuple(words[i : i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)}


def redundancy(sections: Sequence[GuideSection]) -> Score:
    """Score 4 = no chapter repeats another. Per chapter: share of its word 5-grams found in any other chapter."""
    sets = [_shingles(s) for s in _chapters(sections)]
    shares: list[float] = []
    for i, mine in enumerate(sets):
        if not mine:
            continue
        others = set().union(*(o for j, o in enumerate(sets) if j != i)) if len(sets) > 1 else set()
        shares.append(len(mine & others) / len(mine))
    mean = sum(shares) / len(shares) if shares else 0.0
    return _score(
        1.0 - mean,
        {
            "chapters": len(sets),
            "mean_repeated_share": round(mean, 4),
            "max_repeated_share": round(max(shares, default=0.0), 4),
        },
    )


def uncertainty_honesty(sections: Sequence[GuideSection]) -> Score:
    """Gaps stated vs project facts left standing without verification. No unverified facts scores 4."""
    left = sum(
        1
        for s in sections
        for c in s.claims
        if c.kind is ClaimKind.PROJECT_FACT
        and c.status in (ClaimStatus.UNSUPPORTED, ClaimStatus.INFERRED, ClaimStatus.UNKNOWN)
    )
    stated = sum(len(_GAPS.findall(_prose(s))) for s in _chapters(sections))
    ratio = 1.0 if left == 0 else stated / (stated + left)
    return _score(ratio, {"unverified_facts_left": left, "gaps_stated": stated})


def length(sections: Sequence[GuideSection]) -> dict[str, Any]:
    ch = _chapters(sections)
    return {
        "chapters": len(ch),
        "chapters_failed": len(sections) - len(ch),
        "words": sum(len(_prose(s).split()) for s in ch),
        "claims": sum(len(s.claims) for s in sections),
        "code_blocks": sum(len(_codes(s)) for s in ch),
    }


def score_guide(
    sections: Sequence[GuideSection], ir: IRSummary, chunks: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    dims = {
        "coverage": coverage(sections, ir),
        "groundedness": groundedness(sections),
        "citation_accuracy": citation_accuracy(sections, ir, chunks),
        "depth_proxy": depth_proxy(sections, ir),
        "specificity": specificity(sections, ir),
        "redundancy": redundancy(sections),
        "uncertainty_honesty": uncertainty_honesty(sections),
    }
    return {
        "scores": {k: v.score for k, v in dims.items()},
        "raw": {k: v.raw for k, v in dims.items()},
        "length": length(sections),
        "human": "readability and judged depth/specificity: fill review-sheet.json",
    }


def review_sheet(sections: Sequence[GuideSection]) -> dict[str, Any]:
    """Human fields (0-4). Review >= 3 chapters per repo; leave the rest null."""
    return {
        "reviewer": None,
        "date": None,
        "chapters": [
            {
                "key": s.key,
                "title": s.title,
                "readability": None,
                "judged_depth": None,
                "judged_specificity": None,
                "notes": "",
            }
            for s in _chapters(sections)
        ],
    }


# --- CLI -----------------------------------------------------------------------------------------------------


def load_dir(path: Path) -> tuple[list[GuideSection], IRSummary, dict[str, dict[str, Any]]]:
    guide = json.loads((path / "guide.json").read_text(encoding="utf-8"))
    sections = [GuideSection.model_validate(s) for s in guide["sections"]]
    ir = IRSummary.model_validate_json((path / "ir.json").read_text(encoding="utf-8"))
    chunks: dict[str, dict[str, Any]] = json.loads((path / "chunks.json").read_text(encoding="utf-8"))
    return sections, ir, chunks


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="unfold.evaluation.rubric")
    parser.add_argument("dirs", nargs="+", type=Path, help="stored guide directories")
    parser.add_argument("--out", type=Path, default=Path("evals/reports/guide-scores.json"))
    args = parser.parse_args(argv)
    report: dict[str, Any] = {}
    for d in args.dirs:
        sections, ir, chunks = load_dir(d)
        report[d.name] = score_guide(sections, ir, chunks)
        sheet = d / "review-sheet.json"
        if not sheet.exists():  # never overwrite a reviewer's answers
            sheet.write_text(json.dumps(review_sheet(sections), indent=2) + "\n", encoding="utf-8")
        print(f"[{d.name}] " + " ".join(f"{k}={v}" for k, v in report[d.name]["scores"].items()))  # noqa: T201
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
