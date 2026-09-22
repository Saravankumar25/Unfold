# Unfold — Engineering Specification Document (ESD)

> **Caption:** Prepare your project.  
> **Document status:** Draft v1.0  
> **Purpose:** Implementation-level engineering specification for Unfold  
> **Architecture style:** Modular monolith + asynchronous workers + specialized data stores  
> **Primary language:** Python for backend/AI, TypeScript for frontend

---

# 1. Engineering Goals

Unfold must be engineered as a production-ready system from the first implementation while avoiding premature distributed-system complexity.

The system must:

1. accept public GitHub repositories and ZIP uploads,
2. safely ingest untrusted project files,
3. build a deterministic structural understanding,
4. transform that understanding into a normalized Project IR,
5. persist meaningful relationships in Neo4j,
6. build a native lexical + dense Hybrid RAG index,
7. create hierarchical summaries and subsystem understanding,
8. orchestrate deterministic and agentic analysis with LangGraph,
9. use Deep Agents for multi-step project investigation,
10. compile evidence-rich context for Project Guide generation,
11. generate a dynamic 45–60 minute Project Guide,
12. support grounded project chat,
13. produce two PDF export types,
14. run formal retrieval/generation/agent evaluations,
15. support 4 Gemini + 4 Groq credentials without abusing provider quotas,
16. be fully containerizable,
17. include CI/CD and Terraform scaffolding from day one,
18. remain cloud-portable until the final GCP deployment plan is chosen.

---

# 2. Architectural Style

## 2.1 V1 choice: modular monolith + workers

Do **not** split Unfold into many network microservices initially.

Use:
- one FastAPI application,
- one worker application image with multiple queue/task types,
- one Next.js frontend,
- specialized backing stores.

Logical modules are strongly separated in code, but most communicate through native Python interfaces rather than network APIs.

Benefits:
- lower latency,
- easier local development,
- fewer failure boundaries,
- one shared domain model,
- simpler testing,
- easier future extraction if one subsystem needs independent scaling.

### Logical modules

```text
api
session
ingestion
security
repository_intelligence
project_ir
graph
indexing
retrieval
context_engineering
llm_gateway
agents
guide
chat
diagrams
exports
evaluation
observability
cleanup
```

---

# 3. Recommended Repository Layout

```text
unfold/
├── apps/
│   ├── web/                       # Next.js
│   ├── api/                       # FastAPI process
│   └── worker/                    # async worker entrypoint
│
├── backend/
│   ├── src/unfold/
│   │   ├── api/
│   │   │   ├── routes/
│   │   │   ├── dependencies/
│   │   │   ├── middleware/
│   │   │   └── schemas/
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── errors.py
│   │   │   ├── logging.py
│   │   │   ├── ids.py
│   │   │   └── security.py
│   │   ├── domain/
│   │   │   ├── models.py
│   │   │   ├── enums.py
│   │   │   └── events.py
│   │   ├── persistence/
│   │   │   ├── postgres/
│   │   │   ├── redis/
│   │   │   ├── qdrant/
│   │   │   ├── neo4j/
│   │   │   └── object_store/
│   │   ├── ingestion/
│   │   ├── repo_intelligence/
│   │   │   ├── scanners/
│   │   │   ├── parsers/
│   │   │   ├── analyzers/
│   │   │   ├── symbols/
│   │   │   ├── dependencies/
│   │   │   └── languages/
│   │   ├── project_ir/
│   │   ├── graph/
│   │   ├── indexing/
│   │   │   ├── lexical/
│   │   │   ├── dense/
│   │   │   └── chunking/
│   │   ├── retrieval/
│   │   │   ├── planner/
│   │   │   ├── lexical.py
│   │   │   ├── dense.py
│   │   │   ├── graph.py
│   │   │   ├── structural.py
│   │   │   ├── fusion.py
│   │   │   ├── rerank.py
│   │   │   └── sufficiency.py
│   │   ├── context/
│   │   ├── llm/
│   │   │   ├── gateway.py
│   │   │   ├── providers/
│   │   │   ├── router.py
│   │   │   ├── key_pool.py
│   │   │   ├── budgets.py
│   │   │   └── schemas.py
│   │   ├── orchestration/
│   │   │   ├── langgraph/
│   │   │   └── state.py
│   │   ├── agents/
│   │   │   ├── deep_agents/
│   │   │   ├── tools/
│   │   │   └── outputs/
│   │   ├── guide/
│   │   ├── chat/
│   │   ├── diagrams/
│   │   ├── exports/
│   │   ├── evaluation/
│   │   │   ├── datasets/
│   │   │   ├── retrieval/
│   │   │   ├── generation/
│   │   │   ├── agents/
│   │   │   └── regression/
│   │   ├── observability/
│   │   └── cleanup/
│   │
│   ├── tests/
│   │   ├── unit/
│   │   ├── integration/
│   │   ├── contract/
│   │   ├── security/
│   │   └── evals/
│   └── pyproject.toml
│
├── frontend/
│   └── ...
├── infra/
│   ├── terraform/
│   │   ├── modules/
│   │   └── environments/
│   └── docker/
├── evals/
│   ├── golden/
│   ├── fixtures/
│   └── reports/
├── docs/
│   ├── PRD.md
│   ├── ESD.md
│   ├── architecture.md
│   └── AI_infrastructure.md
├── .github/workflows/
├── docker-compose.yml
├── Dockerfile.api
├── Dockerfile.worker
├── Makefile
└── README.md
```

---

# 4. Core Data Stores

## 4.1 PostgreSQL — operational system of record

Purpose:
- anonymous sessions,
- project metadata,
- job metadata,
- guide state,
- chat metadata,
- generated artifacts,
- lifecycle/expiry state,
- evaluation run metadata.

It is **not** the vector database and **not** the code knowledge graph.

Suggested tables:

### sessions

```sql
sessions (
    id UUID PRIMARY KEY,
    public_token_hash TEXT NOT NULL,
    technical_level TEXT NOT NULL,
    user_instruction TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    last_active_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL
)
```

### projects

```sql
projects (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL,
    input_type TEXT NOT NULL,       -- github | zip
    source_locator TEXT NULL,
    source_fingerprint TEXT NOT NULL,
    detected_name TEXT,
    default_branch TEXT,
    commit_sha TEXT,
    project_type JSONB,
    stats JSONB,
    analysis_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
)
```

### analysis_jobs

```sql
analysis_jobs (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL,
    status TEXT NOT NULL,
    current_stage TEXT,
    stage_progress JSONB,
    attempt INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_detail JSONB,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ
)
```

### guide_sections

```sql
guide_sections (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL,
    section_key TEXT NOT NULL,
    title TEXT NOT NULL,
    position INTEGER NOT NULL,
    status TEXT NOT NULL,
    content JSONB,
    evidence_summary JSONB,
    quality_scores JSONB,
    version INTEGER NOT NULL DEFAULT 1
)
```

### chat_sessions

```sql
chat_sessions (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL,
    title TEXT,
    summary TEXT,
    learning_state JSONB,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
)
```

### chat_messages

```sql
chat_messages (
    id UUID PRIMARY KEY,
    chat_session_id UUID NOT NULL,
    role TEXT NOT NULL,
    content JSONB NOT NULL,
    evidence JSONB,
    model_metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL
)
```

### artifacts

```sql
artifacts (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL,
    kind TEXT NOT NULL,
    object_key TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes BIGINT,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
)
```

---

## 4.2 Neo4j — project knowledge graph

Neo4j owns the **relationship model** of the codebase.

It stores semantic engineering entities, not raw AST nodes.

Core labels:
- Project
- Subsystem
- Module
- Directory
- File
- Class
- Function
- Method
- Interface
- APIEndpoint
- Service
- Database
- Table
- Model
- Schema
- Worker
- Queue
- Topic
- Event
- ExternalService
- Library
- Framework
- InfrastructureResource
- MLModel
- Dataset
- Pipeline
- PipelineStage
- Agent
- Retriever
- Concept
- Evidence

Core relationships:
- CONTAINS
- PART_OF
- IMPORTS
- EXPORTS
- CALLS
- IMPLEMENTS
- INHERITS
- DEPENDS_ON
- EXPOSES
- HANDLES
- USES
- READS_FROM
- WRITES_TO
- PUBLISHES_TO
- CONSUMES_FROM
- CONNECTS_TO
- AUTHENTICATES_WITH
- DEPLOYS_TO
- CONFIGURES
- TRAINS
- EVALUATES
- INFERENCES_WITH
- RETRIEVES_FROM
- RERANKS_WITH
- EVIDENCED_BY

Every relationship should contain provenance:

```json
{
  "source": "deterministic|inferred",
  "confidence": 0.0,
  "evidence_ids": ["..."],
  "analyzer": "python_ast_v1",
  "analysis_version": "..."
}
```

---

## 4.3 Qdrant — dense semantic retrieval index

Qdrant stores:
- semantic chunks,
- embeddings,
- metadata needed for filtering,
- references back to Project IR / graph node IDs.

The native Hybrid RAG implementation does **not** delegate fusion logic to Qdrant. Qdrant is primarily the dense vector index.

Suggested payload:

```json
{
  "project_id": "...",
  "chunk_id": "...",
  "file_path": "backend/auth/service.py",
  "language": "python",
  "symbol_id": "...",
  "symbol_type": "class",
  "subsystem_id": "...",
  "graph_node_ids": ["..."],
  "importance": 0.91,
  "content_type": "source|docs|config|test|infra",
  "start_line": 10,
  "end_line": 83,
  "trust": "untrusted_source"
}
```

---

## 4.4 Native BM25 index

BM25 is implemented as an application-owned retrieval component, aligned with the existing Hybrid RAG approach.

Recommended V1 implementation:
- `bm25s` or equivalent efficient Python-native lexical engine,
- one persisted index per project,
- memory-map/load on demand where possible,
- serialized index stored in object storage or worker cache,
- query-time filter by project and content tier.

Do not rely on Qdrant’s built-in hybrid fusion for the primary implementation because:
- Unfold should own lexical retrieval behavior,
- RRF logic should be inspectable/testable,
- query-specific weighting can be evolved independently,
- existing Hybrid RAG behavior can be preserved.

---

## 4.5 Redis

Redis owns ephemeral state:
- worker queues,
- task coordination,
- distributed locks,
- analysis progress,
- rate-limit buckets,
- model/provider health cache,
- LLM response cache,
- chat recent-message cache,
- temporary retrieval cache.

Redis must not become the long-term source of truth.

---

## 4.6 Object storage

Cloud-neutral interface, implemented locally with MinIO and later mapped to GCP-compatible storage.

Stores:
- uploaded ZIP,
- cloned repository archive where required,
- lexical index artifacts,
- Project IR snapshots,
- generated diagrams,
- generated PDFs,
- evaluation reports,
- optional debug artifacts.

All objects receive expiry metadata.

---

# 5. Project Analysis State Machine

```text
RECEIVED
  ↓
VALIDATING_INPUT
  ↓
INGESTING
  ↓
SECURITY_SCANNING
  ↓
INVENTORY
  ↓
STRUCTURAL_ANALYSIS
  ↓
BUILDING_IR
  ↓
BUILDING_GRAPH
  ↓
INDEXING_LEXICAL
  ↓
INDEXING_DENSE
  ↓
SUMMARIZING_HIERARCHY
  ↓
DISCOVERING_SUBSYSTEMS
  ↓
INVESTIGATING_PROJECT
  ↓
PLANNING_GUIDE
  ↓
GENERATING_GUIDE
  ↓
VERIFYING_GUIDE
  ↓
GENERATING_VISUALS
  ↓
FINALIZING
  ↓
READY
```

Failure states:
- FAILED_INPUT
- FAILED_SECURITY
- FAILED_INGESTION
- FAILED_ANALYSIS
- FAILED_PROVIDER_CAPACITY
- FAILED_GENERATION
- FAILED_EXPORT

Retryable failures must be distinguished from terminal failures.

---

# 6. Input Ingestion

## 6.1 GitHub URL

Validation:
- HTTPS only,
- github.com host,
- public repository only,
- normalize URL,
- resolve default branch,
- capture commit SHA,
- shallow clone or archive download,
- enforce total repository size limits.

Security:
- no Git hooks,
- no submodule execution,
- submodules may be recorded but should not be automatically fetched unless explicitly allowed by policy,
- repository remains read-only.

## 6.2 ZIP upload

Validation:
- MIME and magic signature,
- maximum compressed size,
- maximum extracted size,
- maximum file count,
- maximum path depth,
- maximum individual file size.

Extraction:
- use dedicated temporary directory,
- normalize every path,
- reject traversal,
- reject symlink escape,
- reject device/special files.

---

# 7. Repository Inventory

The inventory stage creates a complete metadata inventory before semantic analysis.

For each file:
- path,
- size,
- extension,
- language,
- MIME category,
- generated/vendor flag,
- binary/text flag,
- likely importance,
- repository role,
- hash.

Ignore or heavily down-rank:
- `.git`,
- `node_modules`,
- `.venv`,
- `vendor`,
- generated builds,
- coverage,
- datasets too large for textual analysis,
- model weights,
- minified bundles,
- binaries,
- caches,
- lockfiles except dependency metadata extraction.

Importance tiers:

### Tier 0 — architecture-critical
- entrypoints,
- app bootstrap,
- package manifests,
- Dockerfiles,
- Compose,
- CI workflows,
- Terraform,
- primary API routes,
- DB schemas,
- model/pipeline definitions.

### Tier 1 — core domain
- services,
- controllers,
- repositories,
- main components,
- core algorithms.

### Tier 2 — support
- helpers,
- adapters,
- secondary configuration.

### Tier 3 — context
- docs,
- tests,
- examples.

### Tier 4 — noise/generated
Normally excluded.

---

# 8. Static and Structural Analysis

Use layered analyzers.

## 8.1 Tree-sitter
Used for:
- broad language coverage,
- syntax tree,
- functions/classes,
- imports,
- declarations,
- call-site hints.

## 8.2 Language-aware analyzers

Examples:

### Python
- built-in `ast`,
- import graph,
- decorators,
- FastAPI/Flask/Django routes,
- Pydantic models,
- ORM models,
- task decorators.

### TypeScript/JavaScript
- TypeScript compiler API or language-service-based analyzer when available,
- imports/exports,
- React components,
- hooks,
- route handlers,
- API clients.

### Java
- parser/symbol resolver where available,
- Spring annotations,
- controllers/services/repositories.

The analyzer interface:

```python
class LanguageAnalyzer(Protocol):
    def supports(self, file: SourceFile) -> bool: ...
    def analyze(self, file: SourceFile, ctx: AnalysisContext) -> FileAnalysis: ...
```

No parser is allowed to execute repository code.

---

# 9. Project IR

Project IR is the normalized representation that decouples downstream intelligence from language-specific parsers.

Core data model:

```python
class ProjectIR(BaseModel):
    project: ProjectIdentity
    languages: list[LanguageInfo]
    frameworks: list[FrameworkInfo]
    files: list[FileIR]
    symbols: list[SymbolIR]
    modules: list[ModuleIR]
    endpoints: list[EndpointIR]
    schemas: list[SchemaIR]
    data_stores: list[DataStoreIR]
    services: list[ServiceIR]
    queues: list[QueueIR]
    infrastructure: list[InfraResourceIR]
    ai_components: list[AIComponentIR]
    dependencies: list[DependencyIR]
    relationships: list[RelationshipIR]
    evidence: list[EvidenceIR]
```

Every element should support:
- stable ID,
- provenance,
- confidence,
- source file/line references where applicable.

Project IR snapshots are versioned.

---

# 10. Graph Builder

The graph builder converts Project IR into Neo4j.

Requirements:
- idempotent upsert,
- project-scoped unique keys,
- deterministic relationship edges before inferred edges,
- batch writes,
- provenance on every inferred relation,
- cleanup by `project_id`.

Graph inference occurs in two phases:

### Phase A — deterministic
Built from parsers, manifests, configuration, schemas, imports, routes.

### Phase B — semantic
LLM/Deep Agent discovers:
- subsystem boundaries,
- conceptual ownership,
- higher-order workflows,
- architectural roles.

Semantic inference cannot overwrite deterministic facts.

---

# 11. Chunking and Indexing

## 11.1 Code-aware chunks

Do not use fixed-character chunking.

Chunk by:
- function,
- method,
- class,
- component,
- route,
- schema,
- migration,
- config block,
- documentation section.

Oversized semantic units may be recursively split.

Each chunk stores:
- source path,
- line range,
- parent symbol,
- module,
- subsystem,
- language,
- content type,
- graph references,
- summary,
- importance.

## 11.2 Hierarchical summaries

Summarization hierarchy:

```text
important symbol
   ↓
file
   ↓
module
   ↓
subsystem/community
   ↓
project
```

Do not spend LLM calls summarizing trivial symbols.

Use deterministic signatures and parser metadata wherever sufficient.

---

# 12. Native Hybrid RAG

The native retrieval pipeline is application-owned.

```text
Query
  ↓
Query Planner
  ├─ lexical query
  ├─ semantic query variants
  ├─ graph seed hints
  └─ structural constraints
  ↓
BM25 retrieval
Dense retrieval
Graph retrieval
Structural retrieval
  ↓
Candidate normalization
  ↓
RRF
  ↓
Cross-encoder reranking
  ↓
Graph neighborhood expansion
  ↓
Context sufficiency check
  ↓
Compiled evidence set
```

### Reciprocal Rank Fusion

```text
RRF(d) = Σ 1 / (k + rank_i(d))
```

The fusion implementation is owned by Unfold and fully unit-tested.

Configuration:
- source weights,
- RRF `k`,
- candidate counts,
- reranker cutoff,
- graph expansion depth,
- evidence budget.

All retrieval settings must be versioned for evaluation.

---

# 13. Query Planner

Input:
- user question or guide objective,
- project profile,
- current subsystem,
- user technical level.

Output:

```python
class RetrievalPlan(BaseModel):
    intent: RetrievalIntent
    lexical_queries: list[str]
    semantic_queries: list[str]
    target_subsystems: list[str]
    target_symbols: list[str]
    graph_seed_entities: list[str]
    structural_filters: dict
    desired_evidence_types: list[str]
    breadth: Literal["narrow", "medium", "broad"]
```

Examples of intents:
- overview,
- workflow,
- architecture,
- implementation_location,
- technical_concept,
- design_reasoning,
- failure_analysis,
- security,
- interview_question.

---

# 14. Context Compiler

The Context Compiler is a first-class service.

Input:
- RetrievalPlan,
- project summary,
- subsystem summaries,
- graph results,
- lexical/dense evidence,
- user level,
- user instruction,
- current guide section or chat question,
- token budget.

Output:

```python
class CompiledContext(BaseModel):
    objective: str
    project_identity: str
    global_context: list[ContextItem]
    subsystem_context: list[ContextItem]
    graph_paths: list[GraphPath]
    evidence: list[EvidenceItem]
    known_facts: list[Claim]
    inferred_facts: list[Claim]
    uncertainties: list[Uncertainty]
    user_profile: SessionProfile
    token_count_estimate: int
```

The compiler must:
- deduplicate evidence,
- prioritize higher-confidence sources,
- preserve relevant neighboring relationships,
- avoid overloading the prompt with raw source,
- maintain line/symbol provenance.

---

# 15. LangGraph Orchestration

LangGraph owns the top-level analysis state.

Main graph:

```text
ingest
  ↓
inventory
  ↓
analyze_structure
  ↓
build_ir
  ↓
build_graph
  ↓
build_indexes
  ↓
summarize
  ↓
discover_subsystems
  ↓
complexity_router
  ├───────────────┐
  ↓               ↓
direct_analysis   deep_investigation
  └───────┬───────┘
          ↓
project_synthesis
          ↓
guide_planner
          ↓
chapter_generation [parallel]
          ↓
chapter_verification
          ↓
cross_chapter_consistency
          ↓
visual_generation
          ↓
finalize
```

LangGraph state should store references, not giant blobs.

```python
class AnalysisState(TypedDict):
    project_id: str
    job_id: str
    ir_version: str
    graph_version: str
    index_version: str
    subsystem_ids: list[str]
    findings_ids: list[str]
    guide_plan_id: str | None
    section_ids: list[str]
    errors: list[StageError]
```

---

# 16. Deep Agents

Deep Agents are integrated as investigation workers within the LangGraph workflow.

Typical Deep Agents:

### Architecture Investigator
Tools:
- query_graph
- search_hybrid
- read_symbol
- read_file
- find_references
- inspect_manifest

Output:
- system boundaries,
- responsibilities,
- architecture evidence,
- ambiguities.

### Workflow Investigator
Tools:
- trace_call_path
- query_graph
- search_hybrid
- inspect_endpoint
- inspect_data_model

Output:
- end-to-end workflows,
- triggering events,
- state changes,
- failure branches.

### Data/Database Investigator
Output:
- persistence model,
- schemas,
- read/write flows,
- consistency assumptions.

### AI/ML Investigator
Output:
- model/pipeline,
- retrieval/generation flow,
- dataset/training/eval logic,
- inference behavior.

### Infrastructure Investigator
Output:
- containers,
- CI/CD,
- cloud resources,
- runtime dependencies,
- network boundaries.

### Security Investigator
Output:
- auth flow,
- authorization,
- secrets,
- trust boundaries,
- obvious risks.

The system should spawn only relevant investigators based on Project IR and complexity.

Deep Agent tools must be:
- read-only,
- project-scoped,
- budget-limited,
- observable,
- cancellable.

Do not expose unrestricted shell execution to repository-analysis agents.

---

# 17. LLM Gateway

All model calls go through one interface.

```python
class LLMGateway:
    async def generate(self, request: LLMRequest) -> LLMResponse: ...
    async def generate_structured(self, request: StructuredLLMRequest[T]) -> T: ...
```

`LLMRequest` includes:
- capability,
- prompt class,
- context size,
- expected output tokens,
- temperature,
- structured schema,
- latency priority,
- retry policy,
- provider exclusions.

Capabilities:
- FAST
- BALANCED
- REASONING
- LONG_CONTEXT
- STRUCTURED
- EVALUATOR

Provider implementations:
- GeminiProvider
- GroqProvider

No business logic imports provider SDKs directly.

---

# 18. API Key Pool and Quota Management

Environment supports:
- GEMINI_API_KEY_1..4
- GROQ_API_KEY_1..4

However, quota control is modeled at provider/project/organization level.

Components:

```text
ProviderBudget
KeyPool
HealthTracker
CircuitBreaker
RequestScheduler
ModelRouter
```

Each key has:
- health,
- cooldown_until,
- consecutive_failures,
- last_success,
- last_429,
- credential ID.

Each provider budget tracks:
- requests/minute,
- tokens/minute,
- requests/day,
- concurrent calls,
- estimated remaining capacity.

Behavior:
1. choose provider/model by capability,
2. reserve provider capacity,
3. choose healthy credential,
4. execute,
5. update rate state from headers/errors,
6. retry with bounded jitter if safe,
7. switch provider/model where semantically acceptable,
8. queue when all legal capacity is exhausted.

Do not rotate keys simply to evade shared quotas.

---

# 19. Local Embeddings and Reranking

To preserve free-tier LLM capacity, retrieval should avoid external LLM APIs where practical.

Recommended baseline:
- local embedding model optimized/validated for code + text,
- local cross-encoder reranker.

Model names must remain configurable and benchmark-driven.

Interfaces:

```python
class Embedder(Protocol):
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...

class Reranker(Protocol):
    async def rerank(self, query: str, docs: list[RetrievedItem]) -> list[ScoredItem]: ...
```

CPU operation must be supported.
GPU acceleration may be enabled later.

---

# 20. Guide Generation

## 20.1 Guide planner

Planner inputs:
- project summary,
- subsystem map,
- user level,
- user instruction,
- project category,
- complexity budget.

Output:

```python
class GuidePlan(BaseModel):
    estimated_read_minutes: int
    sections: list[GuideSectionPlan]
```

Each section plan contains:
- purpose,
- target reading time,
- required concepts,
- evidence objectives,
- visual needs,
- depth,
- related subsystems.

## 20.2 Section generation

For every section:
1. build retrieval plan,
2. retrieve evidence,
3. compile context,
4. generate structured section,
5. extract claims,
6. verify claims,
7. repair unsupported content,
8. persist section.

## 20.3 Consistency pass

Cross-section validator checks:
- contradictory descriptions,
- duplicated explanations,
- inconsistent naming,
- missing architecture dependencies,
- missing required project layers.

---

# 21. Evidence and Claim Verification

Every high-value generated claim receives one of:

- VERIFIED
- STRONGLY_INFERRED
- INFERRED
- UNKNOWN

`ClaimRecord`:

```python
class ClaimRecord(BaseModel):
    text: str
    status: ClaimStatus
    evidence_ids: list[str]
    graph_node_ids: list[str]
    confidence: float
```

Rules:
- deterministic evidence outranks LLM inference,
- unsupported factual claims are removed or rewritten as uncertainty,
- proposed improvements must not be presented as existing behavior.

---

# 22. Diagram Generation

Do not let the LLM emit arbitrary SVG/HTML directly.

Use a structured diagram model:

```python
class DiagramSpec(BaseModel):
    kind: str
    nodes: list[DiagramNode]
    edges: list[DiagramEdge]
    groups: list[DiagramGroup]
    annotations: list[str]
```

Renderers:
- Mermaid for textual diagrams,
- React Flow for interactive diagrams,
- dedicated schema renderer for database relationships where useful.

Diagram facts should be derived from Project IR/Neo4j first, then optionally refined by LLM.

---

# 23. Chat Architecture

Chat request pipeline:

```text
message
  ↓
session context loader
  ↓
intent classifier
  ↓
retrieval planner
  ↓
hybrid + graph retrieval
  ↓
sufficiency evaluator
  ├─ sufficient → answer
  └─ insufficient
       ↓
     Deep Agent investigation
       ↓
     retrieve more
       ↓
     answer
  ↓
claim grounding
  ↓
stream response
  ↓
update conversation summary / learning state
```

Chat context:
- recent N turns,
- compacted older summary,
- session learning state,
- relevant Project Guide context,
- current retrieved evidence.

---

# 24. API Surface

Prefix: `/api/v1`

### Sessions
- `POST /sessions`
- `GET /sessions/{session_id}`
- `POST /sessions/{session_id}/touch`

### Projects
- `POST /projects/github`
- `POST /projects/zip`
- `GET /projects/{project_id}`
- `GET /projects/{project_id}/status`
- `DELETE /projects/{project_id}`

### Guide
- `GET /projects/{project_id}/guide`
- `GET /projects/{project_id}/guide/sections/{section_id}`
- `POST /projects/{project_id}/guide/sections/{section_id}/simplify`
- `POST /projects/{project_id}/guide/sections/{section_id}/deepen`

### Graph/visuals
- `GET /projects/{project_id}/architecture`
- `GET /projects/{project_id}/diagrams`
- `GET /projects/{project_id}/diagrams/{diagram_id}`

### Chat
- `POST /projects/{project_id}/chats`
- `GET /chats/{chat_id}`
- `POST /chats/{chat_id}/messages`
- `GET /chats/{chat_id}/stream`

### Export
- `POST /projects/{project_id}/exports/guide`
- `POST /chats/{chat_id}/exports`
- `GET /exports/{artifact_id}`

### Progress streaming
- `GET /projects/{project_id}/events`

Use SSE for:
- analysis progress,
- streamed guide generation where exposed,
- chatbot token/event streaming.

---

# 25. Error Model

Unified error envelope:

```json
{
  "error": {
    "code": "PROJECT_TOO_LARGE_FOR_POLICY",
    "message": "The repository exceeded the configured processing policy.",
    "retryable": false,
    "request_id": "..."
  }
}
```

Representative codes:
- INVALID_GITHUB_URL
- REPOSITORY_NOT_PUBLIC
- ZIP_INVALID
- ZIP_BOMB_DETECTED
- INPUT_TOO_LARGE
- UNSUPPORTED_ARCHIVE
- PROVIDER_RATE_LIMITED
- PROVIDER_UNAVAILABLE
- ANALYSIS_STAGE_FAILED
- RETRIEVAL_FAILED
- GENERATION_FAILED
- SESSION_EXPIRED
- ARTIFACT_EXPIRED

---

# 26. Caching Strategy

Cache:
- repository fingerprint → reusable inventory where policy allows,
- deterministic parse results,
- embeddings by content hash,
- summaries by content hash + prompt version + model family,
- LLM calls by canonical request hash when safe,
- retrieval results for repeated questions,
- provider/model capability metadata.

Never cache:
- secret-bearing raw prompts without encryption,
- cross-session private ZIP data into a globally reusable cache.

---

# 27. Observability

Use OpenTelemetry across API, worker, retrieval, graph, LLM, and export.

Trace example:

```text
project.analysis
├── ingest
├── security_scan
├── inventory
├── parse
├── build_ir
├── neo4j.write
├── index.bm25
├── index.qdrant
├── summarize
├── subsystem.discovery
├── deep_agent.architecture
│   ├── tool.search_hybrid
│   ├── tool.query_graph
│   └── llm.generate
├── guide.plan
├── guide.section.generate
├── guide.section.verify
└── finalize
```

Metrics:
- stage duration,
- files parsed/sec,
- graph nodes/edges,
- index size,
- retrieval latency,
- reranker latency,
- LLM latency,
- token counts,
- 429 count,
- circuit-breaker state,
- tool calls per agent,
- eval scores.

Structured logs must carry:
- request_id,
- project_id,
- job_id,
- trace_id,
- stage,
- provider/model where relevant.

Never log API keys or detected repository secrets.

---

# 28. Evaluation System

## 28.1 Evaluation frameworks

### DeepEval
Primary CI-focused system for:
- RAG,
- generation,
- chatbot,
- agent trajectories,
- tool use,
- end-to-end regression tests.

### RAGAS
Complementary experimentation for:
- context precision,
- context recall,
- faithfulness,
- response relevancy,
- factual correctness,
- tool call/agent metrics where useful.

### Custom deterministic evals
Required for:
- retrieval gold labels,
- graph path correctness,
- symbol/file recall,
- unsupported claim rate,
- diagram structural validity.

---

# 29. Golden Evaluation Dataset

Create a curated benchmark repository set covering:
- small Python API,
- React + FastAPI full stack,
- Java/Spring backend,
- Node/TypeScript service,
- ML repository,
- RAG repository,
- infrastructure/Terraform repository,
- multi-service project.

For every repo maintain:
- expected project type,
- expected major subsystems,
- gold architecture relationships,
- representative questions,
- gold relevant files/symbols,
- gold graph paths,
- expected answer facts.

Version the benchmark in Git.

---

# 30. CI Evaluation Gates

### Every PR
- lint,
- type check,
- unit tests,
- parser fixtures,
- graph construction fixtures,
- retrieval deterministic tests,
- security tests,
- small offline eval smoke suite.

### Scheduled/manual online eval
Uses Gemini/Groq secrets:
- representative RAG samples,
- generation faithfulness,
- agent trajectories,
- guide quality regression.

### Release gate
A release must not regress beyond configured thresholds for:
- Recall@K,
- MRR,
- faithfulness,
- unsupported claims,
- task completion.

Thresholds are established from baseline data, not invented before measurement.

---

# 31. Security Engineering

## 31.1 Trust boundaries

Trust zones:
1. User input
2. Repository content
3. Parser output
4. Project IR
5. AI context
6. Generated output

Raw repository content is always untrusted.

## 31.2 Secret detection
Before content leaves infrastructure for an external LLM:
- scan for credential-like material,
- redact,
- record redaction metadata.

## 31.3 Agent tools
Agents receive only project-scoped read tools.
No general shell.
No arbitrary network fetch.
No repository execution.

## 31.4 Prompt injection
Guard pipeline:
- rule-based suspicious-instruction scanner,
- optional local/provider prompt guard,
- context tagging,
- explicit system policy,
- output evidence verification.

---

# 32. Dockerization

Images:
- `unfold-api`
- `unfold-worker`
- `unfold-web`

Local Docker Compose:
- web
- api
- worker
- postgres
- redis
- neo4j
- qdrant
- minio
- optional telemetry collector

Workers may use the same image with queue-specific commands.

---

# 33. GitHub Actions

Suggested workflows:

```text
ci.yml
security.yml
eval-offline.yml
eval-online.yml
docker.yml
terraform.yml
release.yml
```

### ci.yml
- Python lint/format
- mypy/pyright
- pytest
- frontend lint/typecheck/test
- build frontend
- Docker build smoke

### security.yml
- dependency audit
- secret scan
- container scan
- Terraform static analysis

### eval-offline.yml
- deterministic evaluation suite

### eval-online.yml
- manual/scheduled,
- uses protected secrets,
- bounded budget,
- publishes report artifact.

### terraform.yml
- fmt
- validate
- plan for target environment when configuration exists.

---

# 34. Terraform

From day one, infrastructure definitions live under `infra/terraform`.

Required abstractions:
- network module,
- compute/API module,
- worker compute module,
- PostgreSQL module,
- Redis module,
- Neo4j deployment module or managed endpoint abstraction,
- Qdrant deployment module or managed endpoint abstraction,
- object storage module,
- secret management module,
- observability module,
- IAM/service-account module.

Actual GCP product choices are intentionally deferred.

Do not bake AWS assumptions into application code.

---

# 35. Configuration

Use environment-based typed settings.

Representative variables:

```text
APP_ENV
DATABASE_URL
REDIS_URL
NEO4J_URI
NEO4J_USERNAME
NEO4J_PASSWORD
QDRANT_URL
OBJECT_STORE_ENDPOINT
OBJECT_STORE_BUCKET

GEMINI_API_KEY_1
GEMINI_API_KEY_2
GEMINI_API_KEY_3
GEMINI_API_KEY_4

GROQ_API_KEY_1
GROQ_API_KEY_2
GROQ_API_KEY_3
GROQ_API_KEY_4

EMBEDDING_MODEL
RERANKER_MODEL

SESSION_TTL_SECONDS
MAX_ZIP_BYTES
MAX_EXTRACTED_BYTES
MAX_FILE_COUNT
MAX_SINGLE_FILE_BYTES
```

Never commit real secrets.

---

# 36. Testing Strategy

### Unit tests
- parsers,
- Project IR,
- RRF,
- ranking,
- context compiler,
- model router,
- quota logic,
- claim verifier.

### Integration tests
- Postgres,
- Neo4j,
- Qdrant,
- Redis,
- object store,
- worker flow.

### Contract tests
- Gemini/Groq adapters via mocked fixtures,
- API schemas,
- frontend/backend contracts.

### Security tests
- zip traversal,
- symlink,
- zip bomb,
- prompt injection samples,
- secret redaction,
- malformed repositories.

### End-to-end tests
Fixture repository:
- upload → analyze → guide → chat → PDF.

---

# 37. Performance Engineering

### Parallelizable work
- file parsing,
- embedding batches,
- subsystem analysis,
- deep-agent investigations,
- guide section generation.

### Backpressure
Every expensive stage must respect:
- worker concurrency,
- provider capacity,
- model token limits,
- memory capacity,
- project priority.

### Avoid
- loading entire repository into one prompt,
- embedding vendor/generated files,
- rebuilding unchanged embeddings,
- serial LLM calls where safe parallelism exists,
- unlimited agent loops.

---

# 38. Cleanup and TTL

A scheduled cleanup job:
1. finds expired sessions,
2. marks session deleting,
3. deletes object storage artifacts,
4. deletes Qdrant project points,
5. deletes Neo4j project subgraph,
6. deletes Redis state,
7. deletes or tombstones PostgreSQL rows,
8. confirms completion.

Cleanup is idempotent.

---

# 39. Versioning

Version independently:
- parser version,
- Project IR schema version,
- graph schema version,
- chunker version,
- embedding model,
- retrieval config,
- prompt versions,
- guide planner version,
- evaluation dataset version.

Every guide should be reproducible enough to identify the configuration that produced it.

---

# 40. Engineering Definition of Done

A feature is complete only when it has:
- implementation,
- typed interfaces,
- unit tests,
- error handling,
- observability,
- security review where relevant,
- documentation,
- evaluation coverage for AI behavior where relevant,
- CI validation,
- Docker compatibility.

“Works locally once” is not an acceptable completion criterion.
