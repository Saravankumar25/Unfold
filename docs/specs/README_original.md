# Unfold

> **Prepare your project.**

Unfold turns a public GitHub repository or project ZIP into a rich, visual, evidence-grounded learning experience.

Instead of making you reverse engineer hundreds of files, Unfold maps the project, reconstructs how it works, explains its architecture and workflows, teaches the important technical concepts, prepares realistic questions, and gives you a project-aware AI chatbot for deeper exploration.

---

## Why Unfold?

A lot of people can build a project faster than they can fully understand it.

That happens when:
- students build projects for placements or viva,
- hackathon teams code under time pressure,
- engineers build side projects quickly,
- developers use AI-assisted coding heavily,
- researchers inherit or adapt unfamiliar implementations,
- someone simply wants to learn how an open-source project works.

Unfold is designed for the gap between:

> **“I have the code.”**

and

> **“I genuinely understand this project.”**

---

## What Unfold Generates

For every project, Unfold creates a dynamic Project Guide intended to take roughly **45–60 minutes** to study.

Depending on the repository, the guide may cover:

- project overview,
- 60-second explanation,
- end-to-end workflow,
- system architecture,
- repository structure,
- major subsystems,
- frontend,
- backend,
- database/data layer,
- algorithms,
- AI/ML,
- RAG/agent pipelines,
- infrastructure,
- deployment,
- security,
- important technical concepts,
- engineering decisions,
- tradeoffs,
- limitations,
- improvements,
- future scope,
- interview/viva/review questions,
- revision summary.

The guide structure is **adaptive**. A machine-learning repository and a distributed backend should not receive the same explanation template.

---

## Visual Learning

Unfold can generate project-specific visuals such as:

- architecture diagrams,
- sequence diagrams,
- request lifecycles,
- API flows,
- database relationships,
- dependency graphs,
- event/queue flows,
- AI pipelines,
- RAG pipelines,
- ML training/inference pipelines,
- deployment diagrams.

Interactive diagrams can expose component details and source evidence.

---

## Project Chat

After the Project Guide is generated, users can ask questions such as:

- Why is Redis used here?
- Trace authentication end to end.
- Explain this RRF implementation simply.
- Where is this feature implemented?
- Why not use Kafka here?
- What would break at 100× scale?
- Explain this file.
- Ask me interview questions.
- Give me a strong viva answer for this architecture.

The chatbot combines:
- repository evidence,
- knowledge graph,
- Hybrid RAG,
- Project Guide context,
- short-term conversation memory,
- general engineering knowledge.

---

# Core Architecture

```text
GitHub / ZIP
    ↓
Safe Repository Ingestion
    ↓
Static + Structural Code Intelligence
    ↓
Project IR
    ↓
┌───────────────────────┬──────────────────────────┐
│ Neo4j Knowledge Graph │ Native Hybrid RAG        │
│                       │ BM25 + Qdrant Dense      │
│                       │ RRF + Cross Encoder      │
└───────────────┬───────┴───────────────┬──────────┘
                │                       │
                └──────────┬────────────┘
                           ↓
                    Context Compiler
                           ↓
                 LangGraph Orchestration
                           ↓
                      Deep Agents
                           ↓
                 Gemini / Groq Gateway
                           ↓
                  Evidence Verification
                           ↓
              Project Guide + Project Chat
```

---

# Technology Stack

## Frontend
- Next.js
- React
- TypeScript
- Tailwind CSS
- shadcn/ui
- Motion
- React Flow
- Mermaid
- Shiki

## Backend
- Python
- FastAPI
- Pydantic
- async workers
- Redis-backed job processing

## Code Intelligence
- Tree-sitter
- Python AST
- language-specific analyzers
- custom Project IR

## Graph
- Neo4j
- Cypher
- graph community/subsystem analysis

## Retrieval
- native BM25
- Qdrant dense vector search
- Reciprocal Rank Fusion
- local cross-encoder reranking
- graph retrieval
- structural retrieval
- hierarchical summaries

## AI Orchestration
- LangGraph
- Deep Agents
- native internal tools

## LLM Providers
- Gemini
- Groq

V1 is designed for free-tier-compatible models.

## Data
- PostgreSQL
- Neo4j
- Qdrant
- Redis
- S3-compatible object storage

## Evaluation
- DeepEval
- RAGAS
- custom deterministic benchmarks
- OpenTelemetry traces

## Delivery
- Docker
- Docker Compose
- GitHub Actions
- Terraform
- GCP-ready architecture

The final GCP deployment topology will be designed separately.

---

# What Makes Unfold Different?

Unfold is not:

- an AI README generator,
- a source-code summarizer,
- a generic RAG chatbot,
- an IDE,
- a line-by-line code explainer.

Its core pipeline is:

> **INGEST → UNDERSTAND → MAP → RETRIEVE → VERIFY → TEACH**

The Project Guide is breadth-first.

The chatbot is depth-on-demand.

---

# Project Knowledge Graph

Unfold models relationships such as:

```text
APIEndpoint
    HANDLES → Request
    CALLS → Service
    USES → AuthMiddleware

Service
    CALLS → Repository
    WRITES_TO → Table
    PUBLISHES_TO → Queue

Worker
    CONSUMES_FROM → Queue
    CALLS → ExternalService
```

This allows Unfold to understand code as a connected system rather than a pile of text chunks.

---

# Native Hybrid RAG

Unfold evolves an existing Hybrid RAG architecture:

```text
BM25
  +
Dense Retrieval
  ↓
Reciprocal Rank Fusion
  ↓
Cross-Encoder Reranking
```

Unfold extends it with:

```text
Graph Retrieval
Structural Retrieval
Graph Expansion
Hierarchical Summaries
Context Sufficiency
Context Compilation
```

The retrieval stack is implemented natively inside Unfold.

No MCP dependency is required for internal retrieval.

---

# LangGraph + Deep Agents

LangGraph is the top-level AI workflow runtime.

It manages:
- deterministic analysis,
- agentic analysis,
- branching,
- retries,
- parallel subsystem analysis,
- verification,
- long-running state.

Deep Agents are used for complex repository investigation such as:

- architecture reconstruction,
- workflow tracing,
- database understanding,
- AI/ML analysis,
- infrastructure analysis,
- security analysis,
- difficult chatbot questions.

CrewAI is intentionally not part of the architecture.

---

# Model Routing

Unfold uses a provider-independent LLM Gateway.

Configured credentials:

```text
GEMINI_API_KEY_1
GEMINI_API_KEY_2
GEMINI_API_KEY_3
GEMINI_API_KEY_4

GROQ_API_KEY_1
GROQ_API_KEY_2
GROQ_API_KEY_3
GROQ_API_KEY_4
```

Multiple keys are treated as credentials, not assumed independent quota pools.

The router tracks:
- provider capacity,
- key health,
- rate limits,
- failures,
- circuit state,
- model capabilities.

Tasks request capabilities such as:
- FAST
- BALANCED
- REASONING
- LONG_CONTEXT
- STRUCTURED
- EVALUATOR

rather than hard-coded provider models.

---

# Evaluation First

Unfold is designed with evaluation from the beginning.

## Retrieval
- Recall@K
- Precision@K
- MRR
- nDCG
- relevant file recall
- symbol recall
- graph node/path recall
- subsystem coverage

## Generation
- faithfulness
- factual correctness
- answer relevance
- context relevance
- unsupported-claim rate

## Agents
- task completion
- tool-call accuracy
- tool-call efficiency
- trajectory quality
- evidence completeness
- repeated-loop rate

## Project Guide
- architecture coverage
- workflow completeness
- technology correctness
- explanation coherence
- level appropriateness
- redundancy

---

# Security

Repositories are untrusted input.

Unfold does **not** automatically:

```text
npm install
pip install
python app.py
npm run ...
docker compose up
run repository shell scripts
```

Repository analysis is static/read-only.

Protections include:
- ZIP traversal prevention,
- zip-bomb limits,
- symlink escape protection,
- file count/size limits,
- secret detection/redaction,
- prompt-injection isolation,
- project-scoped agent tools.

---

# Anonymous Sessions

No sign-in is required.

A session temporarily stores:
- project analysis,
- guide,
- chat,
- generated artifacts.

Users are warned before expiry.

Export options:

1. **Project Guide PDF**
2. **Chat Learning PDF**

Expired sessions are deleted from:
- PostgreSQL,
- Neo4j,
- Qdrant,
- Redis,
- object storage.

---

# Local Development

## Prerequisites

Recommended:
- Docker
- Docker Compose
- Python 3.12+
- Node.js 22+
- pnpm
- Git

## Start infrastructure

```bash
docker compose up -d postgres redis neo4j qdrant minio
```

## Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn unfold.api.main:app --reload
```

## Worker

```bash
cd backend
python -m unfold.worker
```

## Frontend

```bash
cd frontend
pnpm install
pnpm dev
```

Exact commands may evolve during implementation.

---

# Environment

Create `.env` from `.env.example`.

Example:

```env
APP_ENV=development

DATABASE_URL=
REDIS_URL=

NEO4J_URI=
NEO4J_USERNAME=
NEO4J_PASSWORD=

QDRANT_URL=

OBJECT_STORE_ENDPOINT=
OBJECT_STORE_BUCKET=

GEMINI_API_KEY_1=
GEMINI_API_KEY_2=
GEMINI_API_KEY_3=
GEMINI_API_KEY_4=

GROQ_API_KEY_1=
GROQ_API_KEY_2=
GROQ_API_KEY_3=
GROQ_API_KEY_4=

EMBEDDING_MODEL=
RERANKER_MODEL=
```

Never commit real credentials.

---

# Testing

Backend:

```bash
pytest
```

Evaluation smoke tests:

```bash
pytest tests/evals
```

Frontend:

```bash
pnpm lint
pnpm typecheck
pnpm test
```

---

# CI/CD

GitHub Actions should include:

- linting,
- type checking,
- unit tests,
- integration tests,
- security checks,
- offline AI evals,
- Docker builds,
- Terraform validation.

Online LLM evals should run through a separate bounded workflow because V1 uses free-tier providers.

---

# Infrastructure as Code

Terraform lives in:

```text
infra/terraform/
```

Modules will eventually define:
- networking,
- compute,
- workers,
- PostgreSQL,
- Redis,
- Neo4j,
- Qdrant,
- object storage,
- secrets,
- observability,
- IAM.

The exact GCP deployment architecture is intentionally deferred until the deployment strategy is reviewed separately.

---

# Documentation

- [`PRD.md`](./PRD.md) — product requirements
- [`ESD.md`](./ESD.md) — engineering specification
- [`architecture.md`](./architecture.md) — HLD + LLD
- [`AI_infrastructure.md`](./AI%20infrastructure.md) — AI/retrieval/agent/evaluation architecture

---

# Engineering Principle

> Do not ask an LLM to infer what can be determined directly from the repository, and do not give an LLM raw repository data when better context can be compiled first.

---

# Product Principle

> A user should not need to read the source code to understand the project, but important explanations should ultimately be grounded in what the source code actually does.

---

# Status

Unfold is currently in architecture/design.

Next engineering milestones:

1. repository ingestion,
2. Project IR,
3. Neo4j graph builder,
4. native Hybrid RAG,
5. context compiler,
6. LangGraph analysis pipeline,
7. Deep Agent investigators,
8. Project Guide generation,
9. Project Chat,
10. evaluation suite,
11. exports,
12. production hardening and GCP deployment.
