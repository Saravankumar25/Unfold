# Unfold — AI Infrastructure

> **Caption:** Prepare your project.  
> **Document status:** Draft v1.0  
> **Purpose:** Detailed design of Unfold’s AI, retrieval, graph, context, agent, model-routing, and evaluation infrastructure  
> **Core constraint:** Free-tier LLM APIs only in V1

---

# 1. AI Infrastructure Thesis

Unfold should not be designed as “send repository to LLM.”

The AI system is a layered intelligence architecture:

```text
Repository
   ↓
Deterministic code intelligence
   ↓
Project IR
   ↓
Neo4j project knowledge graph
   +
Native Hybrid RAG indexes
   +
Hierarchical summaries
   ↓
Context Compiler
   ↓
LangGraph orchestration
   +
Deep Agents
   ↓
Gemini/Groq model gateway
   ↓
Evidence verification
   ↓
Project Guide / Chat
```

The most important components are:

1. **Project understanding**
2. **Graph engineering**
3. **Retrieval quality**
4. **Context engineering**
5. **Agent execution**
6. **Model routing under free-tier constraints**
7. **Evaluation**

LLM choice is only one layer.

---

# 2. AI Goals

The AI stack must:

- work across many project types,
- preserve factual grounding,
- understand large repositories hierarchically,
- exploit code relationships rather than only text similarity,
- support broad project explanation,
- support precise deep-dive chat,
- remain robust when one provider is rate-limited,
- avoid expensive external embeddings,
- evaluate every critical AI subsystem,
- remain debuggable.

---

# 3. AI Components

```text
Repository Intelligence
Project IR
Graph Builder
Subsystem Discovery
Hierarchical Summarizer
Lexical Indexer
Dense Indexer
Retrieval Planner
Hybrid Retriever
Graph Retriever
Cross-Encoder Reranker
Context Sufficiency Evaluator
Context Compiler
LLM Gateway
LangGraph Workflow
Deep Agents
Guide Planner
Guide Generator
Claim Extractor
Evidence Verifier
Chat Agent
Evaluation Engine
```

---

# 4. Model Strategy

## 4.1 Providers

V1 external LLM providers:
- Gemini
- Groq

Credentials:
- 4 Gemini API keys
- 4 Groq API keys

The architecture must **not assume** that four keys mean four independent quota pools.

Provider limits can be enforced at project or organization level.

Therefore:

```text
Key = credential
Provider budget = capacity
```

Keys are primarily used for:
- credential redundancy,
- health isolation,
- replacement,
- failover if one credential is invalid/revoked.

Capacity is scheduled at provider/model level.

---

# 5. Capability-Based Model Routing

Application code requests a capability, not a hard-coded vendor model.

```python
class ModelCapability(str, Enum):
    FAST = "fast"
    BALANCED = "balanced"
    REASONING = "reasoning"
    LONG_CONTEXT = "long_context"
    STRUCTURED = "structured"
    EVALUATOR = "evaluator"
```

Example mapping is configuration-driven:

```yaml
capabilities:
  fast:
    - provider: groq
      model: ${GROQ_FAST_MODEL}
    - provider: gemini
      model: ${GEMINI_FAST_MODEL}

  reasoning:
    - provider: gemini
      model: ${GEMINI_REASONING_MODEL}
    - provider: groq
      model: ${GROQ_REASONING_MODEL}
```

Do not lock documents/code to a temporary preview model.

---

# 6. Provider Routing Policy

Routing factors:

1. capability,
2. model availability,
3. context length,
4. rate-limit headroom,
5. recent error rate,
6. expected latency,
7. structured-output reliability,
8. task priority,
9. provider cooldown.

Pseudo-flow:

```text
Request
  ↓
Capability Filter
  ↓
Available Model Candidates
  ↓
Provider Budget Check
  ↓
Circuit Breaker Check
  ↓
Key Health Check
  ↓
Select Provider/Model/Key
  ↓
Execute
  ↓
Validate
  ↓
Retry / fallback / queue
```

---

# 7. Key Pool

Each provider has a key pool.

```python
class KeyState(BaseModel):
    key_id: str
    enabled: bool
    healthy: bool
    consecutive_failures: int
    cooldown_until: datetime | None
    last_success: datetime | None
    last_error: str | None
```

Keys themselves are stored in environment/secret management and are never written to logs or database rows.

Selection:
- healthy keys only,
- round-robin or least-recently-used,
- no selection logic intended to bypass a shared provider quota.

---

# 8. Provider Budget Manager

The budget manager is more important than key rotation.

Track:
- RPM,
- TPM,
- RPD where applicable,
- concurrent requests,
- recent 429 responses,
- retry-after,
- provider health.

Model:

```python
class ProviderBudget:
    async def reserve(self, request: UsageEstimate) -> Reservation: ...
    async def commit(self, reservation: Reservation, actual: UsageActual): ...
    async def release(self, reservation: Reservation): ...
```

If no capacity:
- queue,
- reduce parallelism,
- use another provider/model if acceptable,
- degrade optional analysis,
- never spin aggressively on 429.

---

# 9. Circuit Breakers

Circuit state:
- CLOSED
- OPEN
- HALF_OPEN

Open when:
- repeated provider failures,
- invalid responses,
- sustained rate-limit responses beyond known capacity.

Half-open:
- small probe request.

Provider health is shared across workers through Redis.

---

# 10. Request Priority

Priority classes:

### P0 — user-blocking
- chat response,
- final guide section needed for UI.

### P1 — core analysis
- architecture synthesis,
- workflow reconstruction,
- guide planning.

### P2 — enrichments
- optional deep dives,
- additional interview questions,
- secondary diagrams.

### P3 — background evaluation
- non-blocking online eval.

Under free-tier pressure:
- preserve P0/P1,
- defer P2/P3.

---

# 11. Local Embeddings

External LLM quota should not be consumed for embeddings if avoidable.

Use a local embedding interface.

Baseline candidates should be benchmarked for:
- code search,
- natural-language-to-code search,
- documentation retrieval,
- mixed code/text repositories.

The chosen model must be:
- free/open,
- locally runnable,
- CPU-capable,
- batchable.

Do not make the architecture dependent on one model name.

Store model/version in Qdrant collection metadata.

---

# 12. Qdrant Vector Layer

Qdrant provides dense retrieval.

One shared collection is acceptable initially.

Required metadata:
- project_id,
- chunk_id,
- file path,
- symbol ID,
- subsystem ID,
- language,
- content type,
- importance,
- graph node IDs.

All queries are project-filtered.

Dense search is a candidate generator, not the final answer.

---

# 13. Native Lexical Retrieval

BM25 is implemented by Unfold.

Rationale:
- exact identifiers matter in code,
- lexical behavior must be transparent,
- fusion must remain application-controlled,
- retrieval experiments need independent components.

Index transformations:
- preserve original tokens,
- split snake_case,
- split camelCase,
- tokenize paths,
- index qualified names,
- retain framework/config identifiers.

Example:

```text
verifyAccessToken
→ verifyAccessToken
→ verify
→ access
→ token
```

---

# 14. Native Hybrid RAG

Core implementation:

```text
                 Query
                   │
            Retrieval Planner
             /      |       \
            /       |        \
         BM25     Dense     Graph
            \       |        /
             \      |       /
               Candidate Pool
                    │
                   RRF
                    │
            Cross-Encoder
                    │
            Graph Expansion
                    │
          Sufficiency Evaluation
                    │
             Context Compiler
```

This directly evolves the earlier Hybrid RAG design:
- BM25
- dense retrieval
- RRF
- cross-encoder reranking

Unfold adds:
- structural retrieval,
- graph retrieval,
- graph expansion,
- context sufficiency,
- hierarchical summaries.

---

# 15. Reciprocal Rank Fusion

For ranking list \(r\):

```text
score(d) = Σ w_i / (k + rank_i(d))
```

Requirements:
- weights configurable,
- source rank preserved,
- exact candidate identity normalization,
- project filters,
- deterministic tests.

Potential adaptive weighting:

### Symbol lookup
Higher lexical weight.

### Conceptual architecture question
Higher dense + graph weight.

### Workflow tracing
Higher graph + structural weight.

Weights are selected by retrieval intent and later tuned with evaluation.

---

# 16. Cross-Encoder Reranking

Use a local cross-encoder where possible.

Purpose:
- recover precision after high-recall fusion.

Pipeline:

```text
BM25 top-N
Dense top-N
Graph candidates
   ↓
Fusion
   ↓
candidate pool
   ↓
Cross encoder
   ↓
top-M
```

Reranker batching is mandatory.

Evaluation should compare:
- no reranker,
- reranker,
- different candidate sizes.

---

# 17. Structural Retrieval

Structural retrieval does not rely on similarity.

Examples:
- find entrypoints,
- get route handler,
- get service for route,
- list imports,
- list callers,
- retrieve schema,
- find tests for symbol.

This data comes from Project IR.

Structural results are especially useful for:
- “where is X implemented?”
- “what calls Y?”
- “which table does this endpoint write to?”

---

# 18. Neo4j Graph Intelligence

Neo4j is central to project understanding.

The graph enables:
- subsystem reconstruction,
- dependency traversal,
- workflow tracing,
- impact analysis,
- context expansion,
- diagram generation.

The graph should not mirror every syntax tree node.

Store semantic software entities and relationships.

---

# 19. Graph Communities

Large repositories need hierarchy.

Process:

1. export relevant project graph,
2. run community detection,
3. assign community/subsystem IDs,
4. summarize each community,
5. persist summary and membership.

Community detection implementation may use open graph libraries locally if that simplifies licensing/deployment.

Community boundaries are suggestions, not absolute truth.
LLM/agent synthesis may refine them.

---

# 20. Hierarchical Summarization

Summary levels:

```text
Symbol
  ↓
File
  ↓
Module
  ↓
Subsystem
  ↓
Project
```

But only meaningful items receive model summaries.

### Deterministic summary first
Example:
- file contains 4 routes,
- imports X/Y,
- exposes class Z.

### Model semantic summary second
Example:
- “This module handles candidate authentication and token verification.”

The final summary stores both factual structure and semantic role.

---

# 21. Retrieval Planner

The Retrieval Planner determines how to search.

Examples:

## Query
“Why is Redis used?”

Plan:
- semantic: Redis purpose, caching, queue, session,
- lexical: redis, Redis, cache, queue,
- graph seeds: Redis/Cache/Queue nodes,
- structural: configs and client initializers.

## Query
“Trace upload flow.”

Plan:
- locate frontend upload event,
- locate API endpoint,
- trace service calls,
- trace storage/data writes,
- inspect async jobs.

The planner may use a cheap LLM for ambiguous queries, but deterministic rules handle obvious cases.

---

# 22. Query Expansion

Use multi-query retrieval where useful.

Example:

```text
"authentication workflow"
```

expands to:
- login,
- token,
- auth middleware,
- session,
- user identity,
- authorization,
- verify token.

Expansion should be bounded and intent-specific.

Do not blindly generate ten paraphrases for every query.

---

# 23. Graph Expansion

After reranking, map retrieved chunks to graph nodes.

Expand around high-value seeds:
- callers,
- callees,
- containing subsystem,
- database entities,
- external dependencies,
- related routes,
- deployment resource.

Graph depth:
- normally 1–2,
- deeper only for workflow tracing.

Use edge-type allow-lists to avoid graph explosion.

---

# 24. Context Sufficiency

A retrieval result can be relevant but incomplete.

Before generation, ask:

- Do we have the start of the workflow?
- Do we have the major components?
- Do we know where data is persisted?
- Are claims supported?
- Is a critical subsystem missing?

The sufficiency checker returns:
- sufficient/not sufficient,
- missing evidence types,
- next retrieval objective.

This supports bounded agentic retrieval.

---

# 25. Context Compiler

The Context Compiler is the quality-critical layer.

It receives:
- project summary,
- subsystem summaries,
- retrieved chunks,
- graph paths,
- structural facts,
- user level,
- objective,
- chat history or guide section state.

It outputs a structured prompt context.

Priority order:
1. verified facts,
2. high-confidence graph facts,
3. direct evidence,
4. subsystem summaries,
5. useful inferred findings,
6. supplemental raw text.

---

# 26. Context Compression

Strategies:
- deduplication by content hash/similarity,
- parent summary substitution,
- evidence snippet extraction,
- repeated-path collapse,
- low-value metadata removal,
- recent chat window + compacted history,
- agent result offloading.

Deep Agents can use context offloading for long investigations.

---

# 27. LangGraph

LangGraph is the orchestration runtime because Unfold requires:
- deterministic + agentic steps,
- persistent state,
- parallel branches,
- retries,
- conditional routing,
- long-running analysis.

Graph state stores IDs/references, not full repository content.

---

# 28. Deep Agents

Deep Agents are used deliberately, not as a rare emergency mechanism.

Suitable tasks:
- architecture investigation,
- workflow reconstruction,
- subsystem analysis,
- AI/ML pipeline analysis,
- infrastructure analysis,
- security investigation,
- hard chatbot questions,
- contradictory evidence resolution.

A Deep Agent receives a constrained tool set and structured output schema.

---

# 29. Deep Agent Tool Set

Core tools:

```text
search_lexical(query)
search_dense(query)
search_hybrid(query)
query_graph(cypher_template, params)
get_neighbors(node_id, edge_types)
find_symbol(name)
find_references(symbol_id)
trace_call_path(source, target)
read_file(path, range)
read_symbol(symbol_id)
inspect_manifest(path)
get_subsystem(subsystem_id)
get_project_summary()
```

Security:
- read-only,
- project-scoped,
- no shell,
- no arbitrary outbound network.

---

# 30. Agent Delegation

A parent investigator may spawn subagents.

Example architecture task:

```text
Architecture Investigator
├── frontend subagent
├── backend subagent
├── data subagent
└── infrastructure subagent
```

Subagents return structured findings.
Parent synthesizes.

This protects main context from uncontrolled growth.

---

# 31. Agent Budgets

Every agent has:
- max tool calls,
- max subagents,
- max LLM calls,
- max wall time,
- max context budget,
- max retrieval rounds.

Budget depends on project complexity.

No infinite “continue until done” loops.

---

# 32. Project Complexity Score

Use deterministic signals:

- source file count,
- source LOC,
- number of languages,
- number of services,
- graph node count,
- graph edge count,
- subsystem count,
- infrastructure complexity,
- AI/ML presence.

Complexity influences:
- number of Deep Agents,
- retrieval breadth,
- summary hierarchy depth,
- parallelism,
- guide reading length.

---

# 33. Guide Intelligence

The guide is generated from a planned knowledge structure.

```text
Project synthesis
   ↓
Guide Planner
   ↓
Section objectives
   ↓
Per-section context compilation
   ↓
Parallel generation
   ↓
Claim verification
   ↓
Cross-section consistency
   ↓
Final guide
```

Never:
- concatenate repository chunks,
- make one giant prompt,
- ask “write a full project explanation.”

---

# 34. Technical Depth Personalization

Inputs:
- Beginner
- Intermediate
- Experienced
- optional user focus.

Depth model:

### Beginner
More intuition, fewer implementation assumptions.

### Intermediate
Architecture + implementation concepts + tradeoffs.

### Experienced
Condensed basics, deeper engineering decisions, scaling, failure behavior.

The chatbot can override depth per question.

---

# 35. Claim Extraction

After generation, extract factual claims.

Example:
> “Authentication is performed by Firebase tokens verified in middleware.”

Convert to:

```json
{
  "claim": "...",
  "type": "project_fact",
  "evidence_required": true
}
```

---

# 36. Evidence Verification

For each project fact:
1. map claim to evidence,
2. check source/graph consistency,
3. classify support.

Statuses:
- VERIFIED
- STRONGLY_INFERRED
- INFERRED
- UNSUPPORTED

Unsupported factual text:
- remove,
- repair,
- or explicitly qualify.

---

# 37. Chat AI Architecture

Chat request:

```text
question
  ↓
intent + depth
  ↓
conversation memory
  ↓
retrieval plan
  ↓
hybrid/graph retrieval
  ↓
sufficiency
  ├─ enough → answer
  └─ insufficient → Deep Agent
                      ↓
                   answer
  ↓
claim verification
  ↓
stream
```

---

# 38. Chat Memory

Store:
- recent messages,
- older summary,
- current learning state.

Learning state may include:
- topics already explained,
- user-requested depth,
- current subsystem focus.

No persistent cross-session memory.

---

# 39. Prompt Engineering

Prompt templates must be versioned.

Categories:
- repository semantic inference,
- subsystem summary,
- guide planning,
- section writing,
- investigator,
- chat,
- claim extraction,
- verification,
- evaluation.

Prompts should:
- separate trusted system instructions from repository data,
- clearly mark repository content as untrusted,
- require structured output,
- require uncertainty when evidence is insufficient.

---

# 40. Prompt Injection Defense

Layers:

1. repository instruction-pattern scanner,
2. trust labeling,
3. secret redaction,
4. hard system instruction,
5. tool scoping,
6. no repository execution,
7. evidence verification,
8. adversarial evaluation suite.

Repository content such as:
- README,
- comments,
- AGENTS.md,
- prompt.md,
- CLAUDE.md

must never override system behavior.

---

# 41. Secrets and Sensitive Content

Even public repositories may accidentally contain secrets.

Before external LLM calls:
- run secret detection,
- replace sensitive values with placeholders,
- preserve enough structure to explain configuration.

Example:
```text
API_KEY="sk-..."
→ API_KEY="<REDACTED_SECRET>"
```

---

# 42. Evaluation Philosophy

Do not ask:
> “Does the demo look good?”

Ask:
> “Which component improved which measurable behavior?”

Every major AI change should have a benchmark delta.

---

# 43. Retrieval Evaluation

Deterministic metrics:
- Recall@1/5/10/20,
- Precision@K,
- MRR,
- MAP,
- nDCG,
- hit rate,
- relevant file recall,
- relevant symbol recall,
- graph node recall,
- graph path recall.

System metrics:
- context redundancy,
- subsystem coverage,
- evidence coverage,
- retrieval latency.

Compare:
- BM25,
- dense,
- BM25+dense+RRF,
- +reranker,
- +graph,
- +query planning,
- +context sufficiency.

---

# 44. RAG Evaluation

Use DeepEval and RAGAS.

Core dimensions:
- contextual precision,
- contextual recall,
- contextual relevancy,
- faithfulness,
- answer relevancy,
- factual correctness.

LLM-judge metrics must use:
- fixed evaluator config,
- versioned prompts,
- repeat samples where variance matters.

---

# 45. Agent Evaluation

Evaluate:
- task completion,
- tool-call accuracy,
- tool-call F1,
- unnecessary tool-call rate,
- retrieval loops,
- trajectory length,
- evidence completeness,
- subagent coverage,
- failure recovery,
- latency.

DeepEval is the primary trajectory-oriented framework.

RAGAS agent/tool metrics may be used as complementary signals.

---

# 46. Guide Evaluation

Custom guide metrics:

### Architecture coverage
Did guide cover major architecture components?

### Workflow completeness
Were primary user/system workflows explained?

### Technology correctness
Are major stack claims correct?

### Evidence support
What fraction of factual statements is supported?

### Explanation coherence
Does the guide read as one system explanation rather than disconnected summaries?

### Level appropriateness
Is explanation depth appropriate for selected user level?

### Redundancy
How much duplicate material exists?

### Read-time adherence
Does the guide roughly fit intended length?

---

# 47. Chat Evaluation

Test:
- factual QA,
- code-location QA,
- workflow tracing,
- concept explanation,
- tradeoff questions,
- multi-turn follow-ups,
- adversarial prompt injection,
- context compaction.

Metrics:
- accuracy,
- grounding,
- relevance,
- continuity,
- tool correctness,
- refusal to invent missing facts.

---

# 48. Human Evaluation

Maintain a human rubric for periodically reviewing:
- understandability,
- correctness,
- usefulness,
- visual quality,
- interview usefulness.

Human review is especially important for Project Guide quality because pure automatic metrics cannot fully measure learning value.

---

# 49. Golden Benchmark

A repository benchmark should include multiple project categories.

Each benchmark case contains:

```json
{
  "repo_id": "...",
  "questions": [...],
  "gold_files": [...],
  "gold_symbols": [...],
  "gold_graph_paths": [...],
  "expected_facts": [...],
  "major_subsystems": [...]
}
```

Use both:
- manually verified samples,
- synthetically generated candidate questions followed by human validation.

---

# 50. Evaluation in CI/CD

### Offline PR gate
Always run:
- retrieval gold tests,
- RRF tests,
- graph extraction tests,
- prompt-injection fixtures,
- deterministic claim checks.

### Online bounded eval
Scheduled/manual:
- selected Gemini/Groq eval calls,
- DeepEval,
- RAGAS,
- agent trajectory tests.

Keep the online set intentionally small because V1 uses free-tier APIs.

### Release benchmark
Run larger benchmark before tagged releases when quota permits.

---

# 51. LLM-as-Judge Risks

Do not trust one evaluator blindly.

Mitigations:
- deterministic metrics first,
- use reference answers/evidence where available,
- multiple metrics,
- occasional human review,
- separate generation model from evaluator when practical,
- record evaluator model/version.

---

# 52. AI Observability

Every LLM call trace records:
- purpose,
- provider,
- model,
- latency,
- input/output token estimate,
- retry count,
- structured-output success,
- project/job ID.

Every retrieval trace records:
- query,
- intent,
- lexical candidates,
- dense candidates,
- fused candidates,
- reranked candidates,
- graph expansion,
- final evidence set.

Every agent trace records:
- objective,
- tools,
- subagents,
- iterations,
- result status.

Use OpenTelemetry as the common instrumentation layer.

Optional self-hosted visualization can be added later.

---

# 53. Caching AI Work

Cache by content/config fingerprint.

Examples:
- embedding(content_hash, embedding_model),
- summary(content_hash, prompt_version, model_family),
- structural analysis(file_hash, parser_version),
- retrieval(query_hash, project_index_version),
- LLM response(prompt_hash, model, schema_version) where safe.

Caching is important because free-tier capacity is constrained.

---

# 54. Graceful Degradation

If strong reasoning model unavailable:
- use fallback model,
- reduce optional enrichments,
- preserve core guide generation,
- mark sections pending if necessary rather than fabricate.

If dense index fails:
- BM25 + graph can still operate.

If graph temporarily unavailable:
- lexical/dense retrieval can still answer limited chat, but architecture generation should not silently claim full quality.

If reranker unavailable:
- use fused ranking.

---

# 55. AI Testing

Unit tests:
- router,
- budget manager,
- key health,
- RRF,
- chunker,
- context prioritizer,
- claim status logic.

Integration:
- Qdrant retrieval,
- Neo4j graph expansion,
- Deep Agent tool contracts,
- LangGraph state recovery.

Regression:
- benchmark repositories.

---

# 56. Free-Tier Optimization

Strategies:
- local embeddings,
- local reranker,
- deterministic parsing,
- hierarchical caching,
- content-hash reuse,
- cheap model for classification,
- parallel calls only within quota,
- no repeated full-repo prompting,
- agent budgets,
- prompt compression,
- skip irrelevant files,
- use stored summaries in later stages.

The goal is not “use the LLM less” universally.
The goal is:
> use LLM tokens only where semantic reasoning provides real value.

---

# 57. Model Assignment Strategy

A practical default:

### Deterministic/no LLM
- file inventory,
- parsing,
- Project IR,
- lexical indexing,
- graph deterministic edges,
- secret scan.

### Fast model
- file/module semantic labels,
- query intent,
- light summaries,
- chat summarization.

### Balanced model
- subsystem summaries,
- normal guide chapters,
- normal chatbot responses.

### Reasoning model
- architecture synthesis,
- workflow reconstruction,
- conflict resolution,
- difficult Deep Agent tasks,
- final verification.

Configuration selects which Gemini/Groq models currently satisfy each tier.

---

# 58. Why Not CrewAI

CrewAI is intentionally excluded because:
- LangGraph already owns orchestration,
- Deep Agents already own planning/delegation/context management,
- a third agent abstraction complicates state and debugging,
- eval traces become harder to interpret.

The architecture should have one orchestration hierarchy.

---

# 59. Why No MCP Internally

MCP is intentionally excluded from Unfold’s internal runtime because:
- native Python calls are lower overhead,
- types are easier to enforce,
- observability is simpler,
- security boundaries are clearer,
- fewer moving pieces.

Future external MCP exposure is possible if Unfold later wants other systems to query its project intelligence.

---

# 60. AI Infrastructure Definition of Done

The AI foundation is considered production-ready when:

- Project IR is versioned,
- Neo4j graph is deterministic + provenance-aware,
- BM25 and dense retrieval are benchmarked,
- RRF and reranker are evaluated,
- graph expansion measurably improves target tasks,
- Context Compiler is independently testable,
- LangGraph stages are resumable,
- Deep Agents are bounded and traced,
- all LLM calls go through Gateway,
- provider budgets/circuit breakers work,
- secret redaction exists,
- prompt injection suite exists,
- retrieval/generation/agent evals run in CI/scheduled workflows,
- guide claims are evidence-checked,
- chatbot preserves session context,
- failures degrade explicitly rather than hallucinating.

---

# 61. North-Star AI Principle

> **Do not ask a model to infer what the system can determine directly, and do not feed a model raw information when the system can compile better context first.**

That principle should guide every AI engineering decision in Unfold.
