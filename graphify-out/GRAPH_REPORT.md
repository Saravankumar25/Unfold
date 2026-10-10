# Graph Report - unfold  (2026-10-10)

## Corpus Check
- 308 files · ~228,925 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 22 file(s) not represented in the graph (top: (none) 8, .example 3, .j2 3)

## Summary
- 6977 nodes · 21205 edges · 282 communities (227 shown, 55 thin omitted)
- Extraction: 86% EXTRACTED · 14% INFERRED · 0% AMBIGUOUS · INFERRED: 2960 edges (avg confidence: 0.88)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `1a75c36f`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- mermaid.min.js
- yct
- t
- push
- UnfoldError
- gZe
- gemini.py
- i
- hybrid.py
- builders.py
- Settings
- GraphQueries
- InventoryFile
- consume
- ProjectIR
- _Builder
- project_ir/builder.py
- projects.py
- ErrorCode
- indexing/chunking.py
- AI_infrastructure.md
- api/types.ts
- LLMRequest
- analyzers/base.py
- BlobStore
- concat
- Chunk
- uze
- peekChar
- .toString
- h
- subrule
- Node
- .constructor
- langchain_core_callbacks
- integration/conftest.py
- rgb
- upload_document
- setAttribute
- Cc
- guide-page.tsx
- api/schemas.py
- compiler.py
- treesitter.py
- test_retrieval_planner.py
- render.py
- uN
- Store
- edges
- sd
- validateDocument
- VectorStore
- join
- architecture.md
- frontend/package.json
- g3
- ESD.md
- communities.py
- Embedder
- inventory.py
- planner.py
- ingestion.py
- _Extractor
- node
- edge
- KeyPool
- web/package.json
- link
- lib/api/client.ts
- test_indexing_chunking.py
- addRelation
- enableRecording
- Ug
- code-view.tsx
- users.ts
- compilerOptions
- setData
- Do
- constructor
- Unfold — Low-Level Design (LLD)
- _JsExtractor
- nodes
- manyInternal
- postgres/models.py
- use-chat-thread.ts
- test_llm_gateway_policy.py
- test_security_hardening.py
- ProviderBudget
- InputError
- gateway.py
- documents.py
- enums.py
- lex
- is
- rules.py
- README_original.md
- ref_react
- devDependencies
- diagram-layout.ts
- register
- safe_extract
- plan_query
- api/main.py
- guide/models.py
- _Extractor
- complexity.py
- CircuitBreaker
- Unfold — High-Level Design (HLD)
- route.ts
- use-project-progress.ts
- pdf.py
- rrf_fuse
- extend
- bn
- DenseIndex
- 4.1 PostgreSQL — operational system of record
- express_ts_prisma/package.json
- authService.ts
- WorkerSettings
- Jg
- LA
- agents/models.py
- sufficiency.py
- export-button.tsx
- Unfold — Product Requirements Document (PRD)
- app.ts
- postService.ts
- llm.py
- Nmt
- addDetails
- e7t
- a7t
- sessions.py
- children
- vercel.json
- dependencies
- Zk
- Runtime
- test_architecture.py
- destructEndLink
- hasNode
- match
- recursiveReduce
- atLeastOneInternal
- optionInternal
- .write
- Technology Stack
- compilerOptions
- split_text
- PROJECT_AUDIT.md — Unfold, Phase 0
- Le
- bezierCurveTo
- 3. Product Principles
- 8. Project Guide Requirements
- devDependencies
- count
- calculateHorizontalSpace
- initialized
- HLD.md
- 46. Guide Evaluation
- 33. Key Architectural Decisions
- dependencies
- compilerOptions
- retriever.py
- inline-text.tsx
- cje
- successors
- ADR 0001: Drop LangGraph, Deep Agents and the LangChain adapter
- yB
- Wot
- 30. CI Evaluation Gates
- test_api.py
- GYe
- YYe
- 8.1 Graph layers
- 24. API Surface
- gje
- fve
- jYe
- Ult
- i0
- VYe
- setup.ts
- langchain_core_language_models
- 28. Scalability Strategy
- 4. Why Multiple Data Stores Are Justified
- 16. Deep Agents
- 7. Primary User Journey
- scripts
- langchain_core_messages
- langchain_core_outputs
- cn
- a1e
- C4
- langchain_core_runnables
- linkNode
- langchain_core_tools
- langchain_core_utils_function_calling
- 30. Failure and Recovery
- 3. Architectural Boundaries
- 33. GitHub Actions
- 36. Testing Strategy
- 7. Repository Inventory
- 8.2 Language-aware analyzers
- 11. Interview and Viva Preparation
- 20. Non-Functional Requirements
- 4. Primary Users
- Local Development
- DocQA — retrieval-augmented question answering over your documents
- expiry-notice.tsx
- 10. Request Priority
- 57. Model Assignment Strategy
- 6.1 Repository Intelligence Layer
- 9. Graph Query Patterns
- 28.1 Evaluation frameworks
- 31. Security Engineering
- 18. Evaluation Requirements
- 19. Security Requirements
- acquireParserWorker
- k
- isSubtype
- q9
- 15. Reciprocal Rank Fusion
- 34. Technical Depth Personalization
- 50. Evaluation in CI/CD
- 37. Performance Engineering
- 16. Agentic Requirements
- 22. Success Metrics
- get_settings
- shortlink-platform
- eslint.config.mjs
- vitest.config.ts
- collectValues
- indexNodes2
- GH
- Nj
- 20. Hierarchical Summarization
- 21. Retrieval Planner
- 10. Chatbot Requirements
- 12. Export Requirements
- 5. Supported Inputs
- schema.sql
- toast.tsx
- getAstNodePath
- r9e
- VENDORED.md
- ingestion/__init__.py
- persistence/__init__.py
- 4. Model Strategy
- repos/README.md
- AGENTS.md
- postcss.config.mjs
- docqa
- github.com/example/shortlink
- unfold

## God Nodes (most connected - your core abstractions)
1. `push()` - 407 edges
2. `t()` - 303 edges
3. `r()` - 288 edges
4. `s()` - 269 edges
5. `n()` - 229 edges
6. `i()` - 203 edges
7. `e()` - 180 edges
8. `a()` - 166 edges
9. `get()` - 136 edges
10. `o()` - 133 edges

## Surprising Connections (you probably didn't know these)
- `4. End-to-end analysis flow` --references--> `JobStage`  [INFERRED]
  docs/HLD.md → backend/src/unfold/domain/enums.py
- `5. Key design decisions (as built)` --references--> `ast()`  [INFERRED]
  docs/HLD.md → backend/src/unfold/exports/static/mermaid.min.js
- `Python` --references--> `ast()`  [INFERRED]
  docs/specs/ESD.md → backend/src/unfold/exports/static/mermaid.min.js
- `11. Git worktree, RAM, disk and workspace plan` --references--> `node()`  [INFERRED]
  PROJECT_AUDIT.md → backend/src/unfold/exports/static/mermaid.min.js
- `6. Ingestion & security (`unfold.ingestion`, `unfold.security`)` --references--> `safe_extract()`  [INFERRED]
  docs/LLD.md → backend/src/unfold/ingestion/archive.py

## Import Cycles
- 1-file cycle: `backend/src/unfold/exports/render.py -> backend/src/unfold/exports/render.py`

## Communities (282 total, 55 thin omitted)

### Community 0 - "mermaid.min.js"
Cohesion: 0.00
Nodes (307): _2(), addAll(), addAttributes(), addCssStyles(), addEdge(), addEntity(), addNamespace(), addPipelineComponent() (+299 more)

### Community 1 - "yct"
Cohesion: 0.33
Nodes (5): e2e(), Jbe(), Kf(), yct(), 8. Testing and production readiness

### Community 2 - "t"
Cohesion: 0.01
Nodes (302): $2e(), i(), a6t(), ACTION(), addAnnotation(), aDe(), adt(), aEe() (+294 more)

### Community 3 - "push"
Cohesion: 0.02
Nodes (206): $7(), A2(), a5(), a8(), a9e(), add(), addActor(), addClass() (+198 more)

### Community 4 - "UnfoldError"
Cohesion: 0.04
Nodes (44): Conflict, GenerationError, NotFound, ProviderCapacityError, Any, Exception, Unified error model (ESD §25). Every failure that reaches a user carries an…, Base error. `http_status` is used by the API; `failure_stage` by the job runner. (+36 more)

### Community 5 - "gZe"
Cohesion: 0.07
Nodes (94): b(), c1e(), DL(), g1e(), gte(), gZe(), b(), Be() (+86 more)

### Community 6 - "gemini.py"
Cohesion: 0.05
Nodes (62): APIError, ModelCapability, _convert(), Any, JSON-Schema normalisation for provider structured output / tool parameters (LLD…, Return a self-contained (no `$ref`) schema restricted to the portable keyword…, _resolve_ref(), to_provider_schema() (+54 more)

### Community 7 - "i"
Cohesion: 0.03
Nodes (140): aa(), addLink(), addNode(), addRelationship(), f(), aLe(), _at(), aUe() (+132 more)

### Community 8 - "hybrid.py"
Cohesion: 0.11
Nodes (30): Every first-stage retriever failed — there is no honest evidence to return., RetrievalFailed, HybridRetriever, _ms(), _node_ref(), BaseException, Hybrid retrieval orchestration (LLD §11, ESD §12, AI infra §15-§24). ``` plan…, Run the applicable retrievers concurrently; returns (attempted, failed). (+22 more)

### Community 9 - "builders.py"
Cohesion: 0.07
Nodes (62): _agg_label(), ai_pipeline(), architecture(), build_all(), data_model(), deployment(), _EdgeAgg, _field_line() (+54 more)

### Community 10 - "Settings"
Cohesion: 0.05
Nodes (34): BaseSettings, SecretStr, Refuse to start in production with dev defaults (audit S-3)., Settings, _trusts_everyone(), cmd_models(), describe(), main() (+26 more)

### Community 11 - "GraphQueries"
Cohesion: 0.08
Nodes (39): _batches(), GraphBuilder, T, Subsystem/Concept nodes + PART_OF (etc.) edges. Never overwrites deterministic…, Set `subsystem_id` on entity nodes (entity uid → subsystem id)., check_label(), check_rel_type(), DegreeNode (+31 more)

### Community 12 - "InventoryFile"
Cohesion: 0.08
Nodes (22): LanguageAnalyzer, Protocol, One analyzer: decides which inventory files it handles and extracts facts from…, PythonAnalyzer, `LanguageAnalyzer` for `.py`/`.pyi` files., _grammar_for(), _installed_grammars(), _is_prisma() (+14 more)

### Community 13 - "consume"
Cohesion: 0.09
Nodes (46): callFunction(), consume(), consumeArg(), consumeArgs(), consumeSpaces(), countExpansion(), expandAfterFuture(), expandMacro() (+38 more)

### Community 14 - "ProjectIR"
Cohesion: 0.13
Nodes (36): build_ir(), Normalize one repository's inventory + analysis into a `ProjectIR` (pure,…, Referential-integrity problems of an IR (empty list = valid). Used by tests and…, validate_ir(), ProjectIR, All entities by id (files, symbols, modules, endpoints, ...). Built on demand;…, _build(), _fid() (+28 more)

### Community 15 - "_Builder"
Cohesion: 0.11
Nodes (18): _basename(), _Builder, _canon_tech(), _dirname(), _is_code(), _is_dockerfile(), _join(), _norm_name() (+10 more)

### Community 16 - "project_ir/builder.py"
Cohesion: 0.13
Nodes (37): Provenance, Where a fact came from. Deterministic facts outrank inferred ones everywhere…, _image_rule(), _join_route(), normalize_route(), Project IR builder (LLD §8, ESD §9): `Inventory` + `RepositoryAnalysis` →…, `/api//users/` → `/api/users`; empty → `/`. Path parameters are kept as written., AIComponentIR (+29 more)

### Community 17 - "projects.py"
Cohesion: 0.07
Nodes (59): client_ip(), CurrentSession, enforce_ip_limits(), enforce_rate_limit(), get_runtime(), owned_chat(), owned_project(), parse_uuid() (+51 more)

### Community 18 - "ErrorCode"
Cohesion: 0.17
Nodes (26): ErrorCode, StrEnum, SecurityRejection, _Budget, _common_root(), _copy_limited(), detect_format(), _extract_tar() (+18 more)

### Community 19 - "indexing/chunking.py"
Cohesion: 0.14
Nodes (32): content_hash(), chunk_file(), _config_units(), content_type_for(), _dockerfile_units(), _ext(), _fan_in(), _gap_units() (+24 more)

### Community 20 - "AI_infrastructure.md"
Cohesion: 0.04
Nodes (53): 11. Local Embeddings, 12. Qdrant Vector Layer, 13. Native Lexical Retrieval, 14. Native Hybrid RAG, 16. Cross-Encoder Reranking, 17. Structural Retrieval, 18. Neo4j Graph Intelligence, 19. Graph Communities (+45 more)

### Community 21 - "api/types.ts"
Cohesion: 0.04
Nodes (57): ProjectContextValue, SessionContext, SessionContextValue, ArchitectureOut, ArtifactKind, ArtifactStatus, Block, BlockType (+49 more)

### Community 22 - "LLMRequest"
Cohesion: 0.05
Nodes (45): Architecture rules (enforced by `tests/unit/test_architecture.py`), backend/ — agent operating manual, Commands (run from `backend/`), Non-negotiables, ProviderError, cache_key(), Any, ResponseCache (+37 more)

### Community 23 - "analyzers/base.py"
Cohesion: 0.07
Nodes (27): AnalysisContext, build_context(), _directories(), find_line(), _go_modules(), _is_under(), line_of(), load_jsonc() (+19 more)

### Community 24 - "BlobStore"
Cohesion: 0.05
Nodes (24): embedder_for(), Process-wide embedder singleton kept in `Runtime.extras`., BM25Index, lexical_key(), LexicalArtifact, LexicalIndexCache, Any, Builder for `LexicalArtifact`s (tokenizer: `indexing.tokenize`). (+16 more)

### Community 25 - "concat"
Cohesion: 0.03
Nodes (88): accept(), AS(), b5e(), buildEmptyAlternationError(), buildEmptyRepetitionError(), buildKeywordPattern(), buildKeywordToken(), buildLeftRecursionError() (+80 more)

### Community 26 - "Chunk"
Cohesion: 0.15
Nodes (16): ContentType, Chunk, BaseModel, Chunk = the unit of retrieval and of evidence (LLD §10). Persisted in `chunks`,…, ChunkRepository, _from_row(), _pid(), Any (+8 more)

### Community 27 - "uze"
Cohesion: 0.50
Nodes (4): cze(), lze(), oze(), uze()

### Community 28 - "peekChar"
Cohesion: 0.11
Nodes (47): ake(), alternative(), assertion(), atom(), atomEscape(), characterClass(), characterClassEscape(), classAtom() (+39 more)

### Community 29 - ".toString"
Cohesion: 0.03
Nodes (97): _5t(), activationCount(), addBox(), addDocument(), addExportedSymbol(), addLocalSymbol(), addMessage(), addSignal() (+89 more)

### Community 30 - "h"
Cohesion: 0.06
Nodes (82): Ab(), aE(), d(), an(), arc(), Ba(), blt(), calculateSpace() (+74 more)

### Community 31 - "subrule"
Cohesion: 0.07
Nodes (34): after(), before(), computeRuleType(), construct(), constructInfix(), cstPostNonTerminal(), Cv(), DEFINE_RULE() (+26 more)

### Community 32 - "Node"
Cohesion: 0.07
Nodes (17): _camelize(), _JavaExtractor, _join_path(), _JvmMixin, _KotlinExtractor, _PhpExtractor, Route/entity helpers shared by the Java and Kotlin extractors (annotation-…, (prefix or None when not a controller, framework). (+9 more)

### Community 33 - ".constructor"
Cohesion: 0.05
Nodes (38): _4e(), bh(), brt(), copy(), d4e(), finalize(), g4e(), Grt() (+30 more)

### Community 35 - "integration/conftest.py"
Cohesion: 0.11
Nodes (21): argparse, asyncpg, Tests, _ensure_database(), migrated_database(), project_id(), fixture, Integration fixtures: real data stores from `docker compose up -d` (ports from… (+13 more)

### Community 36 - "rgb"
Cohesion: 0.07
Nodes (41): Alt(), b8(), Bee(), brighter(), clamp(), D8(), darker(), displayable() (+33 more)

### Community 37 - "upload_document"
Cohesion: 0.15
Nodes (14): delete_document(), get_document(), list_documents(), ArqRedis, AsyncSession, delete, get, post (+6 more)

### Community 38 - "setAttribute"
Cohesion: 0.11
Nodes (33): arcTo(), closePath(), _drawToContext(), fillSketch(), _He(), iHe(), Kpt(), lineTo() (+25 more)

### Community 39 - "Cc"
Cohesion: 0.06
Nodes (37): bv(), Cc(), createDescription(), createDescriptions(), createInfoDescriptions(), createScope(), documentationLinkRenderer(), documentationTagRenderer() (+29 more)

### Community 40 - "guide-page.tsx"
Cohesion: 0.06
Nodes (55): ArchitecturePage(), ArchitectureWorkspace(), ChatPage(), ChatWorkspace(), Conversation(), prompts, ErrorPanel(), GuidePage() (+47 more)

### Community 41 - "api/schemas.py"
Cohesion: 0.12
Nodes (32): ArchitectureOut, ArtifactOut, ChatDetailOut, ChatEvent, ChatMessageOut, ChatSessionOut, CreateGithubProject, DiagramOut (+24 more)

### Community 42 - "compiler.py"
Cohesion: 0.07
Nodes (52): estimate_tokens(), Token estimation + budgeting for compiled contexts (LLD §12). Tokens ≈…, Cut `text` at a line boundary so that result (incl. marker) fits `max_tokens`.…, A running token allowance. `take` succeeds only when the item fits entirely., Account for mandatory content (objective) even if it overflows., TokenBudget, truncate_to_tokens(), _clean_summary() (+44 more)

### Community 43 - "treesitter.py"
Cohesion: 0.12
Nodes (35): Python analyzer on the stdlib `ast` (LLD §7, ESD §8.2). Parses text only —…, _analyze_prisma(), _clean_comment(), Tree-sitter analyzer for non-Python source languages (LLD §7, ESD §8.1–§8.2).…, _strip_prisma_comment(), AIHint, Binding, CallRef (+27 more)

### Community 44 - "test_retrieval_planner.py"
Cohesion: 0.12
Nodes (26): chunk_id(), diagram_id(), _digest(), entity_id(), new_uuid(), UUID, Stable, content/context-derived identifiers (LLD §3)., Deterministic id for an IR/graph entity: recomputation yields the same id… (+18 more)

### Community 45 - "render.py"
Cohesion: 0.11
Nodes (31): DiagramExport, ExportChatMessage, ExportProject, BaseModel, Inputs for the export templates (LLD §19). The worker maps persisted rows onto…, _claim_rows(), _clean(), _code_caption() (+23 more)

### Community 46 - "uN"
Cohesion: 0.06
Nodes (23): Ane(), bje(), Dne(), Ene(), FYe(), Ine(), kne(), Lne() (+15 more)

### Community 47 - "Store"
Cohesion: 0.08
Nodes (26): New(), Connect(), Store, newCode(), main(), TestHealth(), go_pkg_context, go_pkg_crypto_rand (+18 more)

### Community 48 - "edges"
Cohesion: 0.16
Nodes (23): Bge(), Bot(), C0e(), Dge(), E9(), edges(), fT(), Gge() (+15 more)

### Community 49 - "sd"
Cohesion: 0.16
Nodes (29): aD(), Ax(), bd(), cH(), d_e(), eD(), eGe(), f_e() (+21 more)

### Community 50 - "validateDocument"
Cohesion: 0.07
Nodes (32): buildLeafNode(), cancelWrite(), dequeue(), enqueue(), getSource(), handleException(), Ige(), MH() (+24 more)

### Community 51 - "VectorStore"
Cohesion: 0.14
Nodes (7): AsyncSession, AsyncSession, Create the collection if it does not exist., Insert or replace chunk vectors., Return (chunk id, score) pairs for the nearest vectors., Stores and searches chunk embeddings in a Qdrant collection., VectorStore

### Community 52 - "join"
Cohesion: 0.06
Nodes (117): _5e(), A4(), a$e(), ac(), alternatives(), Ar(), b4(), beginGroup() (+109 more)

### Community 53 - "architecture.md"
Cohesion: 0.05
Nodes (36): 10. Retrieval Architecture, 11.1 Guide mode, 11.2 Chat mode, 11.3 Deep investigation mode, 11. Retrieval Modes, 12.1 Token budgeting, 12. Context Engineering Architecture, 13. LangGraph HLD (+28 more)

### Community 54 - "frontend/package.json"
Cohesion: 0.06
Nodes (33): engines, node, react, react-dom, @types/react, @types/react-dom, typescript, @vitejs/plugin-react (+25 more)

### Community 55 - "g3"
Cohesion: 0.19
Nodes (21): aot(), e0e(), Fge(), filterNodes(), a(), fot(), g3(), isMultigraph() (+13 more)

### Community 56 - "ESD.md"
Cohesion: 0.05
Nodes (42): 10. Graph Builder, 11.1 Code-aware chunks, 11.2 Hierarchical summaries, 11. Chunking and Indexing, 12. Native Hybrid RAG, 13. Query Planner, 14. Context Compiler, 15. LangGraph Orchestration (+34 more)

### Community 57 - "communities.py"
Cohesion: 0.14
Nodes (24): _add_weight(), _build_graph(), detect(), _dir(), entity_subsystems(), fan_in(), _label_hint(), _merge_small() (+16 more)

### Community 58 - "Embedder"
Cohesion: 0.13
Nodes (12): Embedder, Any, NDArray, ModelUnavailable, A local model (embedder / cross-encoder) could not be loaded or failed at…, CrossEncoderReranker, Any, Relevance logits aligned with `texts` (higher = more relevant). (+4 more)

### Community 59 - "inventory.py"
Cohesion: 0.14
Nodes (25): build_inventory(), decode_text(), _describe(), _ext(), _is_build_output_dir(), _is_entrypoint(), _is_text(), language_for() (+17 more)

### Community 60 - "planner.py"
Cohesion: 0.07
Nodes (38): RetrievalIntent, RetrievalMode, default_breadth(), Breadth, Versioned retrieval configuration (LLD §11, ESD §12 "Reciprocal Rank Fusion",…, RetrievalConfig, SourceWeights, SubsystemLike (+30 more)

### Community 61 - "ingestion.py"
Cohesion: 0.09
Nodes (23): datetime, Base, Document, DeclarativeBase, QueryLog, SQLAlchemy ORM models (PostgreSQL)., An uploaded source document., Audit trail of questions asked. (+15 more)

### Community 62 - "_Extractor"
Cohesion: 0.08
Nodes (31): AnnAssign, Assign, AST, AsyncFunctionDef, AugAssign, _annotation_type(), _class_assign(), _const_str() (+23 more)

### Community 63 - "node"
Cohesion: 0.13
Nodes (25): i(), n(), c8(), e8(), got(), iot(), n(), k9() (+17 more)

### Community 64 - "edge"
Cohesion: 0.10
Nodes (29): ast(), b9(), cot(), d(), h(), u(), cT(), d0e() (+21 more)

### Community 65 - "KeyPool"
Cohesion: 0.20
Nodes (9): KeyPool, Any, Redis, Per-key health (index only, never key material)., Least-recently-used healthy key index, or None when every key is disabled or…, Mark a 429. `cooldown_s` is applied to this key only when quotas are per key., Credential rejected: take it out of rotation (auto re-enabled after…, T (+1 more)

### Community 66 - "web/package.json"
Cohesion: 0.08
Nodes (24): dependencies, react, react-dom, devDependencies, @types/react, @types/react-dom, typescript, vite (+16 more)

### Community 67 - "link"
Cohesion: 0.07
Nodes (35): allElements(), u(), code(), codespan(), del(), dR(), eu(), flat() (+27 more)

### Community 68 - "lib/api/client.ts"
Cohesion: 0.10
Nodes (20): API_PREFIX, apiClient, ApiClientOptions, ApiError, FetchLike, isErrorEnvelope(), networkError(), RequestOptions (+12 more)

### Community 69 - "test_indexing_chunking.py"
Cohesion: 0.38
Nodes (12): chunk_project(), Chunks for every indexable file of the IR whose text is available (`path →…, _by(), _file(), _ir(), _sym(), test_compose_services_split(), test_deterministic_ids_and_hashes() (+4 more)

### Community 70 - "addRelation"
Cohesion: 0.08
Nodes (37): addClassesToNamespace(), addDescription(), addInterface(), addRelation(), addRelationObjs(), addSingleLink(), addState(), addStyleClass() (+29 more)

### Community 71 - "enableRecording"
Cohesion: 0.11
Nodes (23): atLeastOneInternalRecord(), atLeastOneSepFirstInternalRecord(), Bq(), computeLookaheadFunc(), consumeInternalRecord(), disableRecording(), Dq(), enableRecording() (+15 more)

### Community 72 - "Ug"
Cohesion: 0.22
Nodes (9): buildAlternationAmbiguityError(), buildAlternationPrefixAmbiguityError(), buildEarlyExitMessage(), buildMismatchTokenMessage(), e4(), eae(), Kae(), pAe() (+1 more)

### Community 73 - "code-view.tsx"
Cohesion: 0.14
Nodes (19): TrustedHtml(), CodeView(), ALIASES, getHighlighter(), highlightToHtml(), normalizeLanguage(), THEMES, getMermaid() (+11 more)

### Community 74 - "users.ts"
Cohesion: 0.17
Nodes (18): requireAuth(), requireRole(), router, addComment(), createPost(), getPost(), router, getMe() (+10 more)

### Community 75 - "compilerOptions"
Cohesion: 0.09
Nodes (22): compilerOptions, allowJs, esModuleInterop, incremental, isolatedModules, jsx, lib, module (+14 more)

### Community 76 - "setData"
Cohesion: 0.13
Nodes (22): _8e(), A8e(), B5t(), c5t(), d5t(), f5t(), h5t(), hd() (+14 more)

### Community 77 - "Do"
Cohesion: 0.15
Nodes (15): Bit(), bX(), Do(), fX(), HBe(), hIt(), i_e(), mIt() (+7 more)

### Community 78 - "constructor"
Cohesion: 0.03
Nodes (71): a5e(), aAe(), addElement(), addRequirement(), a(), awaitBuilderState(), awaitDocumentState(), buildNamespaceConflictError() (+63 more)

### Community 79 - "Unfold — Low-Level Design (LLD)"
Cohesion: 0.08
Nodes (28): _dump(), gen(), Any, current(), ProgressReporter, Any, AsyncSession, Record a failure. When the runner will retry, the job stays `queued` at its… (+20 more)

### Community 80 - "_JsExtractor"
Cohesion: 0.11
Nodes (7): add(), _JsExtractor, _npm_package(), (resolved repository path, external, npm package) for a module specifier., Handle `require("x")` / `import("x")`; returns True when `call` was an import., The function node of `fn`, `memo(fn)`, `React.forwardRef(fn)`, ... (None when…, For `router.route('/x').get(h).post(h2)` return ("router", "/x").

### Community 81 - "nodes"
Cohesion: 0.11
Nodes (21): $9(), A9(), dot(), F9(), jTe(), Kmt(), Lf(), lot() (+13 more)

### Community 82 - "manyInternal"
Cohesion: 0.06
Nodes (34): _8(), cM(), cq(), Cte(), Eb(), eUe(), fke(), getRule() (+26 more)

### Community 83 - "postgres/models.py"
Cohesion: 0.15
Nodes (23): AnalysisJobRow, ArtifactRow, Base, ChatMessageRow, ChatSessionRow, DiagramRow, EvalRunRow, FindingRow (+15 more)

### Community 84 - "use-chat-thread.ts"
Cohesion: 0.14
Nodes (26): EvidenceItem, parseJSON(), ChatEvent, ChatMessageOut, ChatMode, EvidenceRef, PostChatMessage, ActivityKind (+18 more)

### Community 85 - "test_llm_gateway_policy.py"
Cohesion: 0.10
Nodes (45): Priority, _all_text(), FakeProvider, _gemini(), _groq(), h(), Harness, _limits() (+37 more)

### Community 86 - "test_security_hardening.py"
Cohesion: 0.09
Nodes (23): lifespan(), Cap on concurrent progress streams per session and overall (audit S-5). In-…, One open stream. `release` is idempotent, so it can be wired to several cleanup…, StreamCap, StreamLease, redis_settings(), _get_ip(), _ip_app() (+15 more)

### Community 87 - "ProviderBudget"
Cohesion: 0.19
Nodes (8): ProviderBudget, Redis, One atomic attempt. Returns (reservation, wait_hint_seconds, reason)., Reserve capacity, waiting in ≤1 s steps up to the priority wait budget. None →…, The call reached the provider: keep the request counted, replace the token…, The call never reached the provider: undo the reservation entirely., Current usage for diagnostics/tests (does not prune expired entries)., Reservation

### Community 88 - "InputError"
Cohesion: 0.14
Nodes (18): IngestionError, InputError, ExtractionLimits, FetchedRepo, GitHubClient, GitHubRef, _guard_host(), parse_github_url() (+10 more)

### Community 89 - "gateway.py"
Cohesion: 0.06
Nodes (50): asyncio, do_run_migrations(), include_object(), Alembic async environment. URL comes from unfold Settings (DATABASE_URL), never…, run_async_migrations(), run_migrations_offline(), run_migrations_online(), Typed, environment-driven settings (LLD §2). One instance per process via… (+42 more)

### Community 90 - "documents.py"
Cohesion: 0.11
Nodes (24): arq, arq_connections, get_db(), get_queue(), ArqRedis, AsyncSession, Shared FastAPI dependencies., Yield a database session per request. (+16 more)

### Community 91 - "enums.py"
Cohesion: 0.14
Nodes (19): ArtifactKind, ArtifactStatus, check_transition(), ClaimKind, ClaimStatus, InputType, InvalidTransition, JobStage (+11 more)

### Community 92 - "lex"
Cohesion: 0.14
Nodes (13): bE(), blockTokens(), _getExpansion(), gT(), iE(), inlineTokens(), jM(), lex() (+5 more)

### Community 93 - "is"
Cohesion: 0.20
Nodes (12): br(), a(), o(), is(), I(), "../../node_modules/.pnpm/vscode-languageserver-protocol@3.17.5/node_modules/vscode-languageserver-protocol/lib/common/connection.js"(), r(), "../../node_modules/.pnpm/vscode-languageserver-protocol@3.17.5/node_modules/vscode-languageserver-protocol/lib/common/protocol.notebook.js"() (+4 more)

### Community 94 - "rules.py"
Cohesion: 0.14
Nodes (16): AIRule, canonical_dep_name(), dep_matches(), image_repository(), match_ai_import(), module_matches(), _r(), Versioned technology-detection rules (LLD §7). Pure data + tiny matchers;… (+8 more)

### Community 95 - "README_original.md"
Cohesion: 0.07
Nodes (26): Agents, Anonymous Sessions, CI/CD, Core Architecture, Documentation, Engineering Principle, Environment, Evaluation First (+18 more)

### Community 96 - "ref_react"
Cohesion: 0.14
Nodes (19): API, Blogpost, Data model, Development, createPost(), fetchPosts(), login(), Post (+11 more)

### Community 97 - "devDependencies"
Cohesion: 0.12
Nodes (17): devDependencies, eslint, eslint-config-next, @eslint/eslintrc, jsdom, tailwindcss, @tailwindcss/postcss, @testing-library/dom (+9 more)

### Community 98 - "diagram-layout.ts"
Cohesion: 0.12
Nodes (21): DiagramCanvas(), KIND_ACCENT, nodeTypes, UnfoldNode(), DiagramCanvas, DiagramEdge, DiagramKind, DiagramNode (+13 more)

### Community 99 - "register"
Cohesion: 0.27
Nodes (16): a(), addEntry(), $d(), Fv(), gv(), _M(), register(), UDe() (+8 more)

### Community 100 - "safe_extract"
Cohesion: 0.22
Nodes (23): Extract `archive` into `dest` (must be empty/new). Raises…, safe_extract(), _add(), parametrize, Path, _tar(), test_depth_limit(), test_empty_zip() (+15 more)

### Community 101 - "plan_query"
Cohesion: 0.27
Nodes (24): plan_query(), Breadth, Deterministic retrieval plan. `intent` overrides classification (guide sections…, assess(), follow_up_plan(), Any, query_keys(), Narrow plan targeting the gaps found by `assess`, or None when there is nothing… (+16 more)

### Community 102 - "api/main.py"
Cohesion: 0.07
Nodes (49): _envelope(), install_error_handling(), http_error(), unfold_error(), unhandled(), validation_error(), FastAPI, Request (+41 more)

### Community 103 - "guide/models.py"
Cohesion: 0.19
Nodes (18): BulletsBlock, CalloutBlock, CodeBlock, DiagramBlock, DraftClaim, GlossaryBlock, GlossaryItem, GuidePlan (+10 more)

### Community 104 - "_Extractor"
Cohesion: 0.09
Nodes (13): _CSharpExtractor, _Extractor, emit(), emit(), _go_local_name(), _go_package(), _GoExtractor, _is_pascal() (+5 more)

### Community 105 - "complexity.py"
Cohesion: 0.19
Nodes (16): AgentBudget, ComplexityProfile, ComplexitySignals, _investigators(), _lin(), _log_ratio(), BaseModel, Deterministic project complexity profile (LLD §8, AI infrastructure §31–§32).… (+8 more)

### Community 106 - "CircuitBreaker"
Cohesion: 0.19
Nodes (9): CircuitBreaker, CircuitState, Any, Redis, StrEnum, Circuit breaker per `provider:model`, shared across workers through Redis (LLD…, _text(), 13. LLM Gateway (`unfold.llm`) (+1 more)

### Community 107 - "Unfold — High-Level Design (HLD)"
Cohesion: 0.25
Nodes (8): 1. What the system does, 2. Deployable units (modular monolith + workers), 3. Logical modules (Python package `unfold`), 4. End-to-end analysis flow, 5. Key design decisions (as built), 6. Quality attributes → mechanisms, 7. Out of scope for this phase, Unfold — High-Level Design (HLD)

### Community 108 - "route.ts"
Cohesion: 0.12
Nodes (17): DELETE, dynamic, envelope(), GET, HEAD, HOP_BY_HOP, OPTIONS, PATCH (+9 more)

### Community 109 - "use-project-progress.ts"
Cohesion: 0.15
Nodes (16): ProgressContent(), ProjectProgressPage(), streamPaths, JobOut, ProgressEvent, Action, reducer(), useProjectProgress() (+8 more)

### Community 110 - "pdf.py"
Cohesion: 0.07
Nodes (32): ExportError, _block_network(), _csp(), _mermaid_source(), PdfRenderer, PdfResult, HTML → PDF with headless Chromium (LLD §19). Runs in the worker `export` queue.…, One Chromium per process, launched lazily on first use; each render gets a… (+24 more)

### Community 111 - "rrf_fuse"
Cohesion: 0.17
Nodes (17): FusedCandidate, max_fused_score(), merge_variants(), Weighted Reciprocal Rank Fusion (LLD §11, ESD §12). Pure and deterministic.…, Fuse ranked chunk-id lists. A ranking with weight ≤ 0 (or missing from…, Merge the rankings of several query variants run against one retriever into a…, Best achievable fused score (rank 1 in every positively weighted ranking)., rrf_fuse() (+9 more)

### Community 112 - "extend"
Cohesion: 0.18
Nodes (11): extend(), havingBaseSizing(), havingBaseStyle(), havingSize(), i(), withColor(), withFont(), withPhantom() (+3 more)

### Community 113 - "bn"
Cohesion: 0.11
Nodes (18): bn(), dje(), fje(), hje(), kje(), lje(), mje(), nE() (+10 more)

### Community 114 - "DenseIndex"
Cohesion: 0.16
Nodes (10): chunk_payload(), DenseIndex, embedding_cache_key(), point_id(), _project_filter(), Any, ndarray, Update `subsystem_id` payloads (chunk id → subsystem id) after subsystem… (+2 more)

### Community 115 - "4.1 PostgreSQL — operational system of record"
Cohesion: 0.14
Nodes (14): 4.1 PostgreSQL — operational system of record, 4.2 Neo4j — project knowledge graph, 4.3 Qdrant — dense semantic retrieval index, 4.4 Native BM25 index, 4.5 Redis, 4.6 Object storage, 4. Core Data Stores, analysis_jobs (+6 more)

### Community 116 - "express_ts_prisma/package.json"
Cohesion: 0.14
Nodes (13): description, typescript, main, name, private, version, jest, prisma (+5 more)

### Community 117 - "authService.ts"
Cohesion: 0.21
Nodes (11): HttpError, credentials, loginHandler(), registerHandler(), router, login(), register(), signToken() (+3 more)

### Community 118 - "WorkerSettings"
Cohesion: 0.50
Nodes (4): arq worker configuration., WorkerSettings, 12. Prioritized implementation roadmap, Not verified in this audit

### Community 119 - "Jg"
Cohesion: 0.04
Nodes (60): a6(), addAstNodeRegionWithAssignmentsTo(), assign(), assignWithoutOverride(), c6(), convert(), createDehyrationContext(), createGrammarElementIdMap() (+52 more)

### Community 120 - "LA"
Cohesion: 0.03
Nodes (110): addToResyncTokens(), AT_LEAST_ONE_SEP(), AT_LEAST_ONE_SEP1(), AT_LEAST_ONE_SEP2(), AT_LEAST_ONE_SEP3(), AT_LEAST_ONE_SEP4(), AT_LEAST_ONE_SEP5(), AT_LEAST_ONE_SEP6() (+102 more)

### Community 121 - "agents/models.py"
Cohesion: 0.33
Nodes (8): ComponentFinding, FindingFact, InvestigationFinding, BaseModel, Deep Agent output contracts (LLD §15). Findings feed the guide planner, section…, WorkflowFinding, WorkflowStep, 17. Diagrams (`unfold.diagrams`)

### Community 122 - "sufficiency.py"
Cohesion: 0.16
Nodes (18): _keep(), Code-aware tokenizer shared by BM25 indexing and querying (LLD §10, AI infra…, Tokens with repetition preserved (term frequency matters for documents)., Same transformation as documents, de-duplicated in first-seen order., _segment_tokens(), tokenize(), tokenize_query(), _identifiers() (+10 more)

### Community 123 - "export-button.tsx"
Cohesion: 0.17
Nodes (15): ExportButton(), ButtonProps, Size, sizes, Variant, variants, useToast(), isApiError() (+7 more)

### Community 124 - "Unfold — Product Requirements Document (PRD)"
Cohesion: 0.15
Nodes (13): 13. AI/Product Intelligence Requirements, 14. Retrieval Requirements, 15. Graph Intelligence Requirements, 17. Model Provider Constraints, 1. Product Vision, 21. Out of Scope for V1, 23. Acceptance Criteria for V1, 24. Product Positioning (+5 more)

### Community 125 - "app.ts"
Cohesion: 0.23
Nodes (8): createApp(), config, main(), errorHandler(), cors, dotenv, helmet, supertest

### Community 126 - "postService.ts"
Cohesion: 0.21
Nodes (9): prisma, listPosts(), addComment(), getPost(), listPublished(), PostNotFoundError, publicFields, CreatePostInput (+1 more)

### Community 127 - "llm.py"
Cohesion: 0.18
Nodes (13): answer_query(), AsyncSession, post, Retrieve relevant chunks with hybrid search and ask the LLM for a cited answer., Citation, Answer, LLMClient, LLM client that answers questions from retrieved context with citations. (+5 more)

### Community 128 - "Nmt"
Cohesion: 0.29
Nodes (7): bu(), M5(), Nmt(), pmt(), PTe(), vmt(), YF()

### Community 129 - "addDetails"
Cohesion: 0.43
Nodes (7): addALink(), addDetails(), addLinks(), addProperties(), getActor(), insertLinks(), insertProperties()

### Community 130 - "e7t"
Cohesion: 0.29
Nodes (7): b7t(), e7t(), eX(), i7t(), k7t(), l7t(), p7t()

### Community 131 - "a7t"
Cohesion: 0.33
Nodes (6): a7t(), d7t(), h7t(), n7t(), s7t(), u7t()

### Community 132 - "sessions.py"
Cohesion: 0.19
Nodes (15): create_session(), current_session(), get, post, Request, Response, RuntimeDep, SessionDep (+7 more)

### Community 133 - "children"
Cohesion: 0.15
Nodes (14): b0e(), cA(), children(), Hge(), hst(), r(), not(), n() (+6 more)

### Community 134 - "vercel.json"
Cohesion: 0.40
Nodes (4): buildCommand, framework, installCommand, $schema

### Community 135 - "dependencies"
Cohesion: 0.12
Nodes (16): dependencies, clsx, @dagrejs/dagre, dompurify, lucide-react, mermaid, motion, next (+8 more)

### Community 137 - "Runtime"
Cohesion: 0.04
Nodes (44): alembic, Job progress: durable stage on the job row, then an ordered replayable event in…, Neo4jStore, Any, Run a parameterised query and return records as dicts (reads and small writes)., Database, AsyncSession, Async engine + session factory (one per process). (+36 more)

### Community 138 - "test_architecture.py"
Cohesion: 0.24
Nodes (8): _modules(), _pkg(), Architecture rules from HLD §3 / backend/CLAUDE.md, enforced on the import…, module dotted name -> set of imported absolute module names., Analysis must never execute repository code (PRD §19)., test_core_and_domain_are_leaf_layers(), test_no_subprocess_or_eval_of_repository_content(), test_only_composition_roots_import_api_or_worker_entrypoints()

### Community 139 - "destructEndLink"
Cohesion: 0.50
Nodes (4): countChar(), destructEndLink(), destructLink(), destructStartLink()

### Community 140 - "hasNode"
Cohesion: 0.16
Nodes (15): G9(), hasNode(), hT(), r(), jst(), KFe(), Mst(), r() (+7 more)

### Community 141 - "match"
Cohesion: 0.07
Nodes (32): bqe(), buildUnableToPopLexerModeMessage(), buildUnexpectedCharactersMessage(), computeNewColumn(), Cqe(), createIndentationTokenInstance(), dedentMatcher(), EJ() (+24 more)

### Community 143 - "atLeastOneInternal"
Cohesion: 0.15
Nodes (13): AT_LEAST_ONE(), AT_LEAST_ONE1(), AT_LEAST_ONE2(), AT_LEAST_ONE3(), AT_LEAST_ONE4(), AT_LEAST_ONE5(), AT_LEAST_ONE6(), AT_LEAST_ONE7() (+5 more)

### Community 144 - "optionInternal"
Cohesion: 0.17
Nodes (12): option(), OPTION1(), OPTION2(), OPTION3(), OPTION4(), OPTION5(), OPTION6(), OPTION7() (+4 more)

### Community 145 - ".write"
Cohesion: 0.29
Nodes (10): _derived_relationships(), _ir_nodes(), add(), _node_props(), project_uid(), Any, (label, uid, props) for every IR entity that becomes a graph node., Structural edges implied by IR fields (containment, handler, schema symbol) —… (+2 more)

### Community 146 - "Technology Stack"
Cohesion: 0.18
Nodes (11): AI Orchestration, Backend, Code Intelligence, Data, Delivery, Evaluation, Frontend, Graph (+3 more)

### Community 147 - "compilerOptions"
Cohesion: 0.18
Nodes (10): compilerOptions, baseUrl, esModuleInterop, module, outDir, paths, rootDir, strict (+2 more)

### Community 148 - "split_text"
Cohesion: 0.29
Nodes (9): _make_chunk(), Split document text into overlapping chunks., Split text on paragraph boundaries into chunks of roughly `chunk_size`…, split_text(), TextChunk, Fuse several ranked id lists: score(d) = sum(1 / (k + rank))., reciprocal_rank_fusion(), test_rrf_prefers_items_ranked_by_both() (+1 more)

### Community 149 - "PROJECT_AUDIT.md — Unfold, Phase 0"
Cohesion: 0.18
Nodes (10): 10. Context-rot prevention strategy, 11. Git worktree, RAM, disk and workspace plan, 1. Project overview and actual architecture, 2. Feature matrix, 3. Functional and API audit, 5. AI, agents and RAG assessment, 6. Infrastructure and cloud-cost assessment, 7. Frontend and UX assessment (+2 more)

### Community 150 - "Le"
Cohesion: 0.19
Nodes (16): aq(), i6(), iq(), jh(), Kh(), Ks(), l6(), Le() (+8 more)

### Community 151 - "bezierCurveTo"
Cohesion: 0.22
Nodes (9): bezierCurveTo(), Ey(), ky(), M_t(), N_t(), NL(), P_t(), qb() (+1 more)

### Community 152 - "3. Product Principles"
Cohesion: 0.20
Nodes (10): 3.1 Repository → Understanding, 3.2 Evidence before explanation, 3.3 Breadth first, depth on demand, 3.4 Adaptive, not template-bound, 3.5 Visual learning is core, 3.6 User level matters, 3.7 Large repositories require selective completeness, 3.8 No permanent account dependency (+2 more)

### Community 153 - "8. Project Guide Requirements"
Cohesion: 0.20
Nodes (10): 8.1 Universal Backbone, 8.2 Dynamic Core Chapters, 8.3 Reading Length, 8.4 Explanation Depth, 8.5 Evidence, 8. Project Guide Requirements, DevOps project, Distributed backend (+2 more)

### Community 154 - "devDependencies"
Cohesion: 0.20
Nodes (10): devDependencies, jest, prisma, supertest, ts-jest, ts-node-dev, @types/express, @types/jest (+2 more)

### Community 155 - "count"
Cohesion: 0.33
Nodes (6): count(), HYe(), ije(), oB(), qYe(), tje()

### Community 156 - "calculateHorizontalSpace"
Cohesion: 0.18
Nodes (18): calculateHorizontalSpace(), calculateOffsetByRotation(), calculateSpaceIfDrawnHorizontally(), calculateSpaceIfDrawnVertical(), calculateVerticalSpace(), getDrawableElementsForBottomAxis(), getDrawableElementsForLeftAxis(), getDrawableElementsForTopAxis() (+10 more)

### Community 157 - "initialized"
Cohesion: 0.33
Nodes (6): getConfiguration(), initialized(), initializeWorkspace(), toSectionName(), updateConfiguration(), updateSectionConfiguration()

### Community 158 - "HLD.md"
Cohesion: 0.33
Nodes (3): 25. Guiding Product Test, Prepare your project., Unfold

### Community 159 - "46. Guide Evaluation"
Cohesion: 0.22
Nodes (9): 46. Guide Evaluation, Architecture coverage, Evidence support, Explanation coherence, Level appropriateness, Read-time adherence, Redundancy, Technology correctness (+1 more)

### Community 160 - "33. Key Architectural Decisions"
Cohesion: 0.22
Nodes (9): 33. Key Architectural Decisions, ADR-001 — Native Hybrid RAG, ADR-002 — Neo4j, ADR-003 — Qdrant, ADR-004 — LangGraph, ADR-005 — Deep Agents, ADR-006 — No CrewAI, ADR-007 — Free-tier LLMs (+1 more)

### Community 161 - "dependencies"
Cohesion: 0.22
Nodes (9): dependencies, bcryptjs, cors, dotenv, express, helmet, jsonwebtoken, @prisma/client (+1 more)

### Community 162 - "compilerOptions"
Cohesion: 0.22
Nodes (8): compilerOptions, baseUrl, jsx, module, moduleResolution, strict, target, include

### Community 163 - "retriever.py"
Cohesion: 0.18
Nodes (11): Chunk, A retrievable slice of a document., embed_query(), Embed a single query., HybridRetriever, Hybrid retrieval: BM25 lexical + dense vector search fused with Reciprocal Rank…, Combines BM25 over chunk text with Qdrant vector search., Rank chunks with BM25. (+3 more)

### Community 164 - "inline-text.tsx"
Cohesion: 0.15
Nodes (16): CiteHandler, InlineText(), renderNodes(), MarkdownView(), MermaidView(), CitationResolver, citationLabels(), findEmClose() (+8 more)

### Community 165 - "cje"
Cohesion: 0.40
Nodes (5): aje(), cje(), eje(), Nne(), One()

### Community 166 - "successors"
Cohesion: 0.50
Nodes (5): isDirected(), isLeaf(), neighbors(), successors(), W3()

### Community 167 - "ADR 0001: Drop LangGraph, Deep Agents and the LangChain adapter"
Cohesion: 0.40
Nodes (4): ADR 0001: Drop LangGraph, Deep Agents and the LangChain adapter, Consequences, Context, Decision

### Community 168 - "yB"
Cohesion: 0.50
Nodes (4): dct(), Hlt(), Tve(), yB()

### Community 169 - "Wot"
Cohesion: 0.50
Nodes (4): J0e(), qot(), Wot(), zot()

### Community 170 - "30. CI Evaluation Gates"
Cohesion: 0.50
Nodes (4): 30. CI Evaluation Gates, Every PR, Release gate, Scheduled/manual online eval

### Community 172 - "GYe"
Cohesion: 0.67
Nodes (3): BYe(), GYe(), p1()

### Community 173 - "YYe"
Cohesion: 0.67
Nodes (3): e5(), sB(), YYe()

### Community 174 - "8.1 Graph layers"
Cohesion: 0.25
Nodes (8): 8.1 Graph layers, 8.2 Graph construction strategy, 8. Knowledge Graph Architecture, Layer 0 — Project, Layer 1 — Subsystems, Layer 2 — Components, Layer 3 — Implementation, Layer 4 — Evidence

### Community 175 - "24. API Surface"
Cohesion: 0.25
Nodes (8): 24. API Surface, Chat, Export, Graph/visuals, Guide, Progress streaming, Projects, Sessions

### Community 177 - "fve"
Cohesion: 0.15
Nodes (12): act(), cct(), dve(), fve(), Hct(), ict(), Kht(), oct() (+4 more)

### Community 180 - "i0"
Cohesion: 0.25
Nodes (11): AW(), Fu(), gke(), i0(), IW(), LW(), mke(), pke() (+3 more)

### Community 184 - "28. Scalability Strategy"
Cohesion: 0.29
Nodes (7): 28. Scalability Strategy, API, Deep Agents, LLM, Neo4j, Qdrant, Workers

### Community 185 - "4. Why Multiple Data Stores Are Justified"
Cohesion: 0.29
Nodes (7): 4. Why Multiple Data Stores Are Justified, Native BM25, Neo4j, Object storage, PostgreSQL, Qdrant, Redis

### Community 186 - "16. Deep Agents"
Cohesion: 0.29
Nodes (7): 16. Deep Agents, AI/ML Investigator, Architecture Investigator, Data/Database Investigator, Infrastructure Investigator, Security Investigator, Workflow Investigator

### Community 187 - "7. Primary User Journey"
Cohesion: 0.29
Nodes (7): 7.1 Landing, 7.2 Analysis Progress, 7.3 Project Guide, 7.4 Project Chat, 7.5 Export, 7.6 Session Expiry, 7. Primary User Journey

### Community 188 - "scripts"
Cohesion: 0.29
Nodes (7): scripts, build, dev, lint, prisma:migrate, start, test

### Community 191 - "cn"
Cohesion: 0.09
Nodes (28): ProjectNav(), ProjectSection, SECTIONS, Skeleton(), apply(), LABEL, listeners, NEXT (+20 more)

### Community 192 - "a1e"
Cohesion: 0.47
Nodes (6): a1e(), $lt(), n1e(), Olt(), r1e(), t1e()

### Community 193 - "C4"
Cohesion: 0.40
Nodes (5): C4(), LP(), TP(), wKe(), x4()

### Community 195 - "linkNode"
Cohesion: 0.33
Nodes (6): deserialize(), fromModel(), getRefNode(), Ha(), linkNode(), reviveReference()

### Community 198 - "30. Failure and Recovery"
Cohesion: 0.33
Nodes (6): 30. Failure and Recovery, Neo4j failure, Provider 429, Qdrant failure, section generation failure, worker crash

### Community 199 - "3. Architectural Boundaries"
Cohesion: 0.33
Nodes (6): 3.1 Frontend boundary, 3.2 API boundary, 3.3 Worker boundary, 3.4 AI boundary, 3.5 Storage boundaries, 3. Architectural Boundaries

### Community 200 - "33. GitHub Actions"
Cohesion: 0.33
Nodes (6): 33. GitHub Actions, ci.yml, eval-offline.yml, eval-online.yml, security.yml, terraform.yml

### Community 201 - "36. Testing Strategy"
Cohesion: 0.33
Nodes (6): 36. Testing Strategy, Contract tests, End-to-end tests, Integration tests, Security tests, Unit tests

### Community 202 - "7. Repository Inventory"
Cohesion: 0.33
Nodes (6): 7. Repository Inventory, Tier 0 — architecture-critical, Tier 1 — core domain, Tier 2 — support, Tier 3 — context, Tier 4 — noise/generated

### Community 203 - "8.2 Language-aware analyzers"
Cohesion: 0.33
Nodes (6): 8.1 Tree-sitter, 8.2 Language-aware analyzers, 8. Static and Structural Analysis, Java, Python, TypeScript/JavaScript

### Community 204 - "11. Interview and Viva Preparation"
Cohesion: 0.33
Nodes (6): 11. Interview and Viva Preparation, Design questions, Extension questions, Failure questions, Repository-grounded questions, Security questions

### Community 205 - "20. Non-Functional Requirements"
Cohesion: 0.33
Nodes (6): 20. Non-Functional Requirements, Observability, Performance, Privacy, Reliability, Scalability

### Community 206 - "4. Primary Users"
Cohesion: 0.33
Nodes (6): 4. Primary Users, Persona A — Student / Placement Candidate, Persona B — Hackathon Participant, Persona C — Software Engineer, Persona D — Researcher / Student Researcher, Persona E — Learner Exploring Open Source

### Community 207 - "Local Development"
Cohesion: 0.33
Nodes (6): Backend, Frontend, Local Development, Prerequisites, Start infrastructure, Worker

### Community 208 - "DocQA — retrieval-augmented question answering over your documents"
Cohesion: 0.33
Nodes (5): Architecture, Configuration, DocQA — retrieval-augmented question answering over your documents, Notes for automated tools, Running locally

### Community 209 - "expiry-notice.tsx"
Cohesion: 0.11
Nodes (26): EXPIRY_COPY, EXPIRY_WARNING_MS, ExpiryBanner(), ExpiryChip(), useRemaining(), LandingPage(), submit(), stages (+18 more)

### Community 216 - "10. Request Priority"
Cohesion: 0.40
Nodes (5): 10. Request Priority, P0 — user-blocking, P1 — core analysis, P2 — enrichments, P3 — background evaluation

### Community 217 - "57. Model Assignment Strategy"
Cohesion: 0.40
Nodes (5): 57. Model Assignment Strategy, Balanced model, Deterministic/no LLM, Fast model, Reasoning model

### Community 218 - "6.1 Repository Intelligence Layer"
Cohesion: 0.40
Nodes (5): 6.1 Repository Intelligence Layer, 6. Project Intelligence Architecture, Inputs, Invariant, Outputs

### Community 219 - "9. Graph Query Patterns"
Cohesion: 0.40
Nodes (5): 9. Graph Query Patterns, Find callers, Find neighbors, Find subsystem implementation, Trace API to persistence

### Community 220 - "28.1 Evaluation frameworks"
Cohesion: 0.40
Nodes (5): 28.1 Evaluation frameworks, 28. Evaluation System, Custom deterministic evals, DeepEval, RAGAS

### Community 221 - "31. Security Engineering"
Cohesion: 0.40
Nodes (5): 31.1 Trust boundaries, 31.2 Secret detection, 31.3 Agent tools, 31.4 Prompt injection, 31. Security Engineering

### Community 222 - "18. Evaluation Requirements"
Cohesion: 0.40
Nodes (5): 18. Evaluation Requirements, Agents, Frameworks, Generation, Retrieval

### Community 223 - "19. Security Requirements"
Cohesion: 0.40
Nodes (5): 19. Security Requirements, Prompt injection, Repository security, Secrets, ZIP controls

### Community 229 - "acquireParserWorker"
Cohesion: 0.67
Nodes (4): acquireParserWorker(), initializeWorkers(), lock(), onReady()

### Community 231 - "k"
Cohesion: 0.50
Nodes (4): cN(), lN(), offset(), k()

### Community 236 - "isSubtype"
Cohesion: 0.50
Nodes (4): getAllSubTypes(), getAllTypes(), isInstance(), isSubtype()

### Community 237 - "q9"
Cohesion: 0.50
Nodes (4): getNodes(), Kot(), q9(), Xot()

### Community 240 - "15. Reciprocal Rank Fusion"
Cohesion: 0.50
Nodes (4): 15. Reciprocal Rank Fusion, Conceptual architecture question, Symbol lookup, Workflow tracing

### Community 241 - "34. Technical Depth Personalization"
Cohesion: 0.50
Nodes (4): 34. Technical Depth Personalization, Beginner, Experienced, Intermediate

### Community 242 - "50. Evaluation in CI/CD"
Cohesion: 0.50
Nodes (4): 50. Evaluation in CI/CD, Offline PR gate, Online bounded eval, Release benchmark

### Community 247 - "37. Performance Engineering"
Cohesion: 0.50
Nodes (4): 37. Performance Engineering, Avoid, Backpressure, Parallelizable work

### Community 248 - "16. Agentic Requirements"
Cohesion: 0.50
Nodes (4): 16. Agentic Requirements, Deep Agents, Excluded, LangGraph

### Community 249 - "22. Success Metrics"
Cohesion: 0.50
Nodes (4): 22. Success Metrics, Product, Quality, Reliability

### Community 250 - "get_settings"
Cohesion: 0.10
Nodes (24): get_settings(), BaseSettings, Application settings loaded from the environment., Runtime configuration for the API and the worker., Return the cached settings instance., Settings, configure_logging(), Configure structured-ish logging to stdout. (+16 more)

### Community 251 - "shortlink-platform"
Cohesion: 0.50
Nodes (3): Deploying, Provisioning, shortlink-platform

### Community 252 - "eslint.config.mjs"
Cohesion: 0.50
Nodes (3): config, ref_eslint_config_next_core_web_vitals, ref_eslint_config_next_typescript

### Community 253 - "vitest.config.ts"
Cohesion: 0.50
Nodes (3): ref_node_url, ref_vitejs_plugin_react, ref_vitest_config

### Community 257 - "collectValues"
Cohesion: 0.67
Nodes (3): all(), collectValues(), findAll()

### Community 261 - "indexNodes2"
Cohesion: 0.67
Nodes (3): getPosForId(), indexNodes(), indexNodes2()

### Community 262 - "GH"
Cohesion: 1.00
Nodes (3): GH(), oDe(), zH()

### Community 266 - "Nj"
Cohesion: 0.67
Nodes (3): Nj(), o9e(), s9e()

### Community 268 - "20. Hierarchical Summarization"
Cohesion: 0.67
Nodes (3): 20. Hierarchical Summarization, Deterministic summary first, Model semantic summary second

### Community 269 - "21. Retrieval Planner"
Cohesion: 0.67
Nodes (3): 21. Retrieval Planner, Query, Query

### Community 274 - "10. Chatbot Requirements"
Cohesion: 0.67
Nodes (3): 10. Chatbot Requirements, Chat memory, Session learning state

### Community 275 - "12. Export Requirements"
Cohesion: 0.67
Nodes (3): 12. Export Requirements, Chat Learning PDF, Project Guide PDF

### Community 276 - "5. Supported Inputs"
Cohesion: 0.67
Nodes (3): 5. Supported Inputs, Explicitly not supported, V1

### Community 278 - "toast.tsx"
Cohesion: 0.10
Nodes (17): csp, nextConfig, frontend_src_app_globals, metadata, mono, sans, serif, AppProviders() (+9 more)

## Knowledge Gaps
- **689 isolated node(s):** `unfold`, `name`, `version`, `private`, `description` (+684 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 1965 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **55 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `20. HTTP API (`unfold.api`, prefix `/api/v1`)` connect `Unfold — Low-Level Design (LLD)` to `guide-page.tsx`, `projects.py`, `ErrorCode`?**
  _High betweenness centrality (0.152) - this node is a cross-community bridge._
- **Why does `4. PostgreSQL schema (`unfold.persistence.postgres.models`, Alembic `migrations/`)` connect `compiler.py` to `Node`, `integration/conftest.py`, `sessions.py`, `UnfoldError`, `_Builder`, `_JsExtractor`, `Unfold — Low-Level Design (LLD)`, `indexing/chunking.py`, `join`, `planner.py`?**
  _High betweenness centrality (0.140) - this node is a cross-community bridge._
- **Why does `current()` connect `Unfold — Low-Level Design (LLD)` to `mermaid.min.js`?**
  _High betweenness centrality (0.136) - this node is a cross-community bridge._
- **Are the 147 inferred relationships involving `t()` (e.g. with `ACTION()` and `aDe()`) actually correct?**
  _`t()` has 147 INFERRED edges - model-reasoned connections that need verification._
- **Are the 175 inferred relationships involving `r()` (e.g. with `mermaid.min.js` and `ACTION()`) actually correct?**
  _`r()` has 175 INFERRED edges - model-reasoned connections that need verification._
- **Are the 16 inferred relationships involving `s()` (e.g. with `C()` and `v()`) actually correct?**
  _`s()` has 16 INFERRED edges - model-reasoned connections that need verification._
- **Are the 160 inferred relationships involving `n()` (e.g. with `mermaid.min.js` and `addAnnotation()`) actually correct?**
  _`n()` has 160 INFERRED edges - model-reasoned connections that need verification._