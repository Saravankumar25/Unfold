"""Context Compiler (LLD §12, ESD §14, AI infra §25-§26). Pure and deterministic.

Priority (highest first) — each tier is filled only with what still fits the token budget:

1. objective, instructions, reader profile, retrieval notes (mandatory)
2. verified facts
3. parent summaries (project → subsystem → module)
4. evidence — primary retrieved items in rerank order
5. graph paths
6. source — graph-expansion evidence
7. supplemental — file / symbol summaries not used as substitutes

Over-budget policy: duplicates (chunk id / content hash / text hash) are dropped first; evidence that does not
fit is substituted by a covering parent summary when one exists (or fits); otherwise the lowest-priority item is
truncated (line boundary, marker) when enough room is left, else dropped. Labels E1..En are assigned to kept
evidence only, and `label_map` / `evidence_refs()` reflect exactly what was rendered.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from unfold.context.budget import TokenBudget, estimate_tokens, truncate_to_tokens
from unfold.context.models import (
    EVIDENCE_PREAMBLE,
    PARENT_LEVELS,
    CompiledContext,
    DroppedItem,
    EvidenceItem,
    SummaryItem,
    evidence_block,
    instructions_block,
    notes_block,
    objective_block,
    path_line,
    profile_block,
    summary_block,
    summary_level_rank,
)
from unfold.core.logging import get_logger
from unfold.domain.models import SessionProfile
from unfold.retrieval.models import GraphPath, RetrievalResult, RetrievedItem
from unfold.security.secrets import redact_text

log = get_logger(__name__)

MIN_TRUNCATED_EVIDENCE_TOKENS = 120  # below this a truncated snippet is not worth its header
SECTION_OVERHEAD_TOKENS = 12  # "## Heading" + fence tags of one section
MAX_FALLBACK_CHARS = 80


def _hash(text: str) -> str:
    return hashlib.sha1(" ".join(text.split()).encode("utf-8"), usedforsecurity=False).hexdigest()


def _profile(profile: SessionProfile | Mapping[str, Any] | None) -> SessionProfile:
    if profile is None:
        return SessionProfile()
    if isinstance(profile, SessionProfile):
        return profile
    return SessionProfile.model_validate(dict(profile))


def _notes(retrieval: RetrievalResult | None) -> list[str]:
    if retrieval is None:
        return []
    notes: list[str] = []
    if not retrieval.sufficient:
        suff = retrieval.trace.sufficiency
        gaps = [
            *[str(x) for x in suff.get("missing_identifiers") or []],
            *[str(x) for x in suff.get("missing_paths") or []],
        ]
        missing_types = [str(x) for x in suff.get("missing_evidence_types") or []]
        note = "Retrieved evidence may be incomplete for this objective"
        if gaps:
            note += f"; not found: {', '.join(gaps[:6])}"
        if missing_types:
            note += f"; no {'/'.join(missing_types[:4])} evidence"
        notes.append(note + ". State uncertainty instead of guessing.")
    if retrieval.trace.degraded:
        notes.append(f"Degraded retrieval components: {', '.join(retrieval.trace.degraded)}.")
    return notes


def _path_sig(p: GraphPath) -> tuple[str, ...]:
    return tuple(str(n.get("uid") or n.get("name") or "") for n in p.nodes)


def _to_evidence(item: RetrievedItem, label: str, text: str, truncated: bool) -> EvidenceItem:
    c = item.chunk
    ev = EvidenceItem(
        label=label,
        chunk_id=c.id,
        path=c.path,
        start_line=c.start_line,
        end_line=c.end_line,
        symbol=c.symbol_name,
        kind=c.kind,
        content_type=c.content_type.value,
        score=item.score,
        rerank_score=item.rerank_score,
        via_expansion=item.via_expansion,
        trust=str(c.trust.get("label") or "untrusted_source"),
        text=text,
        truncated=truncated,
    )
    ev.tokens = estimate_tokens(evidence_block(ev))
    return ev


class _Compiler:
    def __init__(self, budget_tokens: int) -> None:
        self.budget = TokenBudget(max(0, budget_tokens))
        self.dropped: list[DroppedItem] = []
        self.sections_opened: set[str] = set()

    def _fit(self, section: str, tokens: int) -> bool:
        overhead = 0 if section in self.sections_opened else SECTION_OVERHEAD_TOKENS
        if self.budget.take(tokens + overhead):
            self.sections_opened.add(section)
            return True
        return False

    def _drop(self, kind: Any, ref: str, reason: Any, tokens: int = 0, by: str | None = None) -> None:
        self.dropped.append(DroppedItem(kind=kind, ref=ref, reason=reason, tokens=tokens, by=by))

    # tiers ------------------------------------------------------------------------------------------------

    def facts(self, facts: Sequence[str]) -> list[str]:
        kept: list[str] = []
        seen: set[str] = set()
        for raw in facts:
            fact = redact_text(" ".join(raw.split()))
            if not fact:
                continue
            h = _hash(fact)
            if h in seen:
                self._drop("fact", fact[:MAX_FALLBACK_CHARS], "duplicate")
                continue
            seen.add(h)
            tokens = estimate_tokens(f"- {fact}\n")
            if self._fit("facts", tokens):
                kept.append(fact)
            else:
                self._drop("fact", fact[:MAX_FALLBACK_CHARS], "budget", tokens)
        return kept

    def summary(self, s: SummaryItem, kept: list[SummaryItem]) -> bool:
        tokens = estimate_tokens(summary_block(s))
        if self._fit("summaries", tokens):
            kept.append(s)
            return True
        return False

    def evidence(
        self,
        items: Sequence[RetrievedItem],
        start_label: int,
        seen_ids: set[str],
        seen_hashes: set[str],
        kept_summaries: list[SummaryItem],
        spare_summaries: list[SummaryItem],
    ) -> list[EvidenceItem]:
        kept: list[EvidenceItem] = []
        for item in items:
            c = item.chunk
            text = redact_text(c.text)
            h = c.content_hash or _hash(text)
            if c.id in seen_ids or h in seen_hashes or _hash(text) in seen_hashes:
                self._drop("evidence", c.id, "duplicate")
                continue
            seen_ids.add(c.id)
            seen_hashes.update({h, _hash(text)})
            label = f"E{start_label + len(kept)}"
            ev = _to_evidence(item, label, text, truncated=False)
            if self._fit("evidence", ev.tokens):
                kept.append(ev)
                continue
            # 1) a parent summary already in context covers this file → drop, no new tokens
            covering = next((s for s in kept_summaries if s.covers(c.path)), None)
            if covering is not None:
                self._drop("evidence", c.id, "covered", ev.tokens, by=f"{covering.level}:{covering.key}")
                continue
            # 2) substitute with a (cheaper) summary covering this file, if one fits
            sub = next((s for s in spare_summaries if s.covers(c.path)), None)
            if (
                sub is not None
                and estimate_tokens(summary_block(sub)) < ev.tokens
                and self.summary(sub, kept_summaries)
            ):
                spare_summaries.remove(sub)
                self._drop("evidence", c.id, "substituted", ev.tokens, by=f"{sub.level}:{sub.key}")
                continue
            # 3) truncate to the remaining room when it is still worth it
            header_tokens = estimate_tokens(evidence_block(ev.model_copy(update={"text": ""})))
            overhead = 0 if "evidence" in self.sections_opened else SECTION_OVERHEAD_TOKENS
            room = self.budget.remaining - header_tokens - overhead
            if room >= MIN_TRUNCATED_EVIDENCE_TOKENS:
                cut = truncate_to_tokens(text, room)
                if cut:
                    short = _to_evidence(item, label, cut, truncated=True)
                    if self._fit("evidence", short.tokens):
                        kept.append(short)
                        continue
            self._drop("evidence", c.id, "budget", ev.tokens)
        return kept

    def graph_paths(self, paths: Sequence[GraphPath]) -> list[GraphPath]:
        kept: list[GraphPath] = []
        seen: set[tuple[str, ...]] = set()
        for p in paths:
            sig = _path_sig(p)
            if sig in seen or tuple(reversed(sig)) in seen:
                self._drop("graph_path", " > ".join(sig)[:MAX_FALLBACK_CHARS], "duplicate")
                continue
            seen.add(sig)
            clean = GraphPath(
                nodes=[
                    {k: (redact_text(str(v)) if isinstance(v, str) else v) for k, v in n.items()}
                    for n in p.nodes
                ],
                relationships=list(p.relationships),
            )
            tokens = estimate_tokens(path_line(len(kept) + 1, clean) + "\n")
            if self._fit("graph_paths", tokens):
                kept.append(clean)
            else:
                self._drop("graph_path", " > ".join(sig)[:MAX_FALLBACK_CHARS], "budget", tokens)
        return kept


def _clean_summary(s: SummaryItem) -> SummaryItem:
    return s.model_copy(update={"title": redact_text(s.title), "text": redact_text(s.text)})


def compile_context(
    objective: str,
    retrieval: RetrievalResult | None,
    *,
    summaries: Sequence[SummaryItem] = (),
    graph_paths: Sequence[GraphPath] | None = None,
    facts: Sequence[str] = (),
    profile: SessionProfile | Mapping[str, Any] | None = None,
    budget_tokens: int,
    instructions: str | None = None,
) -> CompiledContext:
    """Assemble a budgeted, fenced, citation-ready context (see module docstring for the policy).

    `graph_paths` are added to `retrieval.graph_paths` (duplicates removed). Summaries are de-duplicated by
    (level, key) and by text.
    """
    prof = _profile(profile)
    comp = _Compiler(budget_tokens)
    notes = _notes(retrieval)

    # 1. mandatory
    for block in (
        objective_block(objective),
        instructions_block(instructions),
        profile_block(prof),
        notes_block(notes),
    ):
        comp.budget.force(estimate_tokens(block))

    # 2. verified facts
    kept_facts = comp.facts(facts)

    # summaries: dedupe, split parents / supplemental
    uniq: list[SummaryItem] = []
    seen_keys: set[tuple[str, str]] = set()
    seen_text: set[str] = set()
    for s in sorted(summaries, key=lambda s: summary_level_rank(s.level)):  # stable within a level
        clean = _clean_summary(s)
        th = _hash(clean.text)
        if (s.level, s.key) in seen_keys or th in seen_text or not clean.text.strip():
            comp._drop("summary", f"{s.level}:{s.key}", "duplicate")
            continue
        seen_keys.add((s.level, s.key))
        seen_text.add(th)
        uniq.append(clean)
    parents = [s for s in uniq if s.level in PARENT_LEVELS]
    spare = [s for s in uniq if s.level not in PARENT_LEVELS]

    # 3. parent summaries
    kept_summaries: list[SummaryItem] = []
    for s in parents:
        if not comp.summary(s, kept_summaries):
            comp._drop("summary", f"{s.level}:{s.key}", "budget", estimate_tokens(summary_block(s)))

    # 4. primary evidence, 5. graph paths, 6. expansion evidence
    items = list(retrieval.items) if retrieval is not None else []
    primary = [i for i in items if not i.via_expansion]
    expansion = [i for i in items if i.via_expansion]
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    evidence = comp.evidence(primary, 1, seen_ids, seen_hashes, kept_summaries, spare)
    all_paths = [*(retrieval.graph_paths if retrieval is not None else []), *(graph_paths or [])]
    kept_paths = comp.graph_paths(all_paths)
    evidence += comp.evidence(expansion, len(evidence) + 1, seen_ids, seen_hashes, kept_summaries, spare)

    # 7. supplemental summaries
    for s in spare:
        if not comp.summary(s, kept_summaries):
            comp._drop("summary", f"{s.level}:{s.key}", "budget", estimate_tokens(summary_block(s)))

    ctx = CompiledContext(
        objective=objective.strip(),
        instructions=instructions.strip() if instructions and instructions.strip() else None,
        profile=prof,
        facts=kept_facts,
        summaries=kept_summaries,
        evidence=evidence,
        graph_paths=kept_paths,
        uncertainties=notes,
        budget_tokens=budget_tokens,
        dropped=comp.dropped,
        sufficient=retrieval.sufficient if retrieval is not None else None,
        degraded=list(retrieval.trace.degraded) if retrieval is not None else [],
    )
    _finalize(ctx)
    # Section overhead is estimated during selection; enforce the budget on the exact rendering.
    while ctx.token_count_estimate > budget_tokens and _drop_lowest(ctx):
        _finalize(ctx)
    if ctx.token_count_estimate > budget_tokens:
        log.warning(
            "context.over_budget",
            budget=budget_tokens,
            tokens=ctx.token_count_estimate,
            reason="mandatory sections exceed the budget",
        )
    return ctx


def _relabel(ctx: CompiledContext) -> None:
    for i, e in enumerate(ctx.evidence, 1):
        if e.label != f"E{i}":
            e.label = f"E{i}"
            e.tokens = estimate_tokens(evidence_block(e))
    ctx.label_map = {e.label: e.chunk_id for e in ctx.evidence}


def _finalize(ctx: CompiledContext) -> None:
    _relabel(ctx)
    ctx.token_counts = {name: estimate_tokens(text) for name, text in ctx.sections()}
    ctx.token_count_estimate = estimate_tokens(ctx.render())
    ctx.token_counts["total"] = ctx.token_count_estimate


def _drop_lowest(ctx: CompiledContext) -> bool:
    """Remove the single lowest-priority optional item. False when only mandatory content is left.

    Lowest first: supplemental summaries → expansion evidence → graph paths → primary evidence →
    parent summaries (deepest level first) → facts.
    """
    supplemental = [s for s in ctx.summaries if s.level not in PARENT_LEVELS]
    if supplemental:
        s = supplemental[-1]
        ctx.summaries.remove(s)
        ctx.dropped.append(DroppedItem(kind="summary", ref=f"{s.level}:{s.key}", reason="budget"))
        return True
    expansion = [e for e in ctx.evidence if e.via_expansion]
    primary = [e for e in ctx.evidence if not e.via_expansion]
    for group in (expansion, None, primary):
        if group is None:
            if ctx.graph_paths:
                p = ctx.graph_paths.pop()
                sig = " > ".join(_path_sig(p))[:MAX_FALLBACK_CHARS]
                ctx.dropped.append(DroppedItem(kind="graph_path", ref=sig, reason="budget"))
                return True
            continue
        if group:
            e = group[-1]
            ctx.evidence.remove(e)
            ctx.dropped.append(DroppedItem(kind="evidence", ref=e.chunk_id, reason="budget", tokens=e.tokens))
            return True
    parents = [s for s in ctx.summaries if s.level in PARENT_LEVELS]
    if parents:
        s = max(reversed(parents), key=lambda x: summary_level_rank(x.level))
        ctx.summaries.remove(s)
        ctx.dropped.append(DroppedItem(kind="summary", ref=f"{s.level}:{s.key}", reason="budget"))
        return True
    if ctx.facts:
        f = ctx.facts.pop()
        ctx.dropped.append(DroppedItem(kind="fact", ref=f[:MAX_FALLBACK_CHARS], reason="budget"))
        return True
    return False


__all__ = ["EVIDENCE_PREAMBLE", "compile_context"]
