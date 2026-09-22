# Graph Report - unfold  (2026-09-22)

## Corpus Check
- 295 files · ~220,125 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 22 file(s) not represented in the graph (top: (none) 8, .example 3, .j2 3)

## Summary
- 6856 nodes · 20966 edges · 259 communities (213 shown, 46 thin omitted)
- Extraction: 86% EXTRACTED · 14% INFERRED · 0% AMBIGUOUS · INFERRED: 2905 edges (avg confidence: 0.88)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- mermaid.min.js
- s
- t
- push
- g
- gZe
- gemini.py
- a
- retrievers.py
- builders.py
- Settings
- GraphQueries
- GeminiProvider
- handler
- ProjectIR
- _Builder
- project_ir/builder.py
- projects.py
- ErrorCode
- indexing/chunking.py
- AI_infrastructure.md
- api/types.ts
- gateway.py
- InventoryFile
- ObjectStore
- concat
- Chunk
- apply
- peekChar
- .toString
- p1e
- subrule
- Node
- .constructor
- langchain_adapter.py
- integration/conftest.py
- rgb
- documents.py
- text
- Cc
- cn
- api/schemas.py
- compiler.py
- treesitter.py
- test_retrieval_planner.py
- render.py
- uN
- Store
- edge
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
- IngestionService
- _Extractor
- node
- b9
- KeyPool
- web/package.json
- link
- lib/api/client.ts
- test_indexing_chunking.py
- forEach
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
- LA
- core/errors.py
- use-chat-thread.ts
- test_llm_gateway_policy.py
- ModelCapability
- ProviderBudget
- ingestion.py
- env.py
- query.py
- t
- includes
- is
- rules.py
- README_original.md
- ref_react
- devDependencies
- diagram-layout.ts
- _P
- nKe
- plan_query
- api/errors.py
- TokenBudget
- _Extractor
- complexity.py
- CircuitBreaker
- Unfold — High-Level Design (HLD)
- route.ts
- use-project-progress.ts
- pdf.py
- rrf_fuse
- extend
- RS
- _RustExtractor
- 4.1 PostgreSQL — operational system of record
- express_ts_prisma/package.json
- authService.ts
- tasks.py
- Jg
- atLeastOneInternal
- agents/models.py
- sufficiency.py
- test_api_sessions_projects.py
- Unfold — Product Requirements Document (PRD)
- app.ts
- postService.ts
- llm.py
- aa
- addDetails
- e7t
- a7t
- Vne
- children
- vercel.json
- find
- Wd
- .run
- test_architecture.py
- destructEndLink
- AGENTS.md
- match
- recursiveReduce
- Technology Stack
- compilerOptions
- split_text
- vH
- 3. Product Principles
- 8. Project Guide Requirements
- devDependencies
- calculateHorizontalSpace
- 46. Guide Evaluation
- 33. Key Architectural Decisions
- dependencies
- compilerOptions
- HybridRetriever
- evidence-drawer.tsx
- indexOf
- 8.1 Graph layers
- 24. API Surface
- uct
- i0
- 28. Scalability Strategy
- 4. Why Multiple Data Stores Are Justified
- 16. Deep Agents
- 7. Primary User Journey
- scripts
- project-topbar.tsx
- a1e
- C4
- linkNode
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
- addNamespace
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
- hN
- 15. Reciprocal Rank Fusion
- 34. Technical Depth Personalization
- 50. Evaluation in CI/CD
- 20. Guide Generation
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
- CLAUDE.md
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
- `6. Ingestion & security (`unfold.ingestion`, `unfold.security`)` --references--> `safe_extract()`  [INFERRED]
  docs/LLD.md → backend/src/unfold/ingestion/archive.py
- `17. LLM Gateway` --references--> `LLMRequest`  [INFERRED]
  docs/specs/ESD.md → backend/src/unfold/llm/schemas.py
- `15. Deep Agents (`unfold.agents`)` --references--> `ComplexityProfile`  [INFERRED]
  docs/LLD.md → backend/src/unfold/project_ir/complexity.py

## Import Cycles
- 1-file cycle: `backend/src/unfold/exports/render.py -> backend/src/unfold/exports/render.py`

## Communities (259 total, 46 thin omitted)

### Community 0 - "mermaid.min.js"
Cohesion: 0.00
Nodes (288): act(), activationCount(), addAll(), addAttributes(), addClassesToNamespace(), addCssStyles(), addEdge(), addEntity() (+280 more)

### Community 1 - "s"
Cohesion: 0.03
Nodes (87): $2e(), adt(), B2e(), bdt(), r(), Bmt(), buildDuplicateFoundError(), c2e() (+79 more)

### Community 2 - "t"
Cohesion: 0.02
Nodes (201): i(), ACTION(), aDe(), ake(), ate(), l(), awaitBuilderState(), awaitDocumentState() (+193 more)

### Community 3 - "push"
Cohesion: 0.03
Nodes (166): a5(), a9e(), a$e(), add(), addDescription(), addInterface(), addLayoutHint(), addNodeFromVertex() (+158 more)

### Community 4 - "g"
Cohesion: 0.07
Nodes (81): _5e(), alternatives(), ble(), Bz(), die(), Dit(), m(), dle() (+73 more)

### Community 5 - "gZe"
Cohesion: 0.07
Nodes (71): beginGroup(), endGroup(), endGroups(), g1e(), gZe(), b(), Be(), de() (+63 more)

### Community 6 - "gemini.py"
Cohesion: 0.09
Nodes (34): AuthError, BadRequest, InvalidOutput, parse_json_object(), ProviderCallError, Exception, RateLimited, Parse a JSON object from model text (tolerates ```json fences). Raises… (+26 more)

### Community 7 - "a"
Cohesion: 0.05
Nodes (116): addLink(), addNode(), addRelationship(), aE(), d(), f(), aLe(), _at() (+108 more)

### Community 8 - "retrievers.py"
Cohesion: 0.10
Nodes (30): RetrievalConfig, Every first-stage retriever failed — there is no honest evidence to return., RetrievalFailed, HybridRetriever, _ms(), _node_ref(), BaseException, Run the applicable retrievers concurrently; returns (attempted, failed). (+22 more)

### Community 9 - "builders.py"
Cohesion: 0.07
Nodes (61): DiagramOut, _agg_label(), ai_pipeline(), architecture(), build_all(), data_model(), deployment(), _EdgeAgg (+53 more)

### Community 10 - "Settings"
Cohesion: 0.04
Nodes (82): asyncio, create_app(), FastAPI, FastAPI composition root (HLD §2 `api`). Builds one Runtime per process; never…, get_settings(), BaseSettings, SecretStr, Typed, environment-driven settings (LLD §2). One instance per process via… (+74 more)

### Community 11 - "GraphQueries"
Cohesion: 0.06
Nodes (51): _batches(), _derived_relationships(), GraphBuilder, _ir_nodes(), add(), _node_props(), project_uid(), Any (+43 more)

### Community 12 - "GeminiProvider"
Cohesion: 0.08
Nodes (26): APIError, _convert(), Any, JSON-Schema normalisation for provider structured output / tool parameters (LLD…, Return a self-contained (no `$ref`) schema restricted to the portable keyword…, _resolve_ref(), to_provider_schema(), GeminiProvider (+18 more)

### Community 13 - "handler"
Cohesion: 0.08
Nodes (52): $7(), callFunction(), consume(), consumeArg(), consumeArgs(), consumeSpaces(), countExpansion(), expandAfterFuture() (+44 more)

### Community 14 - "ProjectIR"
Cohesion: 0.06
Nodes (58): Any, Exception, Base error. `http_status` is used by the API; `failure_stage` by the job runner., UnfoldError, build_ir(), Normalize one repository's inventory + analysis into a `ProjectIR` (pure,…, Referential-integrity problems of an IR (empty list = valid). Used by tests and…, validate_ir() (+50 more)

### Community 15 - "_Builder"
Cohesion: 0.10
Nodes (21): _basename(), _Builder, _canon_tech(), _dirname(), _is_code(), _is_dockerfile(), _join(), _norm_name() (+13 more)

### Community 16 - "project_ir/builder.py"
Cohesion: 0.11
Nodes (39): _norm_words(), _stage_of(), Provenance, Where a fact came from. Deterministic facts outrank inferred ones everywhere…, _image_rule(), _join_route(), normalize_route(), Project IR builder (LLD §8, ESD §9): `Inventory` + `RepositoryAnalysis` →… (+31 more)

### Community 17 - "projects.py"
Cohesion: 0.06
Nodes (61): _create(), create_github_project(), create_zip_project(), delete_project(), _dump(), get_project(), job_out(), latest_job() (+53 more)

### Community 18 - "ErrorCode"
Cohesion: 0.07
Nodes (71): ErrorCode, IngestionError, InputError, StrEnum, SecurityRejection, _Budget, _common_root(), _copy_limited() (+63 more)

### Community 19 - "indexing/chunking.py"
Cohesion: 0.09
Nodes (43): content_hash(), chunk_file(), _config_units(), content_type_for(), _dockerfile_units(), _ext(), _fan_in(), _gap_units() (+35 more)

### Community 20 - "AI_infrastructure.md"
Cohesion: 0.04
Nodes (53): 11. Local Embeddings, 12. Qdrant Vector Layer, 13. Native Lexical Retrieval, 14. Native Hybrid RAG, 16. Cross-Encoder Reranking, 17. Structural Retrieval, 18. Neo4j Graph Intelligence, 19. Graph Communities (+45 more)

### Community 21 - "api/types.ts"
Cohesion: 0.04
Nodes (66): ProjectContextValue, SessionContext, SessionContextValue, streamPaths, ArchitectureOut, ArtifactKind, ArtifactOut, ArtifactStatus (+58 more)

### Community 22 - "gateway.py"
Cohesion: 0.09
Nodes (37): GenerationError, ProviderCapacityError, ProviderError, cache_key(), Any, LLM response cache (LLD §13, AI_infrastructure §53). Only for `req.cacheable`…, ResponseCache, _aclose() (+29 more)

### Community 23 - "InventoryFile"
Cohesion: 0.04
Nodes (50): AnalysisContext, build_context(), _directories(), find_line(), _go_modules(), _is_under(), LanguageAnalyzer, line_of() (+42 more)

### Community 24 - "ObjectStore"
Cohesion: 0.06
Nodes (19): lifespan(), StoreUnavailable, LexicalIndexError, A BM25 artifact could not be built, serialized or loaded., lexical_key(), LexicalArtifact, LexicalIndexCache, Any (+11 more)

### Community 25 - "concat"
Cohesion: 0.05
Nodes (66): b5e(), buildLeftRecursionError(), checkIsTarget(), concat(), Cse(), dD(), Fc(), fD() (+58 more)

### Community 26 - "Chunk"
Cohesion: 0.06
Nodes (31): ContentType, chunk_payload(), DenseIndex, embedding_cache_key(), point_id(), _project_filter(), Any, ndarray (+23 more)

### Community 27 - "apply"
Cohesion: 0.08
Nodes (26): a8(), addActor(), addBox(), addMessage(), addNote(), addSignal(), apply(), autoWrap() (+18 more)

### Community 28 - "peekChar"
Cohesion: 0.12
Nodes (45): alternative(), assertion(), atom(), atomEscape(), characterClass(), characterClassEscape(), classAtom(), classEscape() (+37 more)

### Community 29 - ".toString"
Cohesion: 0.03
Nodes (101): _5t(), addDocument(), addExportedSymbol(), addLocalSymbol(), aXe(), build(), buildDocuments(), buildKeywordPattern() (+93 more)

### Community 30 - "p1e"
Cohesion: 0.10
Nodes (43): Ab(), an(), cB(), Cle(), d1e(), db(), dc(), defineClass() (+35 more)

### Community 31 - "subrule"
Cohesion: 0.05
Nodes (47): after(), BACKTRACK(), before(), construct(), constructInfix(), cstPostNonTerminal(), doParse(), exportLexerState() (+39 more)

### Community 32 - "Node"
Cohesion: 0.08
Nodes (20): add(), _camelize(), _CSharpExtractor, _GoExtractor, _JavaExtractor, _join_path(), _JvmMixin, _KotlinExtractor (+12 more)

### Community 33 - ".constructor"
Cohesion: 0.14
Nodes (15): _4e(), brt(), copy(), g4e(), Grt(), hrt(), l4e(), M4e() (+7 more)

### Community 34 - "langchain_adapter.py"
Cohesion: 0.06
Nodes (37): AIMessage, AsyncCallbackManagerForLLMRun, GatewayChatModel, _run(), _sync(), Any, BaseModel, LangChain chat model over the LLM Gateway, used by Deep Agents only (LLD §13,… (+29 more)

### Community 35 - "integration/conftest.py"
Cohesion: 0.09
Nodes (25): argparse, asyncpg, Tests, _ensure_database(), migrated_database(), project_id(), fixture, Integration fixtures: real data stores from `docker compose up -d` (ports from… (+17 more)

### Community 36 - "rgb"
Cohesion: 0.05
Nodes (52): _8(), Alt(), b8(), Bee(), brighter(), clamp(), cM(), Cte() (+44 more)

### Community 37 - "documents.py"
Cohesion: 0.13
Nodes (22): delete_document(), get_document(), list_documents(), ArqRedis, AsyncSession, delete, get, post (+14 more)

### Community 38 - "text"
Cohesion: 0.15
Nodes (28): arcTo(), closePath(), _He(), iHe(), Kpt(), lineTo(), measureText(), moveTo() (+20 more)

### Community 39 - "Cc"
Cohesion: 0.08
Nodes (27): Cc(), createDescription(), createInfoDescriptions(), createScope(), documentationLinkRenderer(), documentationTagRenderer(), findDeclarationNodes(), findDeclarations() (+19 more)

### Community 40 - "cn"
Cohesion: 0.06
Nodes (58): 3. Logical modules (Python package `unfold`), ArchitecturePage(), ArchitectureWorkspace(), ChatPage(), ChatWorkspace(), Conversation(), prompts, ErrorPanel() (+50 more)

### Community 41 - "api/schemas.py"
Cohesion: 0.08
Nodes (46): create_session(), current_session(), get, post, Response, RuntimeDep, SessionDep, Anonymous sessions (LLD §20, PRD §3.8): random token in an HttpOnly cookie;… (+38 more)

### Community 42 - "compiler.py"
Cohesion: 0.08
Nodes (52): estimate_tokens(), Token estimation + budgeting for compiled contexts (LLD §12). Tokens ≈…, Cut `text` at a line boundary so that result (incl. marker) fits `max_tokens`.…, truncate_to_tokens(), _clean_summary(), compile_context(), _Compiler, _drop_lowest() (+44 more)

### Community 43 - "treesitter.py"
Cohesion: 0.11
Nodes (38): Python analyzer on the stdlib `ast` (LLD §7, ESD §8.2). Parses text only —…, _sql_tables(), _analyze_prisma(), _clean_comment(), _go_local_name(), _go_package(), _npm_package(), Tree-sitter analyzer for non-Python source languages (LLD §7, ESD §8.1–§8.2).… (+30 more)

### Community 44 - "test_retrieval_planner.py"
Cohesion: 0.13
Nodes (24): chunk_id(), diagram_id(), _digest(), entity_id(), new_uuid(), UUID, Stable, content/context-derived identifiers (LLD §3)., Deterministic id for an IR/graph entity: recomputation yields the same id… (+16 more)

### Community 45 - "render.py"
Cohesion: 0.07
Nodes (49): DiagramExport, ExportChatMessage, ExportProject, BaseModel, Inputs for the export templates (LLD §19). The worker maps persisted rows onto…, _claim_rows(), _clean(), _code_caption() (+41 more)

### Community 46 - "uN"
Cohesion: 0.04
Nodes (65): aje(), Ane(), Ba(), bje(), bn(), BYe(), cje(), count() (+57 more)

### Community 47 - "Store"
Cohesion: 0.08
Nodes (26): New(), Connect(), Store, newCode(), main(), TestHealth(), go_pkg_context, go_pkg_crypto_rand (+18 more)

### Community 48 - "edge"
Cohesion: 0.12
Nodes (36): A9(), b0e(), Bge(), Bot(), C0e(), Dge(), E9(), edge() (+28 more)

### Community 49 - "sd"
Cohesion: 0.10
Nodes (36): aD(), aGe(), Ax(), bd(), bh(), cH(), d_e(), eD() (+28 more)

### Community 50 - "validateDocument"
Cohesion: 0.07
Nodes (32): buildLeafNode(), cancelWrite(), dequeue(), enqueue(), getSource(), handleException(), Ige(), MH() (+24 more)

### Community 51 - "VectorStore"
Cohesion: 0.14
Nodes (7): AsyncSession, AsyncSession, Create the collection if it does not exist., Insert or replace chunk vectors., Return (chunk id, score) pairs for the nearest vectors., Stores and searches chunk embeddings in a Qdrant collection., VectorStore

### Community 52 - "join"
Cohesion: 0.06
Nodes (86): _2(), A4(), ac(), bce(), boe(), bP(), bse(), circle() (+78 more)

### Community 53 - "architecture.md"
Cohesion: 0.06
Nodes (35): 10. Retrieval Architecture, 11.1 Guide mode, 11.2 Chat mode, 11.3 Deep investigation mode, 11. Retrieval Modes, 12.1 Token budgeting, 12. Context Engineering Architecture, 13. LangGraph HLD (+27 more)

### Community 54 - "frontend/package.json"
Cohesion: 0.04
Nodes (51): dependencies, clsx, @dagrejs/dagre, dompurify, lucide-react, mermaid, motion, next (+43 more)

### Community 55 - "g3"
Cohesion: 0.10
Nodes (35): ast(), e0e(), Fge(), filterNodes(), a(), fot(), g3(), G9() (+27 more)

### Community 56 - "ESD.md"
Cohesion: 0.05
Nodes (42): 10. Graph Builder, 11.1 Code-aware chunks, 11.2 Hierarchical summaries, 11. Chunking and Indexing, 12. Native Hybrid RAG, 13. Query Planner, 14. Context Compiler, 15. LangGraph Orchestration (+34 more)

### Community 57 - "communities.py"
Cohesion: 0.14
Nodes (24): _add_weight(), _build_graph(), detect(), _dir(), entity_subsystems(), fan_in(), _label_hint(), _merge_small() (+16 more)

### Community 58 - "Embedder"
Cohesion: 0.11
Nodes (15): Embedder, embedder_for(), Any, NDArray, Process-wide embedder singleton kept in `Runtime.extras`., ModelUnavailable, A local model (embedder / cross-encoder) could not be loaded or failed at…, CrossEncoderReranker (+7 more)

### Community 59 - "inventory.py"
Cohesion: 0.15
Nodes (24): build_inventory(), decode_text(), _describe(), _ext(), _is_build_output_dir(), _is_entrypoint(), _is_text(), language_for() (+16 more)

### Community 60 - "planner.py"
Cohesion: 0.07
Nodes (39): RetrievalIntent, RetrievalMode, default_breadth(), Breadth, Versioned retrieval configuration (LLD §11, ESD §12 "Reciprocal Rank Fusion",…, SourceWeights, SubsystemLike, Plan + retrieve in one call (`k` defaults by plan breadth). (+31 more)

### Community 61 - "IngestionService"
Cohesion: 0.18
Nodes (10): finalize(), 14. Orchestration (`unfold.orchestration`), Document, An uploaded source document., extract_text(), IngestionService, Extract plain text from a PDF or Markdown/plain-text upload., Owns the document lifecycle in PostgreSQL and Qdrant. (+2 more)

### Community 62 - "_Extractor"
Cohesion: 0.09
Nodes (30): AnnAssign, Assign, AST, AsyncFunctionDef, AugAssign, _annotation_type(), _class_assign(), _const_str() (+22 more)

### Community 63 - "node"
Cohesion: 0.12
Nodes (28): aot(), i(), n(), c8(), e8(), got(), h8(), n() (+20 more)

### Community 64 - "b9"
Cohesion: 0.11
Nodes (23): b9(), cot(), d(), h(), u(), cT(), d0e(), eot() (+15 more)

### Community 65 - "KeyPool"
Cohesion: 0.14
Nodes (13): KeyPool, Any, Redis, Credential pool per provider (LLD §13, ESD §18). Keys are credentials, not…, Per-key health (index only, never key material)., Least-recently-used healthy key index, or None when every key is disabled or…, Mark a 429. `cooldown_s` is applied to this key only when quotas are per key., Credential rejected: take it out of rotation (auto re-enabled after… (+5 more)

### Community 66 - "web/package.json"
Cohesion: 0.08
Nodes (24): dependencies, react, react-dom, devDependencies, @types/react, @types/react-dom, typescript, vite (+16 more)

### Community 67 - "link"
Cohesion: 0.07
Nodes (35): allElements(), u(), checkbox(), code(), codespan(), del(), eu(), flat() (+27 more)

### Community 68 - "lib/api/client.ts"
Cohesion: 0.15
Nodes (13): API_PREFIX, apiClient, ApiClientOptions, ApiError, FetchLike, isErrorEnvelope(), networkError(), RequestOptions (+5 more)

### Community 69 - "test_indexing_chunking.py"
Cohesion: 0.38
Nodes (12): chunk_project(), Chunks for every indexable file of the IR whose text is available (`path →…, _by(), _file(), _ir(), _sym(), test_compose_services_split(), test_deterministic_ids_and_hashes() (+4 more)

### Community 70 - "forEach"
Cohesion: 0.04
Nodes (61): A2(), addAnnotation(), addAstNodeRegionWithAssignmentsTo(), addClass(), addMember(), addMembers(), addSingleLink(), addSubGraph() (+53 more)

### Community 71 - "enableRecording"
Cohesion: 0.11
Nodes (23): atLeastOneInternalRecord(), atLeastOneSepFirstInternalRecord(), Bq(), computeLookaheadFunc(), consumeInternalRecord(), disableRecording(), Dq(), enableRecording() (+15 more)

### Community 72 - "Ug"
Cohesion: 0.25
Nodes (8): buildAlternationAmbiguityError(), buildAlternationPrefixAmbiguityError(), buildEarlyExitMessage(), buildMismatchTokenMessage(), e4(), eae(), pAe(), Ug()

### Community 73 - "code-view.tsx"
Cohesion: 0.12
Nodes (24): TrustedHtml(), CodeView(), CiteHandler, MarkdownView(), MermaidView(), ALIASES, getHighlighter(), highlightToHtml() (+16 more)

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
Nodes (77): a5e(), aAe(), addElement(), addRequirement(), a(), bezierCurveTo(), buildNamespaceConflictError(), c0() (+69 more)

### Community 79 - "Unfold — Low-Level Design (LLD)"
Cohesion: 0.08
Nodes (23): Architecture rules (enforced by `tests/unit/test_architecture.py`), backend/ — agent operating manual, Commands (run from `backend/`), Non-negotiables, current(), 11. Retrieval (`unfold.retrieval`), 12. Context Compiler (`unfold.context.compiler`), 15. Deep Agents (`unfold.agents`) (+15 more)

### Community 80 - "_JsExtractor"
Cohesion: 0.13
Nodes (5): _JsExtractor, (resolved repository path, external, npm package) for a module specifier., Handle `require("x")` / `import("x")`; returns True when `call` was an import., The function node of `fn`, `memo(fn)`, `React.forwardRef(fn)`, ... (None when…, For `router.route('/x').get(h).post(h2)` return ("router", "/x").

### Community 81 - "nodes"
Cohesion: 0.09
Nodes (29): $9(), dot(), F9(), hT(), r(), iot(), isDirected(), isLeaf() (+21 more)

### Community 82 - "LA"
Cohesion: 0.06
Nodes (43): buildLookaheadForAlternation(), buildLookaheadForOptional(), canPerformInRuleRecovery(), canRecoverWithSingleTokenDeletion(), canRecoverWithSingleTokenInsertion(), canTokenTypeBeDeletedInRecovery(), canTokenTypeBeInsertedInRecovery(), CONSUME1() (+35 more)

### Community 83 - "core/errors.py"
Cohesion: 0.10
Nodes (39): CurrentSession, enforce_rate_limit(), get_runtime(), owned_chat(), owned_project(), parse_uuid(), Request, RuntimeDep (+31 more)

### Community 84 - "use-chat-thread.ts"
Cohesion: 0.17
Nodes (22): postEventStream(), ChatEvent, ChatMode, ActivityKind, ActivityStep, applyChatEvent(), assistantKey(), ChatAction (+14 more)

### Community 85 - "test_llm_gateway_policy.py"
Cohesion: 0.13
Nodes (42): Priority, _all_text(), _gemini(), _groq(), h(), Harness, _limits(), Any (+34 more)

### Community 86 - "ModelCapability"
Cohesion: 0.09
Nodes (13): ModelCapability, Provider, Any, Protocol, Provider protocol + typed provider exceptions (LLD §13). Providers translate…, Resolve `CAPABILITY_TIERS[capability]` through a provider's tier→model map…, One vendor. `key_index` selects the credential (0-based); keys themselves never…, tier_models() (+5 more)

### Community 87 - "ProviderBudget"
Cohesion: 0.15
Nodes (11): ProviderBudget, Redis, Provider capacity budget in Redis (LLD §13, AI_infrastructure §8). All checks…, One atomic attempt. Returns (reservation, wait_hint_seconds, reason)., Reserve capacity, waiting in ≤1 s steps up to the priority wait budget. None →…, The call reached the provider: keep the request counted, replace the token…, The call never reached the provider: undo the reservation entirely., Current usage for diagnostics/tests (does not prune expired entries). (+3 more)

### Community 88 - "ingestion.py"
Cohesion: 0.13
Nodes (17): Application settings loaded from the environment., embed_query(), embed_texts(), _model(), Local sentence-transformer embeddings., Embed a batch of texts into normalized dense vectors., Embed a single query., Document ingestion: persist uploads, extract text, chunk, embed and index. (+9 more)

### Community 89 - "env.py"
Cohesion: 0.15
Nodes (10): alembic, do_run_migrations(), include_object(), Alembic async environment. URL comes from unfold Settings (DATABASE_URL), never…, run_async_migrations(), run_migrations_offline(), run_migrations_online(), Connection (+2 more)

### Community 90 - "query.py"
Cohesion: 0.12
Nodes (16): arq, get_db(), get_queue(), ArqRedis, AsyncSession, Shared FastAPI dependencies., Yield a database session per request., Return an arq Redis pool used to enqueue background jobs. (+8 more)

### Community 91 - "t"
Cohesion: 0.14
Nodes (33): b(), c1e(), DL(), getLinks(), gte(), IL(), I(), k() (+25 more)

### Community 92 - "includes"
Cohesion: 0.09
Nodes (27): blockTokens(), cIt(), ck(), e9e(), eVe(), _getExpansion(), gT(), hb() (+19 more)

### Community 93 - "is"
Cohesion: 0.07
Nodes (23): br(), a(), o(), create(), createAsync(), createLangiumDocument(), every(), fromString() (+15 more)

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

### Community 99 - "_P"
Cohesion: 0.10
Nodes (27): a(), addEntry(), Ar(), arc(), b4(), fse(), Fv(), g4() (+19 more)

### Community 100 - "nKe"
Cohesion: 0.20
Nodes (14): a6t(), dP(), fP(), gee(), getMaxDimension(), Kae(), mHe(), nHe() (+6 more)

### Community 101 - "plan_query"
Cohesion: 0.27
Nodes (24): plan_query(), Breadth, Deterministic retrieval plan. `intent` overrides classification (guide sections…, assess(), follow_up_plan(), Any, query_keys(), Narrow plan targeting the gaps found by `assess`, or None when there is nothing… (+16 more)

### Community 102 - "api/errors.py"
Cohesion: 0.21
Nodes (13): _envelope(), install_error_handling(), http_error(), unfold_error(), unhandled(), validation_error(), FastAPI, Request (+5 more)

### Community 103 - "TokenBudget"
Cohesion: 0.22
Nodes (3): A running token allowance. `take` succeeds only when the item fits entirely., Account for mandatory content (objective) even if it overflows., TokenBudget

### Community 104 - "_Extractor"
Cohesion: 0.12
Nodes (6): _Extractor, emit(), emit(), _is_pascal(), Shared per-file walking state; subclasses implement one grammar family., Byte offset after leading annotations / attributes / decorators of a…

### Community 105 - "complexity.py"
Cohesion: 0.19
Nodes (17): AgentBudget, ComplexityProfile, ComplexitySignals, _investigators(), _lin(), _log_ratio(), BaseModel, Deterministic project complexity profile (LLD §8, AI infrastructure §31–§32).… (+9 more)

### Community 106 - "CircuitBreaker"
Cohesion: 0.18
Nodes (9): CircuitBreaker, CircuitState, Any, Redis, StrEnum, Circuit breaker per `provider:model`, shared across workers through Redis (LLD…, _text(), test_circuit_half_open_admits_exactly_one_probe() (+1 more)

### Community 107 - "Unfold — High-Level Design (HLD)"
Cohesion: 0.17
Nodes (9): 1. What the system does, 4. End-to-end analysis flow, 5. Key design decisions (as built), 6. Quality attributes → mechanisms, 7. Out of scope for this phase, Unfold — High-Level Design (HLD), 25. Guiding Product Test, Prepare your project. (+1 more)

### Community 108 - "route.ts"
Cohesion: 0.15
Nodes (14): DELETE, dynamic, envelope(), GET, HEAD, HOP_BY_HOP, OPTIONS, PATCH (+6 more)

### Community 109 - "use-project-progress.ts"
Cohesion: 0.12
Nodes (19): backoffDelay(), parseJSON(), readEventStream(), SSEMessage, SSEParser, subscribeEventStream(), Subscription, JobOut (+11 more)

### Community 110 - "pdf.py"
Cohesion: 0.15
Nodes (15): ExportError, _block_network(), _csp(), _mermaid_source(), PdfRenderer, PdfResult, HTML → PDF with headless Chromium (LLD §19). Runs in the worker `export` queue.…, One Chromium per process, launched lazily on first use; each render gets a… (+7 more)

### Community 111 - "rrf_fuse"
Cohesion: 0.17
Nodes (17): FusedCandidate, max_fused_score(), merge_variants(), Weighted Reciprocal Rank Fusion (LLD §11, ESD §12). Pure and deterministic.…, Fuse ranked chunk-id lists. A ranking with weight ≤ 0 (or missing from…, Merge the rankings of several query variants run against one retriever into a…, Best achievable fused score (rank 1 in every positively weighted ranking)., rrf_fuse() (+9 more)

### Community 112 - "extend"
Cohesion: 0.15
Nodes (13): extend(), havingBaseSizing(), havingBaseStyle(), havingCrampedStyle(), havingSize(), havingStyle(), i(), withColor() (+5 more)

### Community 113 - "RS"
Cohesion: 0.17
Nodes (18): accept(), addToResyncTokens(), AS(), Cae(), getCurrRuleFullName(), getGAstProductions(), getNextPossibleTokenTypes(), jA() (+10 more)

### Community 115 - "4.1 PostgreSQL — operational system of record"
Cohesion: 0.14
Nodes (14): 4.1 PostgreSQL — operational system of record, 4.2 Neo4j — project knowledge graph, 4.3 Qdrant — dense semantic retrieval index, 4.4 Native BM25 index, 4.5 Redis, 4.6 Object storage, 4. Core Data Stores, analysis_jobs (+6 more)

### Community 116 - "express_ts_prisma/package.json"
Cohesion: 0.14
Nodes (13): description, typescript, main, name, private, version, jest, prisma (+5 more)

### Community 117 - "authService.ts"
Cohesion: 0.21
Nodes (11): HttpError, credentials, loginHandler(), registerHandler(), router, login(), register(), signToken() (+3 more)

### Community 118 - "tasks.py"
Cohesion: 0.22
Nodes (7): arq_connections, ingest_document(), arq background worker: document ingestion jobs., Chunk, embed and index one uploaded document., arq worker configuration., WorkerSettings, logging

### Community 119 - "Jg"
Cohesion: 0.04
Nodes (55): a6(), assign(), assignWithoutOverride(), c6(), convert(), createDehyrationContext(), createGrammarElementIdMap(), createHydrationContext() (+47 more)

### Community 120 - "atLeastOneInternal"
Cohesion: 0.03
Nodes (78): AT_LEAST_ONE(), AT_LEAST_ONE1(), AT_LEAST_ONE2(), AT_LEAST_ONE3(), AT_LEAST_ONE4(), AT_LEAST_ONE5(), AT_LEAST_ONE6(), AT_LEAST_ONE7() (+70 more)

### Community 121 - "agents/models.py"
Cohesion: 0.33
Nodes (8): ComponentFinding, FindingFact, InvestigationFinding, BaseModel, Deep Agent output contracts (LLD §15). Findings feed the guide planner, section…, WorkflowFinding, WorkflowStep, 17. Diagrams (`unfold.diagrams`)

### Community 122 - "sufficiency.py"
Cohesion: 0.16
Nodes (18): _keep(), Code-aware tokenizer shared by BM25 indexing and querying (LLD §10, AI infra…, Tokens with repetition preserved (term frequency matters for documents)., Same transformation as documents, de-duplicated in first-seen order., _segment_tokens(), tokenize(), tokenize_query(), _identifiers() (+10 more)

### Community 123 - "test_api_sessions_projects.py"
Cohesion: 0.32
Nodes (12): app(), client(), AsyncClient, FastAPI, fixture, _session(), test_github_project_lifecycle_and_ownership(), test_health_and_ready() (+4 more)

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

### Community 128 - "aa"
Cohesion: 0.18
Nodes (11): aa(), bct(), bu(), M5(), mct(), Nmt(), pmt(), PTe() (+3 more)

### Community 129 - "addDetails"
Cohesion: 0.43
Nodes (7): addALink(), addDetails(), addLinks(), addProperties(), getActor(), insertLinks(), insertProperties()

### Community 130 - "e7t"
Cohesion: 0.29
Nodes (7): b7t(), e7t(), eX(), i7t(), k7t(), l7t(), p7t()

### Community 131 - "a7t"
Cohesion: 0.33
Nodes (6): a7t(), d7t(), h7t(), n7t(), s7t(), u7t()

### Community 132 - "Vne"
Cohesion: 0.33
Nodes (6): Bb(), by(), gE(), Jl(), Vne(), XM()

### Community 133 - "children"
Cohesion: 0.24
Nodes (6): cA(), children(), not(), n(), Uge(), t()

### Community 134 - "vercel.json"
Cohesion: 0.40
Nodes (4): buildCommand, framework, installCommand, $schema

### Community 135 - "find"
Cohesion: 0.25
Nodes (9): find(), findIndex(), findNode(), isEmpty(), iterator(), mP(), normalizeUri(), [Symbol.iterator]() (+1 more)

### Community 138 - "test_architecture.py"
Cohesion: 0.24
Nodes (8): _modules(), _pkg(), Architecture rules from HLD §3 / backend/CLAUDE.md, enforced on the import…, module dotted name -> set of imported absolute module names., Analysis must never execute repository code (PRD §19)., test_core_and_domain_are_leaf_layers(), test_no_subprocess_or_eval_of_repository_content(), test_only_composition_roots_import_api_or_worker_entrypoints()

### Community 139 - "destructEndLink"
Cohesion: 0.50
Nodes (4): countChar(), destructEndLink(), destructLink(), destructStartLink()

### Community 141 - "match"
Cohesion: 0.06
Nodes (37): bqe(), buildUnableToPopLexerModeMessage(), buildUnexpectedCharactersMessage(), bW(), computeNewColumn(), Cqe(), createIndentationTokenInstance(), dedentMatcher() (+29 more)

### Community 146 - "Technology Stack"
Cohesion: 0.18
Nodes (11): AI Orchestration, Backend, Code Intelligence, Data, Delivery, Evaluation, Frontend, Graph (+3 more)

### Community 147 - "compilerOptions"
Cohesion: 0.18
Nodes (10): compilerOptions, baseUrl, esModuleInterop, module, outDir, paths, rootDir, strict (+2 more)

### Community 148 - "split_text"
Cohesion: 0.29
Nodes (9): _make_chunk(), Split document text into overlapping chunks., Split text on paragraph boundaries into chunks of roughly `chunk_size`…, split_text(), TextChunk, Fuse several ranked id lists: score(d) = sum(1 / (k + rank))., reciprocal_rank_fusion(), test_rrf_prefers_items_ranked_by_both() (+1 more)

### Community 150 - "vH"
Cohesion: 0.26
Nodes (12): aq(), computeRuleType(), Cv(), defineRule(), jh(), Kh(), Ks(), nq() (+4 more)

### Community 152 - "3. Product Principles"
Cohesion: 0.20
Nodes (10): 3.1 Repository → Understanding, 3.2 Evidence before explanation, 3.3 Breadth first, depth on demand, 3.4 Adaptive, not template-bound, 3.5 Visual learning is core, 3.6 User level matters, 3.7 Large repositories require selective completeness, 3.8 No permanent account dependency (+2 more)

### Community 153 - "8. Project Guide Requirements"
Cohesion: 0.20
Nodes (10): 8.1 Universal Backbone, 8.2 Dynamic Core Chapters, 8.3 Reading Length, 8.4 Explanation Depth, 8.5 Evidence, 8. Project Guide Requirements, DevOps project, Distributed backend (+2 more)

### Community 154 - "devDependencies"
Cohesion: 0.20
Nodes (10): devDependencies, jest, prisma, supertest, ts-jest, ts-node-dev, @types/express, @types/jest (+2 more)

### Community 156 - "calculateHorizontalSpace"
Cohesion: 0.18
Nodes (18): calculateHorizontalSpace(), calculateOffsetByRotation(), calculateSpaceIfDrawnHorizontally(), calculateSpaceIfDrawnVertical(), calculateVerticalSpace(), getDrawableElementsForBottomAxis(), getDrawableElementsForLeftAxis(), getDrawableElementsForTopAxis() (+10 more)

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
Cohesion: 0.18
Nodes (11): Base, Chunk, DeclarativeBase, QueryLog, A retrievable slice of a document., Audit trail of questions asked., HybridRetriever, Combines BM25 over chunk text with Qdrant vector search. (+3 more)

### Community 164 - "evidence-drawer.tsx"
Cohesion: 0.09
Nodes (27): DrawerState, EvidenceContext, EvidenceContextValue, EvidenceDetail(), EvidenceItem, CiteChip(), InlineText(), renderNodes() (+19 more)

### Community 172 - "indexOf"
Cohesion: 0.06
Nodes (29): addHiddenNodes(), addParents(), c_e(), eKe(), getRefNode(), Hp(), indexOf(), iq() (+21 more)

### Community 174 - "8.1 Graph layers"
Cohesion: 0.25
Nodes (8): 8.1 Graph layers, 8.2 Graph construction strategy, 8. Knowledge Graph Architecture, Layer 0 — Project, Layer 1 — Subsystems, Layer 2 — Components, Layer 3 — Implementation, Layer 4 — Evidence

### Community 175 - "24. API Surface"
Cohesion: 0.25
Nodes (8): 24. API Surface, Chat, Export, Graph/visuals, Guide, Progress streaming, Projects, Sessions

### Community 177 - "uct"
Cohesion: 0.40
Nodes (5): ef(), Hct(), ict(), uct(), X5()

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

### Community 191 - "project-topbar.tsx"
Cohesion: 0.07
Nodes (32): LandingPage(), submit(), stages, ProgressContent(), ProjectProgressPage(), ProjectNav(), ProjectSection, ProjectTopbar() (+24 more)

### Community 192 - "a1e"
Cohesion: 0.47
Nodes (6): a1e(), $lt(), n1e(), Olt(), r1e(), t1e()

### Community 193 - "C4"
Cohesion: 0.40
Nodes (5): C4(), LP(), TP(), wKe(), x4()

### Community 195 - "linkNode"
Cohesion: 0.50
Nodes (4): deserialize(), fromModel(), Ha(), linkNode()

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

### Community 210 - "addNamespace"
Cohesion: 0.40
Nodes (5): addNamespace(), createNamespaceNode(), getAncestorIds(), linkParentChild(), resolveQualifiedId()

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

### Community 238 - "hN"
Cohesion: 0.50
Nodes (4): hN(), setAccDescription(), setAccTitle(), setDiagramTitle()

### Community 240 - "15. Reciprocal Rank Fusion"
Cohesion: 0.50
Nodes (4): 15. Reciprocal Rank Fusion, Conceptual architecture question, Symbol lookup, Workflow tracing

### Community 241 - "34. Technical Depth Personalization"
Cohesion: 0.50
Nodes (4): 34. Technical Depth Personalization, Beginner, Experienced, Intermediate

### Community 242 - "50. Evaluation in CI/CD"
Cohesion: 0.50
Nodes (4): 50. Evaluation in CI/CD, Offline PR gate, Online bounded eval, Release benchmark

### Community 245 - "20. Guide Generation"
Cohesion: 0.50
Nodes (4): 20.1 Guide planner, 20.2 Section generation, 20.3 Consistency pass, 20. Guide Generation

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
Cohesion: 0.14
Nodes (15): get_settings(), BaseSettings, Runtime configuration for the API and the worker., Return the cached settings instance., Settings, configure_logging(), Configure structured-ish logging to stdout., create_app() (+7 more)

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
Nodes (16): nextConfig, frontend_src_app_globals, metadata, mono, sans, serif, AppProviders(), SessionProvider() (+8 more)

## Knowledge Gaps
- **672 isolated node(s):** `unfold`, `name`, `version`, `private`, `description` (+667 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 1907 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **46 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `20. HTTP API (`unfold.api`, prefix `/api/v1`)` connect `Unfold — Low-Level Design (LLD)` to `cn`, `projects.py`, `ErrorCode`, `core/errors.py`?**
  _High betweenness centrality (0.180) - this node is a cross-community bridge._
- **Why does `4. PostgreSQL schema (`unfold.persistence.postgres.models`, Alembic `migrations/`)` connect `compiler.py` to `Node`, `integration/conftest.py`, `api/schemas.py`, `ProjectIR`, `_Builder`, `Unfold — Low-Level Design (LLD)`, `indexing/chunking.py`, `join`, `planner.py`?**
  _High betweenness centrality (0.176) - this node is a cross-community bridge._
- **Why does `current()` connect `Unfold — Low-Level Design (LLD)` to `mermaid.min.js`?**
  _High betweenness centrality (0.147) - this node is a cross-community bridge._
- **Are the 147 inferred relationships involving `t()` (e.g. with `ACTION()` and `aDe()`) actually correct?**
  _`t()` has 147 INFERRED edges - model-reasoned connections that need verification._
- **Are the 175 inferred relationships involving `r()` (e.g. with `mermaid.min.js` and `ACTION()`) actually correct?**
  _`r()` has 175 INFERRED edges - model-reasoned connections that need verification._
- **Are the 16 inferred relationships involving `s()` (e.g. with `C()` and `v()`) actually correct?**
  _`s()` has 16 INFERRED edges - model-reasoned connections that need verification._
- **Are the 160 inferred relationships involving `n()` (e.g. with `mermaid.min.js` and `addAnnotation()`) actually correct?**
  _`n()` has 160 INFERRED edges - model-reasoned connections that need verification._