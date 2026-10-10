# Graph Report - unfold  (2026-10-10)

## Corpus Check
- 321 files · ~239,571 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 22 file(s) not represented in the graph (top: (none) 8, .example 3, .j2 3)

## Summary
- 7164 nodes · 22165 edges · 279 communities (220 shown, 59 thin omitted)
- Extraction: 86% EXTRACTED · 14% INFERRED · 0% AMBIGUOUS · INFERRED: 3147 edges (avg confidence: 0.89)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `199654b6`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- mermaid.min.js
- yct
- t
- push
- core/errors.py
- gZe
- gemini.py
- i
- hybrid.py
- builders.py
- cli.py
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
- gateway.py
- analyzers/base.py
- Unfold — Low-Level Design (LLD)
- concat
- enums.py
- secrets.py
- peekChar
- .toString
- d
- subrule
- Node
- .constructor
- langchain_core_callbacks
- ModelCapability
- rgb
- LLMRequest
- lineTo
- Cc
- guide-page.tsx
- env.py
- compiler.py
- ingestion.py
- api/errors.py
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
- plan_query
- IngestionService
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
- ProgressReporter
- _JsExtractor
- nodes
- manyInternal
- UnfoldError
- use-chat-thread.ts
- test_llm_gateway_policy.py
- test_security_hardening.py
- ProviderBudget
- TokenBudget
- pipeline.py
- documents.py
- domain/models.py
- text
- landing-page.tsx
- detect.py
- README_original.md
- ref_react
- devDependencies
- diagram-layout.ts
- indexOf
- setAttribute
- assess
- test_api_sessions_projects.py
- generate.py
- .data_access
- complexity.py
- CircuitBreaker
- Settings
- route.ts
- use-project-progress.ts
- pdf.py
- dataclasses
- extend
- bn
- DenseIndex
- 4.1 PostgreSQL — operational system of record
- express_ts_prisma/package.json
- authService.ts
- to_provider_schema
- Jg
- LA
- agents/models.py
- retrievers.py
- qqe
- Unfold — Product Requirements Document (PRD)
- app.ts
- postService.ts
- llm.py
- Nmt
- addDetails
- e7t
- a7t
- Unfold
- children
- vercel.json
- dependencies
- Zk
- BlobStore
- test_architecture.py
- destructEndLink
- hT
- match
- recursiveReduce
- .__init__
- .__init__
- GraphBuilder
- Technology Stack
- compilerOptions
- split_text
- Runtime
- Le
- sse_starlette_sse
- 3. Product Principles
- 8. Project Guide Requirements
- devDependencies
- count
- calculateHorizontalSpace
- starlette_background
- HLD.md
- 46. Guide Evaluation
- 33. Key Architectural Decisions
- dependencies
- compilerOptions
- HybridRetriever
- evidence-drawer.tsx
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
- `Python` --references--> `ast()`  [INFERRED]
  docs/specs/ESD.md → backend/src/unfold/exports/static/mermaid.min.js
- `11. Git worktree, RAM, disk and workspace plan` --references--> `node()`  [INFERRED]
  PROJECT_AUDIT.md → backend/src/unfold/exports/static/mermaid.min.js
- `6. Ingestion & security (`unfold.ingestion`, `unfold.security`)` --references--> `safe_extract()`  [INFERRED]
  docs/LLD.md → backend/src/unfold/ingestion/archive.py
- `17. LLM Gateway` --references--> `LLMRequest`  [INFERRED]
  docs/specs/ESD.md → backend/src/unfold/llm/schemas.py

## Import Cycles
- 1-file cycle: `backend/src/unfold/exports/render.py -> backend/src/unfold/exports/render.py`

## Communities (279 total, 59 thin omitted)

### Community 0 - "mermaid.min.js"
Cohesion: 0.00
Nodes (312): _2(), activationCount(), addAll(), addAttributes(), addCssStyles(), addEdge(), addEntity(), addEntry() (+304 more)

### Community 1 - "yct"
Cohesion: 0.33
Nodes (5): e2e(), Jbe(), Kf(), yct(), 8. Testing and production readiness

### Community 2 - "t"
Cohesion: 0.01
Nodes (273): $2e(), i(), a$e(), aa(), ACTION(), addAnnotation(), aDe(), adt() (+265 more)

### Community 3 - "push"
Cohesion: 0.02
Nodes (212): $7(), A2(), a5(), a9e(), add(), addActor(), addBox(), addHiddenNodes() (+204 more)

### Community 4 - "core/errors.py"
Cohesion: 0.13
Nodes (9): Unified error model (ESD §25). Every failure that reaches a user carries an…, StoreUnavailable, MinioBlobStore, Path, T, `BlobStore` adapter over the MinIO SDK (ESD §4.6). Sync SDK → worker threads.…, Typed retrieval errors (ESD §25)., minio (+1 more)

### Community 5 - "gZe"
Cohesion: 0.07
Nodes (74): beginGroup(), ek(), endGroup(), endGroups(), g1e(), gZe(), b(), Be() (+66 more)

### Community 6 - "gemini.py"
Cohesion: 0.06
Nodes (52): APIError, AuthError, BadRequest, InvalidOutput, parse_json_object(), ProviderCallError, Exception, RateLimited (+44 more)

### Community 7 - "i"
Cohesion: 0.02
Nodes (146): Ab(), accept(), addLink(), addNode(), addRelationship(), f(), AS(), _at() (+138 more)

### Community 8 - "hybrid.py"
Cohesion: 0.10
Nodes (31): RetrievalMode, Process-wide reranker singleton kept in `Runtime.extras`., reranker_for(), RetrievalConfig, Every first-stage retriever failed — there is no honest evidence to return., RetrievalFailed, FusedCandidate, HybridRetriever (+23 more)

### Community 9 - "builders.py"
Cohesion: 0.07
Nodes (64): diagram_id(), _agg_label(), ai_pipeline(), architecture(), build_all(), data_model(), deployment(), _EdgeAgg (+56 more)

### Community 10 - "cli.py"
Cohesion: 0.12
Nodes (18): cmd_models(), describe(), main(), _out(), _ping(), Operator CLI (LLD §13): `python -m unfold.llm.cli models|ping`. `models` prints…, Configuration summary without secrets (also used by tests)., build_gateway() (+10 more)

### Community 11 - "GraphQueries"
Cohesion: 0.12
Nodes (29): check_rel_type(), DegreeNode, EndpointFlow, GraphEdge, GraphNode, GraphOverview, GraphValidationError, ModuleDependency (+21 more)

### Community 12 - "InventoryFile"
Cohesion: 0.05
Nodes (70): LanguageAnalyzer, Protocol, One analyzer: decides which inventory files it handles and extracts facts from…, ConfigAnalyzer, add(), _images_of(), _kind(), _line_of() (+62 more)

### Community 13 - "consume"
Cohesion: 0.09
Nodes (46): callFunction(), consume(), consumeArg(), consumeArgs(), consumeSpaces(), countExpansion(), expandAfterFuture(), expandMacro() (+38 more)

### Community 14 - "ProjectIR"
Cohesion: 0.07
Nodes (54): build_ir(), Normalize one repository's inventory + analysis into a `ProjectIR` (pure,…, Referential-integrity problems of an IR (empty list = valid). Used by tests and…, validate_ir(), Project IR (LLD §8): schema, pure builder, complexity profile and object-store…, ProjectIdentity, ProjectIR, clear_ir_cache() (+46 more)

### Community 15 - "_Builder"
Cohesion: 0.10
Nodes (21): _basename(), _Builder, _canon_tech(), _dirname(), _is_code(), _is_dockerfile(), _join(), _norm_name() (+13 more)

### Community 16 - "project_ir/builder.py"
Cohesion: 0.14
Nodes (26): Provenance, Where a fact came from. Deterministic facts outrank inferred ones everywhere…, _image_rule(), _join_route(), normalize_route(), Project IR builder (LLD §8, ESD §9): `Inventory` + `RepositoryAnalysis` →…, `/api//users/` → `/api/users`; empty → `/`. Path parameters are kept as written., AIComponentIR (+18 more)

### Community 17 - "projects.py"
Cohesion: 0.04
Nodes (103): client_ip(), CurrentSession, enforce_ip_limits(), enforce_rate_limit(), get_runtime(), owned_chat(), parse_uuid(), Request (+95 more)

### Community 18 - "ErrorCode"
Cohesion: 0.06
Nodes (72): ErrorCode, IngestionError, InputError, StrEnum, SecurityRejection, _Budget, _common_root(), _copy_limited() (+64 more)

### Community 19 - "indexing/chunking.py"
Cohesion: 0.21
Nodes (24): content_hash(), chunk_file(), _config_units(), content_type_for(), _dockerfile_units(), _ext(), _fan_in(), _gap_units() (+16 more)

### Community 20 - "AI_infrastructure.md"
Cohesion: 0.04
Nodes (53): 11. Local Embeddings, 12. Qdrant Vector Layer, 13. Native Lexical Retrieval, 14. Native Hybrid RAG, 16. Cross-Encoder Reranking, 17. Structural Retrieval, 18. Neo4j Graph Intelligence, 19. Graph Communities (+45 more)

### Community 21 - "api/types.ts"
Cohesion: 0.04
Nodes (63): ProjectContextValue, SessionContext, SessionContextValue, streamPaths, ArchitectureOut, ArtifactKind, ArtifactOut, ArtifactStatus (+55 more)

### Community 22 - "gateway.py"
Cohesion: 0.11
Nodes (32): ProviderCapacityError, ProviderError, _aclose(), _CallState, estimate_tokens(), LLMGateway, invoke(), invoke() (+24 more)

### Community 23 - "analyzers/base.py"
Cohesion: 0.07
Nodes (26): AnalysisContext, build_context(), _directories(), find_line(), _go_modules(), _is_under(), line_of(), load_jsonc() (+18 more)

### Community 24 - "Unfold — Low-Level Design (LLD)"
Cohesion: 0.06
Nodes (23): BM25Index, LexicalArtifact, LexicalIndexCache, Any, Builder for `LexicalArtifact`s (tokenizer: `indexing.tokenize`)., In-process LRU of loaded artifacts keyed by object key (8 by default)., A built / loaded BM25 index plus the chunk id of every document (same order)., 10. Indexing (`unfold.indexing`) (+15 more)

### Community 25 - "concat"
Cohesion: 0.04
Nodes (67): b5e(), buildKeywordPattern(), buildKeywordToken(), buildLeftRecursionError(), checkIsTarget(), concat(), Cse(), Dv() (+59 more)

### Community 26 - "enums.py"
Cohesion: 0.12
Nodes (20): ArtifactKind, ArtifactStatus, ContentType, StrEnum, Domain enumerations shared by every layer (LLD §5, §11, §13, §16)., SectionStatus, Chunk, BaseModel (+12 more)

### Community 27 - "secrets.py"
Cohesion: 0.11
Nodes (24): Untrusted-data fencing for prompts (LLD §6). Repository text is data, never…, Wrap repository content; neutralise any embedded fence tags so data cannot…, untrusted(), InjectionSignal, Rule-based prompt-injection scanner (LLD §6, AI infra §40). Signals label…, `untrusted_source` for all repository data; `suspicious` when injection-like…, scan(), trust_label() (+16 more)

### Community 28 - "peekChar"
Cohesion: 0.12
Nodes (45): alternative(), assertion(), atom(), atomEscape(), characterClass(), characterClassEscape(), classAtom(), classEscape() (+37 more)

### Community 29 - ".toString"
Cohesion: 0.03
Nodes (97): _5t(), addDocument(), addExportedSymbol(), addLocalSymbol(), aXe(), build(), buildDocuments(), buildKeywordTokens() (+89 more)

### Community 30 - "d"
Cohesion: 0.06
Nodes (137): _5e(), an(), arc(), b(), Ba(), ble(), blt(), c1e() (+129 more)

### Community 31 - "subrule"
Cohesion: 0.12
Nodes (21): after(), before(), computeRuleType(), construct(), constructInfix(), Cv(), DEFINE_RULE(), doParse() (+13 more)

### Community 32 - "Node"
Cohesion: 0.06
Nodes (28): add(), _CSharpExtractor, _Extractor, emit(), _GoExtractor, _JavaExtractor, _join_path(), _JvmMixin (+20 more)

### Community 33 - ".constructor"
Cohesion: 0.05
Nodes (38): _4e(), bh(), brt(), copy(), d4e(), finalize(), g4e(), Grt() (+30 more)

### Community 35 - "ModelCapability"
Cohesion: 0.12
Nodes (9): ModelCapability, Provider, Any, Protocol, Resolve `CAPABILITY_TIERS[capability]` through a provider's tier→model map…, One vendor. `key_index` selects the credential (0-based); keys themselves never…, tier_models(), ModelRouter (+1 more)

### Community 36 - "rgb"
Cohesion: 0.07
Nodes (41): Alt(), b8(), Bee(), brighter(), clamp(), D8(), darker(), displayable() (+33 more)

### Community 37 - "LLMRequest"
Cohesion: 0.14
Nodes (9): cache_key(), Any, LLM response cache (LLD §13, AI_infrastructure §53). Only for `req.cacheable`…, ResponseCache, LLMRequest, T, FakeProvider, Scripted `Provider`: per-model queues of actions; records every call it… (+1 more)

### Community 38 - "lineTo"
Cohesion: 0.17
Nodes (23): arcTo(), closePath(), _drawToContext(), Kpt(), lineTo(), measureText(), moveTo(), omt() (+15 more)

### Community 39 - "Cc"
Cohesion: 0.06
Nodes (43): addAstNodeRegionWithAssignmentsTo(), assign(), assignWithoutOverride(), bv(), Cc(), createDescription(), createDescriptions(), createInfoDescriptions() (+35 more)

### Community 40 - "guide-page.tsx"
Cohesion: 0.06
Nodes (51): ArchitecturePage(), ArchitectureWorkspace(), ChatPage(), ChatWorkspace(), Conversation(), prompts, ErrorPanel(), ExportButton() (+43 more)

### Community 41 - "env.py"
Cohesion: 0.11
Nodes (10): alembic, do_run_migrations(), include_object(), Alembic async environment. URL comes from unfold Settings (DATABASE_URL), never…, run_async_migrations(), run_migrations_offline(), run_migrations_online(), Connection (+2 more)

### Community 42 - "compiler.py"
Cohesion: 0.10
Nodes (43): estimate_tokens(), _clean_summary(), compile_context(), _Compiler, _drop_lowest(), _finalize(), _hash(), _notes() (+35 more)

### Community 43 - "ingestion.py"
Cohesion: 0.13
Nodes (16): embed_query(), embed_texts(), _model(), Local sentence-transformer embeddings., Embed a batch of texts into normalized dense vectors., Embed a single query., extract_text(), Document ingestion: persist uploads, extract text, chunk, embed and index. (+8 more)

### Community 44 - "api/errors.py"
Cohesion: 0.21
Nodes (13): _envelope(), install_error_handling(), http_error(), unfold_error(), unhandled(), validation_error(), FastAPI, Request (+5 more)

### Community 45 - "render.py"
Cohesion: 0.11
Nodes (33): DiagramExport, ExportChatMessage, ExportProject, BaseModel, Inputs for the export templates (LLD §19). The worker maps persisted rows onto…, _claim_rows(), _clean(), _code_caption() (+25 more)

### Community 46 - "uN"
Cohesion: 0.06
Nodes (22): Ane(), bje(), Dne(), Ene(), FYe(), Ine(), kne(), Lne() (+14 more)

### Community 47 - "Store"
Cohesion: 0.08
Nodes (26): New(), Connect(), Store, newCode(), main(), TestHealth(), go_pkg_context, go_pkg_crypto_rand (+18 more)

### Community 48 - "edges"
Cohesion: 0.16
Nodes (25): b0e(), Bge(), Bot(), C0e(), Dge(), E9(), edges(), fT() (+17 more)

### Community 49 - "sd"
Cohesion: 0.13
Nodes (31): aD(), aGe(), Ax(), bd(), d_e(), eD(), eGe(), f_e() (+23 more)

### Community 50 - "validateDocument"
Cohesion: 0.06
Nodes (38): buildLeafNode(), cancelWrite(), dequeue(), enqueue(), g6(), getNameNode(), getSource(), handleException() (+30 more)

### Community 51 - "VectorStore"
Cohesion: 0.14
Nodes (7): AsyncSession, AsyncSession, Create the collection if it does not exist., Insert or replace chunk vectors., Return (chunk id, score) pairs for the nearest vectors., Stores and searches chunk embeddings in a Qdrant collection., VectorStore

### Community 52 - "join"
Cohesion: 0.05
Nodes (98): A4(), ac(), Ar(), b4(), blockquote(), boe(), bP(), bse() (+90 more)

### Community 53 - "architecture.md"
Cohesion: 0.06
Nodes (35): 10. Retrieval Architecture, 11.1 Guide mode, 11.2 Chat mode, 11.3 Deep investigation mode, 11. Retrieval Modes, 12.1 Token budgeting, 12. Context Engineering Architecture, 13. LangGraph HLD (+27 more)

### Community 54 - "frontend/package.json"
Cohesion: 0.06
Nodes (32): engines, node, react, react-dom, @types/react, @types/react-dom, typescript, @vitejs/plugin-react (+24 more)

### Community 55 - "g3"
Cohesion: 0.12
Nodes (31): e0e(), Fge(), filterNodes(), a(), fot(), g3(), hasNode(), isMultigraph() (+23 more)

### Community 56 - "ESD.md"
Cohesion: 0.05
Nodes (42): 10. Graph Builder, 11.1 Code-aware chunks, 11.2 Hierarchical summaries, 11. Chunking and Indexing, 12. Native Hybrid RAG, 13. Query Planner, 14. Context Compiler, 15. LangGraph Orchestration (+34 more)

### Community 57 - "communities.py"
Cohesion: 0.13
Nodes (25): _add_weight(), _build_graph(), detect(), _dir(), entity_subsystems(), fan_in(), _label_hint(), _merge_small() (+17 more)

### Community 58 - "Embedder"
Cohesion: 0.12
Nodes (11): Embedder, Any, NDArray, ModelUnavailable, A local model (embedder / cross-encoder) could not be loaded or failed at…, CrossEncoderReranker, Any, Relevance logits aligned with `texts` (higher = more relevant). (+3 more)

### Community 59 - "inventory.py"
Cohesion: 0.13
Nodes (27): build_inventory(), decode_text(), _describe(), _ext(), _is_build_output_dir(), _is_entrypoint(), _is_text(), language_for() (+19 more)

### Community 60 - "plan_query"
Cohesion: 0.08
Nodes (55): chunk_id(), _digest(), entity_id(), Deterministic id for an IR/graph entity: recomputation yields the same id…, RetrievalIntent, SubsystemCandidate, Same transformation as documents, de-duplicated in first-seen order., tokenize_query() (+47 more)

### Community 61 - "IngestionService"
Cohesion: 0.24
Nodes (7): 14. Orchestration (`unfold.orchestration`), Document, An uploaded source document., IngestionService, Owns the document lifecycle in PostgreSQL and Qdrant., Create a pending document row holding the extracted text., Chunk, embed and index a pending document. Returns the number of chunks.

### Community 62 - "_Extractor"
Cohesion: 0.08
Nodes (34): AnnAssign, Assign, AST, AsyncFunctionDef, AugAssign, _annotation_type(), _class_assign(), _const_str() (+26 more)

### Community 63 - "node"
Cohesion: 0.13
Nodes (25): aot(), i(), n(), c8(), e8(), got(), n(), k9() (+17 more)

### Community 64 - "edge"
Cohesion: 0.10
Nodes (31): ast(), b9(), cot(), d(), h(), u(), cT(), d0e() (+23 more)

### Community 65 - "KeyPool"
Cohesion: 0.15
Nodes (12): KeyPool, Any, Redis, Credential pool per provider (LLD §13, ESD §18). Keys are credentials, not…, Per-key health (index only, never key material)., Least-recently-used healthy key index, or None when every key is disabled or…, Mark a 429. `cooldown_s` is applied to this key only when quotas are per key., Credential rejected: take it out of rotation (auto re-enabled after… (+4 more)

### Community 66 - "web/package.json"
Cohesion: 0.08
Nodes (24): dependencies, react, react-dom, devDependencies, @types/react, @types/react-dom, typescript, vite (+16 more)

### Community 67 - "link"
Cohesion: 0.06
Nodes (42): allElements(), u(), buildEmptyAlternationError(), checkbox(), code(), codespan(), computeContentAssist(), del() (+34 more)

### Community 68 - "lib/api/client.ts"
Cohesion: 0.15
Nodes (13): API_PREFIX, apiClient, ApiClientOptions, ApiError, FetchLike, isErrorEnvelope(), networkError(), RequestOptions (+5 more)

### Community 69 - "test_indexing_chunking.py"
Cohesion: 0.38
Nodes (12): chunk_project(), Chunks for every indexable file of the IR whose text is available (`path →…, _by(), _file(), _ir(), _sym(), test_compose_services_split(), test_deterministic_ids_and_hashes() (+4 more)

### Community 70 - "addRelation"
Cohesion: 0.07
Nodes (44): addClass(), addClassesToNamespace(), addDescription(), addInterface(), addMember(), addMembers(), addRelation(), addRelationObjs() (+36 more)

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
Nodes (85): a5e(), aAe(), addElement(), addRequirement(), a(), awaitBuilderState(), awaitDocumentState(), bezierCurveTo() (+77 more)

### Community 79 - "ProgressReporter"
Cohesion: 0.08
Nodes (26): Non-negotiables, check_transition(), InvalidTransition, JobStage, ValueError, Forward-only pipeline; re-entering the current stage (resume) is allowed;…, current(), _heartbeat() (+18 more)

### Community 80 - "_JsExtractor"
Cohesion: 0.11
Nodes (8): _go_local_name(), _go_package(), _JsExtractor, _npm_package(), (resolved repository path, external, npm package) for a module specifier., Handle `require("x")` / `import("x")`; returns True when `call` was an import., The function node of `fn`, `memo(fn)`, `React.forwardRef(fn)`, ... (None when…, For `router.route('/x').get(h).post(h2)` return ("router", "/x").

### Community 81 - "nodes"
Cohesion: 0.11
Nodes (23): $9(), A9(), dot(), F9(), iot(), jTe(), Kmt(), Lf() (+15 more)

### Community 82 - "manyInternal"
Cohesion: 0.05
Nodes (38): _8(), cM(), convert(), Cte(), Eb(), eUe(), fke(), getRule() (+30 more)

### Community 83 - "UnfoldError"
Cohesion: 0.06
Nodes (60): owned_project(), 404 (not 403) on foreign ids so project ids are not enumerable., _diagram_out(), get_architecture(), get_diagram(), get_evidence(), get_guide(), get_section() (+52 more)

### Community 84 - "use-chat-thread.ts"
Cohesion: 0.11
Nodes (29): backoffDelay(), parseJSON(), postEventStream(), readEventStream(), SSEMessage, SSEParser, subscribeEventStream(), Subscription (+21 more)

### Community 85 - "test_llm_gateway_policy.py"
Cohesion: 0.12
Nodes (42): Priority, _all_text(), _gemini(), _groq(), h(), Harness, _limits(), Any (+34 more)

### Community 86 - "test_security_hardening.py"
Cohesion: 0.21
Nodes (14): _get_ip(), _ip_app(), FastAPI, parametrize, Audit S-2, S-3, S-5, S-7 — behaviour that needs no data store., _settings(), test_client_ip_is_normalised(), ip() (+6 more)

### Community 87 - "ProviderBudget"
Cohesion: 0.19
Nodes (8): ProviderBudget, Redis, One atomic attempt. Returns (reservation, wait_hint_seconds, reason)., Reserve capacity, waiting in ≤1 s steps up to the priority wait budget. None →…, The call reached the provider: keep the request counted, replace the token…, The call never reached the provider: undo the reservation entirely., Current usage for diagnostics/tests (does not prune expired entries)., Reservation

### Community 88 - "TokenBudget"
Cohesion: 0.14
Nodes (7): Token estimation + budgeting for compiled contexts (LLD §12). Tokens ≈…, Cut `text` at a line boundary so that result (incl. marker) fits `max_tokens`.…, A running token allowance. `take` succeeds only when the item fits entirely., Account for mandatory content (objective) even if it overflows., TokenBudget, truncate_to_tokens(), math

### Community 89 - "pipeline.py"
Cohesion: 0.04
Nodes (87): argparse, asyncio, asyncpg, FastAPI composition root (HLD §2 `api`). Builds one Runtime per process; never…, Project cleanup (LLD §19): remove a project from every store. Idempotent; safe…, get_settings(), Typed, environment-driven settings (LLD §2). One instance per process via…, configure_logging() (+79 more)

### Community 90 - "documents.py"
Cohesion: 0.07
Nodes (37): arq, arq_connections, get_db(), get_queue(), ArqRedis, AsyncSession, Shared FastAPI dependencies., Yield a database session per request. (+29 more)

### Community 91 - "domain/models.py"
Cohesion: 0.16
Nodes (24): ClaimKind, ClaimStatus, ClaimRecord, Shared domain value objects (LLD §8, §16, §21). Pure pydantic; no I/O. Imported…, _contains_word(), grade_claims(), _identifiers(), Grade every claim against the stored text of the chunks it cites.… (+16 more)

### Community 92 - "text"
Cohesion: 0.19
Nodes (15): a6t(), BI(), dP(), e(), fP(), getMaxDimension(), HNe(), nKe() (+7 more)

### Community 93 - "landing-page.tsx"
Cohesion: 0.22
Nodes (10): LandingPage(), submit(), stages, GithubRef, MAX_INSTRUCTION_CHARS, MAX_ZIP_BYTES, validateGithubUrl(), validateZipFile() (+2 more)

### Community 94 - "detect.py"
Cohesion: 0.12
Nodes (26): detect(), ai_hit(), hit(), _Hit, _import_candidates(), Technology and project-category detection (LLD §7). Pure: file analyses in,…, Every prefix of `module` that ends on a `.` or `/` boundary, plus the module…, _score_categories() (+18 more)

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

### Community 99 - "indexOf"
Cohesion: 0.10
Nodes (25): a(), a8(), $d(), Fv(), h(), indexOf(), jQ(), _k() (+17 more)

### Community 100 - "setAttribute"
Cohesion: 0.18
Nodes (13): eYe(), getAttribute(), hVe(), LWe(), mP(), d(), setAttribute(), sHe() (+5 more)

### Community 101 - "assess"
Cohesion: 0.21
Nodes (26): assess(), follow_up_plan(), _identifiers(), _paths(), Any, query_keys(), Deterministic context-sufficiency check + bounded follow-up planning (LLD §11,…, Narrow plan targeting the gaps found by `assess`, or None when there is nothing… (+18 more)

### Community 102 - "test_api_sessions_projects.py"
Cohesion: 0.17
Nodes (21): app(), _app_client(), client(), _peer(), AsyncClient, FastAPI, fixture, Test double for the `JobQueue` port: records enqueues instead of starting… (+13 more)

### Community 103 - "generate.py"
Cohesion: 0.12
Nodes (33): GenerationError, _clip(), normalise_plan(), plan_guide(), Guide planning, section writing and claim verification (LLD §15, §16). All…, One line, bounded: repository-derived names must not smuggle newlines or…, slugify(), _strip_diagrams() (+25 more)

### Community 105 - "complexity.py"
Cohesion: 0.17
Nodes (24): AgentBudget, ComplexityProfile, ComplexitySignals, _investigators(), _lin(), _log_ratio(), BaseModel, Deterministic project complexity profile (LLD §8, AI infrastructure §31–§32).… (+16 more)

### Community 106 - "CircuitBreaker"
Cohesion: 0.14
Nodes (12): CircuitBreaker, CircuitState, Any, Redis, StrEnum, Circuit breaker per `provider:model`, shared across workers through Redis (LLD…, _text(), Exception (+4 more)

### Community 107 - "Settings"
Cohesion: 0.04
Nodes (42): Architecture rules (enforced by `tests/unit/test_architecture.py`), backend/ — agent operating manual, Commands (run from `backend/`), Tests, create_app(), lifespan(), install_proxy_trust(), FastAPI (+34 more)

### Community 108 - "route.ts"
Cohesion: 0.13
Nodes (16): DELETE, dynamic, envelope(), GET, HEAD, HOP_BY_HOP, OPTIONS, PATCH (+8 more)

### Community 109 - "use-project-progress.ts"
Cohesion: 0.13
Nodes (20): JobOut, ProgressEvent, ProgressItem, ProgressPage, Action, finalEvent(), MAX_BACKOFF_MS, nextPollDelay() (+12 more)

### Community 110 - "pdf.py"
Cohesion: 0.16
Nodes (14): ExportError, _block_network(), _csp(), _mermaid_source(), PdfRenderer, PdfResult, HTML → PDF with headless Chromium (LLD §19). Runs in the worker `export` queue.…, One Chromium per process, launched lazily on first use; each render gets a… (+6 more)

### Community 111 - "dataclasses"
Cohesion: 0.12
Nodes (20): Versioned retrieval configuration (LLD §11, ESD §12 "Reciprocal Rank Fusion",…, SourceWeights, max_fused_score(), merge_variants(), Weighted Reciprocal Rank Fusion (LLD §11, ESD §12). Pure and deterministic.…, Fuse ranked chunk-id lists. A ranking with weight ≤ 0 (or missing from…, Merge the rankings of several query variants run against one retriever into a…, Best achievable fused score (rank 1 in every positively weighted ranking). (+12 more)

### Community 112 - "extend"
Cohesion: 0.18
Nodes (11): extend(), havingBaseSizing(), havingBaseStyle(), havingSize(), i(), withColor(), withFont(), withPhantom() (+3 more)

### Community 113 - "bn"
Cohesion: 0.12
Nodes (16): bn(), dje(), fje(), hje(), kje(), lje(), mje(), oje() (+8 more)

### Community 114 - "DenseIndex"
Cohesion: 0.14
Nodes (13): chunk_payload(), DenseIndex, embedding_cache_key(), point_id(), _project_filter(), Any, ndarray, Update `subsystem_id` payloads (chunk id → subsystem id) after subsystem… (+5 more)

### Community 115 - "4.1 PostgreSQL — operational system of record"
Cohesion: 0.14
Nodes (14): 4.1 PostgreSQL — operational system of record, 4.2 Neo4j — project knowledge graph, 4.3 Qdrant — dense semantic retrieval index, 4.4 Native BM25 index, 4.5 Redis, 4.6 Object storage, 4. Core Data Stores, analysis_jobs (+6 more)

### Community 116 - "express_ts_prisma/package.json"
Cohesion: 0.14
Nodes (13): description, typescript, main, name, private, version, jest, prisma (+5 more)

### Community 117 - "authService.ts"
Cohesion: 0.21
Nodes (11): HttpError, credentials, loginHandler(), registerHandler(), router, login(), register(), signToken() (+3 more)

### Community 118 - "to_provider_schema"
Cohesion: 0.39
Nodes (7): _convert(), Any, JSON-Schema normalisation for provider structured output / tool parameters (LLD…, Return a self-contained (no `$ref`) schema restricted to the portable keyword…, _resolve_ref(), to_provider_schema(), copy

### Community 119 - "Jg"
Cohesion: 0.06
Nodes (40): a6(), c6(), createDehyrationContext(), createGrammarElementIdMap(), createHydrationContext(), CW(), dehydrate(), dehydrateAstNode() (+32 more)

### Community 120 - "LA"
Cohesion: 0.02
Nodes (158): addToResyncTokens(), AT_LEAST_ONE(), AT_LEAST_ONE1(), AT_LEAST_ONE2(), AT_LEAST_ONE3(), AT_LEAST_ONE4(), AT_LEAST_ONE5(), AT_LEAST_ONE6() (+150 more)

### Community 121 - "agents/models.py"
Cohesion: 0.33
Nodes (8): ComponentFinding, FindingFact, InvestigationFinding, BaseModel, Deep Agent output contracts (LLD §15). Findings feed the guide planner, section…, WorkflowFinding, WorkflowStep, 17. Diagrams (`unfold.diagrams`)

### Community 122 - "retrievers.py"
Cohesion: 0.11
Nodes (21): _keep(), Code-aware tokenizer shared by BM25 indexing and querying (LLD §10, AI infra…, Tokens with repetition preserved (term frequency matters for documents)., _segment_tokens(), tokenize(), _entity_text(), _interleave(), _norm_route() (+13 more)

### Community 123 - "qqe"
Cohesion: 0.29
Nodes (7): AI(), ay(), Eie(), kI(), mb(), qqe(), zqe()

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

### Community 132 - "Unfold"
Cohesion: 0.33
Nodes (4): Commands, graphify, Unfold, Working rules

### Community 133 - "children"
Cohesion: 0.19
Nodes (9): cA(), children(), Hge(), hst(), r(), not(), n(), Uge() (+1 more)

### Community 134 - "vercel.json"
Cohesion: 0.40
Nodes (4): buildCommand, framework, installCommand, $schema

### Community 135 - "dependencies"
Cohesion: 0.12
Nodes (16): dependencies, clsx, @dagrejs/dagre, dompurify, lucide-react, mermaid, motion, next (+8 more)

### Community 137 - "BlobStore"
Cohesion: 0.04
Nodes (28): lexical_key(), Database, AsyncSession, PostgresProgressStore, Any, Best effort: a deleted project or an unstorable payload must not fail the job…, Fixed-window limiter. Phase 4 swaps in a DynamoDB (TTL items) implementation of…, RedisRateLimiter (+20 more)

### Community 138 - "test_architecture.py"
Cohesion: 0.22
Nodes (9): _modules(), _pkg(), Architecture rules from HLD §3 / backend/CLAUDE.md, enforced on the import…, module dotted name -> set of imported absolute module names., Analysis must never execute repository code (PRD §19)., test_core_and_domain_are_leaf_layers(), test_infrastructure_sdks_only_in_adapters(), test_no_subprocess_or_eval_of_repository_content() (+1 more)

### Community 139 - "destructEndLink"
Cohesion: 0.50
Nodes (4): countChar(), destructEndLink(), destructLink(), destructStartLink()

### Community 140 - "hT"
Cohesion: 0.40
Nodes (5): G9(), hT(), r(), qst(), sources()

### Community 141 - "match"
Cohesion: 0.07
Nodes (32): bqe(), buildUnableToPopLexerModeMessage(), buildUnexpectedCharactersMessage(), computeNewColumn(), Cqe(), createIndentationTokenInstance(), dedentMatcher(), EJ() (+24 more)

### Community 145 - "GraphBuilder"
Cohesion: 0.13
Nodes (20): _batches(), _derived_relationships(), GraphBuilder, _ir_nodes(), add(), _node_props(), project_uid(), Any (+12 more)

### Community 146 - "Technology Stack"
Cohesion: 0.18
Nodes (11): AI Orchestration, Backend, Code Intelligence, Data, Delivery, Evaluation, Frontend, Graph (+3 more)

### Community 147 - "compilerOptions"
Cohesion: 0.18
Nodes (10): compilerOptions, baseUrl, esModuleInterop, module, outDir, paths, rootDir, strict (+2 more)

### Community 148 - "split_text"
Cohesion: 0.29
Nodes (9): _make_chunk(), Split document text into overlapping chunks., Split text on paragraph boundaries into chunks of roughly `chunk_size`…, split_text(), TextChunk, Fuse several ranked id lists: score(d) = sum(1 / (k + rank))., reciprocal_rank_fusion(), test_rrf_prefers_items_ranked_by_both() (+1 more)

### Community 149 - "Runtime"
Cohesion: 0.08
Nodes (48): cleanup_project(), Remove blobs, vectors, graph nodes, progress and every database row of one…, Run (or resume) one analysis job. Never raises for job-level failures: they are…, run_analysis(), RunOutcome, AnalysisJobRow, ChunkRow, Runtime (+40 more)

### Community 150 - "Le"
Cohesion: 0.12
Nodes (20): aLe(), aq(), cKe(), i6(), iq(), Kh(), Ks(), l6() (+12 more)

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

### Community 163 - "HybridRetriever"
Cohesion: 0.16
Nodes (13): Base, Chunk, DeclarativeBase, QueryLog, SQLAlchemy ORM models (PostgreSQL)., A retrievable slice of a document., Audit trail of questions asked., HybridRetriever (+5 more)

### Community 164 - "evidence-drawer.tsx"
Cohesion: 0.08
Nodes (33): DrawerState, EvidenceContext, EvidenceContextValue, EvidenceDetail(), EvidenceItem, EvidenceProvider(), CiteChip(), CiteHandler (+25 more)

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
Cohesion: 0.18
Nodes (11): act(), cct(), dve(), fve(), Hct(), ict(), oct(), sct() (+3 more)

### Community 180 - "i0"
Cohesion: 0.23
Nodes (12): AW(), Fu(), gke(), i0(), IW(), LW(), mke(), pke() (+4 more)

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
Nodes (31): ProjectNav(), ProjectSection, ProjectTopbar(), SECTIONS, Skeleton(), apply(), LABEL, listeners (+23 more)

### Community 192 - "a1e"
Cohesion: 0.47
Nodes (6): a1e(), $lt(), n1e(), Olt(), r1e(), t1e()

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
Cohesion: 0.18
Nodes (17): EXPIRY_COPY, EXPIRY_WARNING_MS, ExpiryBanner(), ExpiryChip(), useRemaining(), useSession(), useToast(), Tooltip() (+9 more)

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
Nodes (23): get_settings(), BaseSettings, Application settings loaded from the environment., Runtime configuration for the API and the worker., Return the cached settings instance., Settings, configure_logging(), Configure structured-ish logging to stdout. (+15 more)

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
- **691 isolated node(s):** `unfold`, `name`, `version`, `private`, `description` (+686 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 2012 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **59 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `4. PostgreSQL schema (`unfold.persistence.postgres.models`, Alembic `migrations/`)` connect `compiler.py` to `Node`, `hybrid.py`, `builders.py`, `Settings`, `ProjectIR`, `_Builder`, `projects.py`, `indexing/chunking.py`, `join`, `Unfold — Low-Level Design (LLD)`?**
  _High betweenness centrality (0.131) - this node is a cross-community bridge._
- **Why does `20. HTTP API (`unfold.api`, prefix `/api/v1`)` connect `ProgressReporter` to `guide-page.tsx`, `projects.py`, `ErrorCode`, `Unfold — Low-Level Design (LLD)`?**
  _High betweenness centrality (0.130) - this node is a cross-community bridge._
- **Why does `Api` connect `guide-page.tsx` to `evidence-drawer.tsx`, `BlobStore`, `Settings`, `use-project-progress.ts`, `ProgressReporter`, `use-chat-thread.ts`, `api/types.ts`, `Runtime`, `landing-page.tsx`?**
  _High betweenness centrality (0.117) - this node is a cross-community bridge._
- **Are the 147 inferred relationships involving `t()` (e.g. with `ACTION()` and `aDe()`) actually correct?**
  _`t()` has 147 INFERRED edges - model-reasoned connections that need verification._
- **Are the 175 inferred relationships involving `r()` (e.g. with `mermaid.min.js` and `ACTION()`) actually correct?**
  _`r()` has 175 INFERRED edges - model-reasoned connections that need verification._
- **Are the 16 inferred relationships involving `s()` (e.g. with `C()` and `v()`) actually correct?**
  _`s()` has 16 INFERRED edges - model-reasoned connections that need verification._
- **Are the 160 inferred relationships involving `n()` (e.g. with `mermaid.min.js` and `addAnnotation()`) actually correct?**
  _`n()` has 160 INFERRED edges - model-reasoned connections that need verification._