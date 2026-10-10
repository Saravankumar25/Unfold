# Investigation engine: from "documentation about a repo" to "guide that teaches the repo"

Status: accepted 2026-10-10 (ADR 0017). Owner phase: **3** (build), 4 (run on AWS), 5 (regress and harden), 6 (verify live).
This is a design for the existing pipeline, not a rewrite. Everything marked *exists* was verified in source on `main`
at `accfc01`; everything marked *new* is unbuilt.

## 1. What exists (verified) and what is missing

Pipeline today (`orchestration/pipeline.py:_Run.execute`):
`acquire -> analyze (repo_intel + project_ir) -> graph + lexical + dense index -> subsystems -> plan -> sections -> verify -> visuals`.

| Capability | State | Evidence |
|---|---|---|
| File inventory, language, ignore rules, secrets scan | exists | `repo_intel/inventory.py`, `rules.py`, `detect.py` |
| Symbols, imports, routes, models, services, queues, data stores | exists | `repo_intel/analyzers/*` (Tree-sitter + Python AST), `project_ir/schema.py` |
| Graph with `IMPORTS`/`CALLS` and traversal | exists | `graph/models.py:REL_TYPES`, `graph/builder.py`, `graph/queries.py` |
| Hybrid retrieval: BM25 + dense + structural, RRF, <=2 follow-up rounds, <=6-chunk graph expansion | exists | `retrieval/hybrid.py`, `sufficiency.py` |
| Context compiler: redaction, fencing, budget, evidence labels | exists | `context/compiler.py` |
| Guide planner | thin | `guide/generate.py:plan_guide`: one LLM call over ~10 one-line facts, 3-10 sections, 3 fixed backbone sections |
| Chapter research | **absent** | `write_section` builds one query string and one retrieval, then one LLM call (`generate.py:292-325`) |
| Claim verification | partial | `grade_claims`: identifier-in-cited-chunk check only; no path/line/relationship/excerpt check; no revision |
| Revision loop | **absent** | `_verify` grades and marks `DEGRADED`; nothing is rewritten |
| Chapter regeneration, input hashing | **absent** | `_plan` deletes every `GuideSectionRow` on each run; no input hash; no per-chapter route |
| Citations in reading flow | unverified | evidence labels `[E3]` are stored per section; rendering is a frontend question for task 3.8 |

## 2. Limitations: confirmed vs hypothesis

| # | Claim in the brief | Verdict | Basis |
|---|---|---|---|
| A | Shallow understanding | **Partly confirmed.** Structure exists in IR + graph, but the writer sees 1-line facts and one retrieval. | `generate.py:136-172, 292` |
| B | No repo-wide map | **Not confirmed as stated.** The map exists as `ProjectIR` + graph. Gap: it is not exposed as symbol lookup, flow tracing or an architecture model with proof labels. | `project_ir/schema.py`, `graph/queries.py` |
| C | Generic planning | **Confirmed.** No dependencies, research questions, targets or depth contract. | `plan_guide`, `GuideSectionPlan` |
| D | No research before writing | **Confirmed.** | `write_section` |
| E | Superficial chapters | **Hypothesis.** Needs the baseline (task 3.1) and the depth scorer. | none yet |
| F | Weak verification | **Partly confirmed.** Deterministic identifier check exists; nothing checks paths, line ranges, edges or quoted excerpts. | `grade_claims` |
| G | Poor evidence presentation | **Hypothesis.** Check frontend and export rendering in 3.8. | none yet |
| H | Inefficient regeneration | **Confirmed** for chapters; stage-level and section-level *resume* already works. | `_plan` delete, `_sections` skip |
| I | Infrastructure excess | **Owned by phase 4** (`docs/infra/`); the engine adds no store. | ADR 0013 (phase 4) |

## 3. Decisions (the brief's section 11, answered)

1. **Deterministic vs LLM.** Deterministic: inventory, symbols, edges, entry points, flow traces, chapter *targets*,
   all path/line/symbol/excerpt/edge checks, hashing, budgets. LLM only: naming and ordering chapters, decomposing a
   chapter objective into questions, writing prose, one semantic critique (coverage, depth, coherence).
2. **Graph database.** No new store. Keep the existing `GraphBuilder`/`GraphQueries` behind the port; phase 4 decides
   Neo4j vs in-memory (ADR 0013). The engine reads only `RepoMap` (derived from IR + graph) so either backend works.
3. **Hybrid vs symbol-aware retrieval.** Keep hybrid (it exists and is tested). Add symbol lookup and flow tracing as
   *additional* sources; prove value with the retrieval-hit and depth evals, do not assume it.
4. **Cross-file following without context blow-up.** The investigator never concatenates files. It collects
   `Finding`s (claim + evidence ref + proof), ranks by relevance, and gives the writer a brief capped at
   `RESEARCH_BRIEF_TOKENS`. Excerpts are fetched by ref at write time.
5. **Provenance.** `EvidenceRecord` = `{id, path, start_line, end_line, commit_sha, content_hash, symbol?}`. Created at
   chunking, carried unchanged through brief, claim, citation and export. The commit SHA makes links permanent.
6. **When to investigate more.** A question is *open* when its coverage < threshold: no finding with proof >= heuristic,
   or the retrieval sufficiency check fails, or a flow trace ends at an unresolved edge. Bounded by per-chapter caps.
7. **Cheap, reliable verification.** Deterministic gates run first and for free. One LLM critique per chapter, only on
   dimensions that need judgment; its output is a typed list of failing block ids, never a rewrite.
8. **Dependencies and invalidation.** `input_hash = H(plan_entry, evidence content_hashes, prompt versions, level)`.
   Equal hash => reuse. Chapter `depends_on` is for ordering and reader links only; it never triggers regeneration.
   **Not built** (see ADR 0017 as-built): nothing can reach it, because a project has one analysis job.
9. **Keep / simplify / remove.** Keep everything in section 1 marked *exists*. Remove nothing in phase 3. Phase 4
   may drop Qdrant/Neo4j only behind the retrieval evals. No new database, queue or always-on service.
10. **Smallest architecture with a major improvement.** Five additions in `unfold`: `understand/` (map, flows,
    architecture), `guide/plan` v2, `guide/research`, `guide/verify` + `guide/revise`, `evaluation/rubric`.

## 4. Data contracts

Pydantic models in the existing style (`extra="forbid"`, frozen where possible). Persisted as JSON on existing rows or one
additive table; no destructive migration.

```python
class Proof(StrEnum):            # how an edge or fact is known
    STATIC = "static"            # parser saw it (import, decorator route, class def)
    HEURISTIC = "heuristic"      # name-resolution match, framework convention
    INFERRED = "inferred"        # LLM or retrieval proximity only; never drawn as a diagram edge

class SymbolRef(BaseModel):  id: str; name: str; kind: str; path: str; start_line: int; end_line: int
class Edge(BaseModel):       src: str; dst: str; rel: str; proof: Proof   # rel in graph.models.REL_TYPES (IMPORTS, CALLS, HANDLES, READS_FROM, ...); proof is new on graph edges (task 3.2)
class FlowStep(BaseModel):   symbol: SymbolRef; via: Edge | None; note: str | None
class FlowTrace(BaseModel):  entry: SymbolRef; steps: list[FlowStep]; truncated: bool; unresolved: list[str]

class ArchitectureModel(BaseModel):
    project_kind: Literal["cli","library","web_service","pipeline","app","monorepo","unknown"]
    components: list[Component]          # id, title, member_paths, responsibility (deterministic), community
    entry_points: list[SymbolRef]
    flows: list[FlowTrace]               # request, job, event; each step proof-labelled
    persistence: list[Boundary]          # store, access symbols
    external: list[Dependency]
    uncertainty: list[str]               # e.g. "dynamic dispatch in x.py", "unsupported language: kt"

class ChapterPlan(BaseModel):            # replaces GuideSectionPlan (v2; v1 rows still load)
    key: str; title: str; objective: str
    questions: list[str]                 # 2-6, answerable from code
    targets: list[str]                   # SymbolRef.id | path | component id; all must exist in RepoMap
    depends_on: list[str]                # chapter keys, acyclic
    expected_evidence: list[Literal["definition","flow","config","test","route","model"]]
    depth: Literal["overview","standard","deep"]
    visual_needs: list[str]; target_minutes: float

class Finding(BaseModel):    question_id: int; statement: str; evidence: list[str]; proof: Proof
class ResearchBrief(BaseModel):
    chapter_key: str; findings: list[Finding]; flows: list[FlowTrace]; tests: list[str]
    open_questions: list[str]; coverage: float; spent: Budget   # rounds, retrievals, llm_calls, tokens

class CheckResult(BaseModel):  check: str; ok: bool; subject: str; detail: str | None
class VerificationResult(BaseModel):
    deterministic: list[CheckResult]     # path_exists, line_in_bounds, symbol_exists, excerpt_matches, edge_exists
    claims: list[ClaimRecord]            # existing grade_claims output, extended
    semantic: dict[str, float] | None    # coverage, depth, coherence, specificity, redundancy (0-1)
    failing_blocks: list[str]; verdict: Literal["accept","revise","accept_with_gaps"]
```

Job state reuses `JobStage` (`domain/enums.py`). Existing stages are reused: architecture discovery runs under
`INVESTIGATING_PROJECT`, then `PLANNING_GUIDE` (v2), `GENERATING_GUIDE` (research + write per chapter), `VERIFYING_GUIDE`.
Two stages are added: `RESEARCHING_GUIDE` (before writing) and `REVISING_GUIDE` (after verify). Each writes a
`stage_progress` checkpoint like the stages that exist.

Storage: `ArchitectureModel` -> IR snapshot blob (exists); `ChapterPlan` -> `GuidePlanRow.plan`; `ResearchBrief`,
`VerificationResult`, `input_hash` -> additive columns on `GuideSectionRow` (`research`, `verification`, `input_hash`).

## 5. Stage behavior

**Architecture discovery (deterministic).** Components = existing graph communities + IR services; entry points =
IR services/endpoints/`__main__`/CLI decorators/workers; flows = `trace_flow` from each entry, depth <= 6, fan-out <= 4,
stops at unresolved edges and records them in `unfold.uncertainty`. A diagram edge is drawn only when `proof != INFERRED`.

**Adaptive planner.** Deterministic skeleton first: project kind selects backbone (a CLI gets *overview, usage,
commands, internals*; a web service gets *overview, request lifecycle, persistence, jobs, config/deploy*). Chapter count
scales with `complexity` (files, components, flows), clamped 4-14; no chapter without >= 1 valid target. One LLM call
refines titles, objectives and questions inside that skeleton. Validation rejects unknown targets and cycles and falls
back to the skeleton (typed degradation, recorded).

**Investigator** (`guide/research.py`), per chapter, plain bounded loop, no agent framework (ADR 0001):
1. questions from the plan (LLM decomposition only when the plan has < 2);
2. per question: symbol lookup for targets -> hybrid retrieve (existing sufficiency/graph expansion) -> flow trace for
   entry-point targets -> test lookup (paths matching target symbols);
3. record `Finding`s with proof; compute coverage = answered / asked;
4. if coverage < 0.7 and budget remains, one targeted extra round using each open question's missing facet;
5. stop at caps. Defaults: 2 rounds, 12 retrievals, 4 LLM calls, `RESEARCH_BRIEF_TOKENS=6000` per chapter, all under the
   per-job LLM budget already enforced by the gateway.

**Writer.** Input is brief + excerpts-by-ref + compact facts. Output keeps the existing `SectionDraft` blocks so
the frontend and exports are unchanged; adds required "what can go wrong" and "how to change it" blocks when the
brief has matching findings. Chapters that depend on others receive their one-line summaries only.

**Verifier.** Deterministic first: every cited path exists in the snapshot, line range within file length, symbol exists
in `RepoMap`, quoted code is a substring (whitespace-normalised) of the file, claimed edge exists with proof >=
heuristic. Then existing `grade_claims`. Then one semantic critique. Verdict:
`accept`; `revise` (failing blocks listed); `accept_with_gaps` after the cap, with unsupported statements removed or
prefixed "Not confirmed in the code:".

**Reviser.** <= 2 rounds. Only failing blocks are rewritten, each with one targeted retrieval for its missing evidence.
Stops early when verdict is `accept` or no score improves.

**Publish.** Citations are `path:L12-L40` linking to `https://github.com/<owner>/<repo>/blob/<sha>/<path>#L12-L40`
(ZIP uploads: link to the in-app source viewer). Labels `[E3]` never reach the reader; evidence appendix is a separate
view. PDF/Markdown exports read the stored canonical content only.

## 6. Incremental processing (scoped honestly)

- Phase 3 delivers: `POST /projects/{id}/sections/{key}/regenerate`, resume at stage and
  chapter granularity, results tied to `commit_sha`.
- Not in phase 3: cross-revision reuse of parsed files by content hash and partial graph rebuilds. Recorded as a known
  limitation; correctness over cleverness. Revisit only if phase 5 benchmarks show re-analysis dominating cost.

## 7. Evaluation rubric (task 3.9, gates in phase 5, checked live in phase 6)

Each dimension scores 0-4. Automated scorers are deterministic and run in CI on stored guides; judged scores are
recorded from a live run with a human spot-check of >= 3 chapters per repo.

| Dimension | 0 | 2 | 4 | Scorer |
|---|---|---|---|---|
| Coverage | key components unmentioned | most components, flows missing | every component and entry-point flow explained | auto: share of IR components/entry points named in the guide |
| Groundedness | claims uncited | most cited, some unchecked | every claim cited and verified | auto: `grade_claims` verified ratio, unsupported count |
| Citation accuracy | links dead or wrong lines | paths right, lines loose | path + line + excerpt match | auto: resolve rate of cited path/line/excerpt vs snapshot |
| Depth | names things | explains what | explains how, order, state, errors | auto proxy: flows traced + error/edge blocks present; judged |
| Specificity | generic advice | mixed | only this repo | auto: share of sentences naming a repo symbol; judged |
| Redundancy | large repeated parts | some repeated | none | auto: n-gram overlap across chapters |
| Readability | hard to follow | ok | clear sequence | judged |
| Uncertainty honesty | invents | silent gaps | gaps stated | auto: gaps recorded vs unsupported statements left |

Gate rule: no dimension may fall more than 0.3 below the recorded baseline; citation accuracy >= 0.98 and zero
unsupported statements presented as fact are absolute. Baseline numbers are produced by task 3.1; none are assumed here.
Length is reported but never scored.

## 8. Risks

| Risk | Mitigation |
|---|---|
| LLM cost rises with research calls | Deterministic first; caps; per-job budget; cost per chapter logged and compared to baseline |
| Call graph wrong for dynamic code | `proof` labels, `unfold.uncertainty`, no diagram edge for INFERRED |
| Planner overfits one repo kind | Skeleton per kind; three fixture repos in evals; unsupported language path tested |
| Verification false negatives | Excerpt check is whitespace-normalised; semantic critique can only request revision |
| Scope creep into Phase 4 files | Engine uses ports and `RepoMap` only; no store or SDK import |
