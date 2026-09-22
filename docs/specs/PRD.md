# Unfold — Product Requirements Document (PRD)

> **Caption:** Prepare your project.  
> **Product:** Unfold  
> **Document status:** Draft v1.0  
> **Primary audience:** Product, Engineering, AI/ML, Design, QA  
> **Scope:** V1 foundation with production-ready architecture from day one

---

## 1. Product Vision

**Unfold turns any public technical project into a rich, visual, trustworthy learning experience.**

A user gives Unfold either:
- a public GitHub repository URL, or
- a project ZIP file,

and Unfold studies the repository as a system, reconstructs how it works, identifies its architecture, workflows, technologies, algorithms, infrastructure, security model, limitations, and engineering decisions, and then produces a structured explanation intended to take approximately **45–60 minutes to read and understand**.

The user does **not** need to be the project author.

Unfold must work for:
- students preparing for placements, interviews, project reviews, or viva voce,
- hackathon teams who rapidly built a project and need to explain it,
- engineers revisiting side projects,
- engineers learning an unfamiliar open-source repository,
- researchers understanding research implementations,
- developers learning architecture patterns from public projects,
- users who partially or fully generated code using AI and now need to genuinely understand it.

Unfold is not a generic “chat with your repository” product. The primary product is a **project-understanding engine**. Chat is a second, depth-on-demand interface over the same project intelligence.

---

## 2. Core Promise

After spending approximately 45–60 minutes with an Unfold-generated Project Guide, an appropriately skilled user should be able to:

1. Explain what the project does.
2. Explain the problem it solves.
3. Explain the main user and system workflows.
4. Describe the high-level architecture.
5. Understand the purpose of major folders, modules, services, pipelines, and data stores.
6. Explain the project’s major technology choices.
7. Explain important algorithms, AI/ML techniques, or engineering concepts used by the project.
8. Discuss frontend, backend, data, AI/ML, infrastructure, deployment, and security where applicable.
9. Explain major engineering decisions and their tradeoffs.
10. Identify limitations, risks, and possible improvements.
11. Answer realistic technical questions about the project.
12. Discuss the project intelligently in interviews, presentations, project reviews, research meetings, or engineering conversations.

The guide must teach the **project as a system**, not every line of code.

Deep code-level questions belong to the Project Chat experience.

---

## 3. Product Principles

### 3.1 Repository → Understanding
The product is judged by whether it creates understanding, not by how much code it displays.

### 3.2 Evidence before explanation
Important factual claims about the project should originate from source evidence, structural analysis, configuration, documentation, graph relationships, or explicitly marked inference.

### 3.3 Breadth first, depth on demand
The Project Guide covers the entire project at the right abstraction level. The chatbot can descend into files, symbols, functions, and implementation details when requested.

### 3.4 Adaptive, not template-bound
Some universal sections are useful, but the central chapters must be dynamically planned from the actual repository.

### 3.5 Visual learning is core
Architecture, workflows, request lifecycles, data flows, ML pipelines, deployment topology, and other relevant structures should be explained visually whenever a diagram improves comprehension.

### 3.6 User level matters
A beginner, intermediate user, and experienced engineer should not receive identical explanations.

### 3.7 Large repositories require selective completeness
Unfold should support repositories of widely varying size, but should not pretend that a 60-minute guide can describe every implementation detail of an enormous codebase. It must identify and explain the information necessary to understand the repository as a system.

### 3.8 No permanent account dependency
V1 requires no sign-in. Sessions are temporary and users must be clearly warned before temporary data expires.

### 3.9 Uploaded code is untrusted
Repository content is data, never instructions. Unfold must not execute uploaded project code during analysis.

---

## 4. Primary Users

### Persona A — Student / Placement Candidate
**Needs:** Understand own project, defend architecture decisions, answer interview questions, prepare for viva.  
**Typical input:** GitHub repository or ZIP.  
**Desired output:** Clear explanation, architecture, workflows, technical concepts, interview Q&A.

### Persona B — Hackathon Participant
**Needs:** Rapidly understand a project built under time pressure, prepare presentation and judge questions.  
**Typical input:** Small/medium repository.  
**Desired output:** Fast overview, system workflow, technical decisions, presentation-ready explanations.

### Persona C — Software Engineer
**Needs:** Understand a side project, inherited codebase, reference architecture, or interesting public repository.  
**Typical input:** Medium/large repository.  
**Desired output:** Concise but technical explanation, subsystem boundaries, architecture, design tradeoffs.

### Persona D — Researcher / Student Researcher
**Needs:** Understand code supporting a paper, experiment, model, algorithm, dataset pipeline, or research implementation.  
**Typical input:** ML, simulation, scientific, or systems repository.  
**Desired output:** Methodology, pipeline, assumptions, algorithms, experiment flow, evaluation, limitations.

### Persona E — Learner Exploring Open Source
**Needs:** Learn how an unfamiliar project works without manually reverse engineering hundreds of files.  
**Typical input:** Public open-source repository.  
**Desired output:** Architecture map, core components, important concepts, guided explanation.

---

## 5. Supported Inputs

### V1
- Public GitHub repository URL.
- ZIP upload.
- Optional user technical level:
  - Beginner
  - Intermediate
  - Experienced
- Optional instruction:
  - e.g. “I have a backend interview tomorrow.”
  - e.g. “Focus more on ML methodology.”
  - e.g. “Explain this as preparation for a research presentation.”

### Explicitly not supported
- Private GitHub repositories.
- GitHub OAuth for private repository access.
- Executing the uploaded repository.
- Installing repository dependencies to infer runtime behavior.

Private repository support is not planned as a core product requirement.

---

## 6. Supported Project Categories

Unfold should not be restricted to one project type.

Expected categories include:
- web applications,
- frontend applications,
- backend services,
- APIs,
- mobile applications,
- AI/ML projects,
- RAG systems,
- agentic AI systems,
- research code,
- data engineering,
- DevOps/infrastructure,
- distributed systems,
- CLI tools,
- libraries/frameworks,
- database-centric applications,
- cloud-native systems,
- monorepos,
- multi-service repositories.

The analysis depth and guide structure must adapt to the detected project.

---

## 7. Primary User Journey

### 7.1 Landing
User sees:

**Unfold**  
*Prepare your project.*

Primary actions:
- Paste public GitHub URL
- Upload ZIP

Secondary inputs:
- Technical level
- Optional focus/instruction

CTA:
- **Unfold Project**

### 7.2 Analysis Progress
The user sees meaningful staged progress, for example:
- Fetching project
- Inspecting repository
- Detecting languages and frameworks
- Mapping repository structure
- Building project knowledge graph
- Understanding major subsystems
- Indexing project knowledge
- Reconstructing workflows
- Planning your guide
- Generating diagrams
- Verifying explanations
- Preparing interview questions
- Finalizing Project Guide

Progress must reflect real backend stages rather than fake timers.

### 7.3 Project Guide
The user lands in a rich documentation-style learning experience.

Recommended layout:
- left: dynamic table of contents,
- center: Project Guide,
- right or bottom: persistent “Ask Unfold” entry point,
- optional mini-map/progress,
- contextual actions such as:
  - Make simpler
  - Go deeper
  - Explain concept
  - Show evidence
  - Ask about this
  - Quiz me
  - Interview me

### 7.4 Project Chat
The user asks questions grounded in:
- original repository,
- code graph,
- indexed corpus,
- generated Project Guide,
- current chat history.

Chat may answer broader technical concepts when related to the project.

### 7.5 Export
Users may export up to two primary files:
1. **Project Guide PDF**
2. **Chat Learning PDF**

### 7.6 Session Expiry
Because there is no sign-in, the user is warned before data expiry.

Example:
> Your Unfold session is temporary. Download your Project Guide and any chat notes you want to keep before this session expires.

---

## 8. Project Guide Requirements

### 8.1 Universal Backbone

Every guide should include, where applicable:

1. **Project in 60 Seconds**
2. **What Problem This Project Solves**
3. **Who/What Uses It**
4. **End-to-End Workflow**
5. **Architecture**
6. **Repository Map**
7. **Major Subsystems**
8. **Frontend**
9. **Backend**
10. **Database / Data Layer**
11. **Algorithms / AI / ML**
12. **Infrastructure / Deployment**
13. **Security**
14. **Important Technical Concepts**
15. **Important Engineering Decisions**
16. **Limitations**
17. **Improvements / Future Scope**
18. **Interview / Viva / Review Questions**
19. **Quick Revision Summary**

Sections that do not apply should be omitted or merged naturally.

### 8.2 Dynamic Core Chapters

The middle of the guide must be repository-specific.

Examples:

#### RAG project
- ingestion,
- chunking,
- embeddings,
- indexing,
- lexical retrieval,
- semantic retrieval,
- fusion,
- reranking,
- context construction,
- generation,
- evaluation.

#### ML project
- problem formulation,
- dataset,
- preprocessing,
- feature engineering,
- model architecture,
- training,
- validation,
- metrics,
- inference,
- limitations.

#### Distributed backend
- request routing,
- service boundaries,
- persistence,
- caching,
- queues,
- retries,
- consistency,
- observability,
- deployment.

#### DevOps project
- provisioning,
- environments,
- CI/CD,
- container lifecycle,
- secrets,
- networking,
- observability,
- rollback.

### 8.3 Reading Length

Target:
- approximately 45–60 minutes for a normal complete project,
- shorter for trivial projects,
- potentially longer for unusually broad repositories if required.

Unfold should optimize for **useful coverage**, not arbitrary word count.

### 8.4 Explanation Depth

Depth is dynamically selected based on:
- user technical level,
- project complexity,
- importance of concept,
- optional user focus,
- chapter role.

The default guide should not become a programming textbook.

### 8.5 Evidence

The backend should maintain evidence for factual claims.

The UI may expose evidence selectively through:
- “View evidence”
- source file references
- relevant symbols
- repository locations

Citations do not need to clutter every sentence.

---

## 9. Visual Learning Requirements

Unfold should generate visual explanations based on project type.

Possible visualizations:
- high-level architecture,
- sequence diagrams,
- request lifecycle,
- API flow,
- database schema/relationships,
- data flow,
- dependency graph,
- module/subsystem graph,
- authentication flow,
- event-driven flow,
- background-job flow,
- AI pipeline,
- ML training/inference pipeline,
- RAG pipeline,
- agent workflow,
- deployment architecture,
- CI/CD flow,
- cloud topology,
- algorithm illustration.

### Interactive diagrams
Where useful, users should be able to:
- click a node,
- view explanation,
- view important source evidence,
- follow related components,
- ask a question about that node.

Static diagrams are acceptable when interactivity adds little value.

---

## 10. Chatbot Requirements

The chatbot must be capable of:

- explaining any part of the project,
- simplifying an explanation,
- going deeper,
- locating implementation,
- explaining relevant files/functions/symbols,
- explaining technical concepts,
- comparing alternatives,
- tracing workflows,
- discussing architectural tradeoffs,
- answering interview-style questions,
- conducting project-specific mock questioning,
- connecting general engineering knowledge to the project.

### Chat memory
- short-term session memory only,
- recent turns retained directly,
- older history summarized/compacted,
- no cross-user memory,
- no long-term personal profile.

### Session learning state
Within a chat session, Unfold may track:
- concepts already explained,
- user level,
- current focus,
- topics the user struggled with.

This improves pedagogy without becoming permanent personalization.

---

## 11. Interview and Viva Preparation

Interview preparation is not a separate unrelated product. It emerges from project understanding.

Questions should include:

### Repository-grounded questions
- How does X work?
- Where is Y implemented?
- What happens after Z?

### Design questions
- Why was this technology appropriate?
- What alternatives could be used?
- What are the tradeoffs?

### Failure questions
- What happens if this service fails?
- How would you handle scale?
- How would you improve reliability?

### Security questions
- What attack surface exists?
- How is authentication enforced?
- Where could data leak?

### Extension questions
- How would you improve this?
- How would you make it production-grade?
- What would change at 100× scale?

Generated answers must distinguish:
- what the repository actually does,
- what can reasonably be inferred,
- what is a proposed improvement.

---

## 12. Export Requirements

### Project Guide PDF
Contains:
- main explanation,
- visuals,
- architecture,
- workflows,
- technical concepts,
- selected code excerpts when useful,
- decisions,
- limitations,
- Q&A,
- revision material.

### Chat Learning PDF
Contains:
- useful questions and answers,
- diagrams/code generated during chat,
- optional concise learning notes,
- chronological context where needed.

The Chat Learning PDF is separate from the Project Guide PDF.

---

## 13. AI/Product Intelligence Requirements

Unfold must construct a reusable project understanding before generating the guide.

The system should create:
- repository inventory,
- Project IR,
- code knowledge graph,
- hierarchical summaries,
- retrievable semantic corpus,
- major subsystem map,
- detected workflows,
- evidence ledger,
- guide plan.

The Project Guide must not be generated directly from raw repository text.

---

## 14. Retrieval Requirements

Unfold uses a native Hybrid RAG pipeline inspired by the previously developed Hybrid RAG implementation:

- BM25 lexical retrieval,
- dense semantic retrieval,
- Reciprocal Rank Fusion,
- cross-encoder reranking,
- graph retrieval/expansion,
- structural retrieval,
- hierarchical summaries.

No MCP dependency is required for the internal retrieval stack.

Retrieval behavior must differ between:
- broad Project Guide analysis,
- precise chatbot questions.

---

## 15. Graph Intelligence Requirements

A Neo4j knowledge graph represents meaningful software relationships.

Examples:
- modules,
- files,
- classes,
- functions,
- API endpoints,
- schemas,
- tables,
- services,
- workers,
- queues,
- external services,
- infrastructure resources,
- ML components,
- concepts.

Relationships may include:
- CONTAINS,
- IMPORTS,
- CALLS,
- IMPLEMENTS,
- EXPOSES,
- READS_FROM,
- WRITES_TO,
- PUBLISHES_TO,
- CONSUMES_FROM,
- USES,
- DEPENDS_ON,
- DEPLOYS_TO,
- AUTHENTICATES_WITH.

The graph stores semantic engineering relationships, not every raw AST token.

---

## 16. Agentic Requirements

### LangGraph
LangGraph is the top-level AI workflow orchestrator.

It coordinates:
- deterministic stages,
- model-driven stages,
- retries,
- branching,
- parallel subsystem analysis,
- verification loops,
- chat workflows.

### Deep Agents
Deep Agents are first-class for tasks requiring:
- multi-step investigation,
- decomposition,
- subagents,
- context isolation,
- large-repository investigation,
- architecture/workflow reconstruction,
- difficult chatbot questions.

Deep Agents are not restricted to rare fallback use. They are intentionally used when agentic decomposition improves analysis quality.

### Excluded
- CrewAI
- MCP as internal architecture

---

## 17. Model Provider Constraints

V1 is designed around:
- 4 Gemini API keys,
- 4 Groq API keys,
- free-tier-compatible models only.

The system must:
- support provider/model routing,
- track health,
- enforce provider quotas,
- retry responsibly,
- use circuit breakers,
- degrade gracefully,
- queue rather than overload providers,
- avoid assuming that multiple keys multiply quotas.

No architecture should depend on one specific model name remaining available forever.

---

## 18. Evaluation Requirements

Evaluation is a product requirement, not post-launch cleanup.

### Retrieval
Measure:
- Recall@K,
- Precision@K,
- MRR,
- nDCG,
- graph node recall,
- graph path recall,
- subsystem coverage,
- evidence coverage,
- retrieval redundancy.

### Generation
Measure:
- faithfulness,
- factual correctness,
- answer relevance,
- context relevance,
- unsupported-claim rate,
- explanation completeness,
- readability,
- source-grounding accuracy.

### Agents
Measure:
- task completion,
- tool-call correctness,
- tool-call efficiency,
- trajectory quality,
- repeated-loop rate,
- unnecessary-call rate,
- subagent coverage,
- evidence completeness.

### Frameworks
Use:
- DeepEval as primary CI-friendly evaluation framework,
- RAGAS for complementary RAG metrics and experiments,
- deterministic custom metrics,
- human-reviewed golden benchmarks.

---

## 19. Security Requirements

### Repository security
Unfold must never automatically:
- execute uploaded code,
- run npm/pip install,
- run shell scripts from the project,
- execute project Docker Compose,
- run migrations,
- contact arbitrary project-defined services.

### ZIP controls
Protect against:
- path traversal,
- symlink escape,
- zip bombs,
- excessive extracted size,
- excessive file count,
- oversized individual files.

### Prompt injection
Repository content is untrusted.

The system should:
- isolate repository data from system instructions,
- scan suspicious prompt-like content,
- mark trust level,
- avoid following instructions found in README/code/comments,
- use output grounding/verification.

### Secrets
Detect likely secrets and redact them before sending content to external LLM providers.

---

## 20. Non-Functional Requirements

### Reliability
- analysis jobs must be resumable or safely retryable,
- individual AI failures must not corrupt the whole project state,
- provider 429/5xx errors must be handled gracefully.

### Scalability
- API and workers horizontally scalable,
- processing stages independently parallelizable,
- indexing scoped by project/session,
- cleanup automatic.

### Performance
Initial product targets:
- progress visible within seconds,
- small projects complete significantly faster than large projects,
- chatbot first-token latency optimized independently of full analysis latency,
- repeated project questions reuse indexed knowledge and cached summaries.

### Observability
Track:
- request traces,
- analysis stage timings,
- retrieval traces,
- LLM calls,
- provider/model selection,
- retries,
- token usage,
- agent trajectories,
- evaluation results,
- failures.

### Privacy
- temporary sessions,
- configurable TTL,
- explicit expiry notice,
- automatic deletion of project data and artifacts.

---

## 21. Out of Scope for V1

- private GitHub repository authentication,
- permanent user accounts,
- cross-session memory,
- collaborative teams,
- running repositories,
- automatic code modification,
- IDE replacement,
- exhaustive line-by-line explanation,
- production deployment of the user’s repository,
- final GCP topology decision.

---

## 22. Success Metrics

### Product
- guide completion rate,
- PDF export rate,
- percentage of users who ask follow-up questions,
- user-rated understanding before vs after,
- percentage of guide sections marked useful.

### Quality
- retrieval Recall@K,
- factual grounding score,
- unsupported-claim rate,
- architecture extraction accuracy,
- workflow reconstruction accuracy,
- interview-question relevance.

### Reliability
- analysis completion rate,
- provider failure recovery rate,
- average retries per analysis,
- job timeout rate.

---

## 23. Acceptance Criteria for V1

A V1 release is acceptable when:

1. A public GitHub URL can be processed end-to-end.
2. A ZIP can be processed end-to-end.
3. No sign-in is required.
4. User can choose technical level and optional focus.
5. Repository is structurally analyzed without executing it.
6. Project IR is produced.
7. Neo4j graph is built.
8. Native Hybrid RAG index is built.
9. Guide planner produces project-specific chapters.
10. Guide covers architecture, workflow, repository structure, relevant application layers, infrastructure, security, decisions, limitations, improvements, and Q&A.
11. At least several meaningful diagrams are generated when appropriate.
12. Project Chat answers grounded follow-ups.
13. Project Guide PDF exports successfully.
14. Chat Learning PDF exports successfully.
15. Session data expires automatically.
16. Gemini/Groq provider fallback works.
17. Retrieval and AI evaluation suites run in CI or dedicated eval workflows.
18. Docker-based local environment works.
19. GitHub Actions validates code and tests.
20. Terraform structure exists and is deployment-ready, while GCP production topology remains a later decision.

---

## 24. Product Positioning

Recommended core messaging:

# Unfold
### Prepare your project.

**Turn any public technical project into a visual, understandable learning experience.**

Drop a GitHub repository or project ZIP. Unfold studies the codebase, maps how it works, explains the architecture and important technical concepts, prepares realistic questions, and lets you explore deeper with AI.

Unfold is not:
- “AI README generator”
- “chat with GitHub”
- “code summarizer”
- “generic interview bot”

Unfold is a **project-understanding system**.

---

## 25. Guiding Product Test

Every major feature should answer one question:

> Does this help the user understand the project better, faster, and more accurately?

If not, it should not become part of the core product.
