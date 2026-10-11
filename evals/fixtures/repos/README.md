# Fixture repositories (parse-only)

These directories are small but realistic repositories used by Unfold's repository-intelligence tests and, later,
as the golden evaluation set. They are **never executed, installed or imported** — Unfold only parses them.

| Fixture | Shape |
|---|---|
| `fastapi_rag/` | Python FastAPI RAG service (SQLAlchemy, Qdrant, BM25 + vector + RRF retrieval, arq worker, Docker Compose, CI) |
| `express_ts_prisma/` | TypeScript Express API with Prisma, JWT auth middleware and a small React frontend |
| `terraform_k8s/` | Infrastructure repository: Terraform modules (network, GKE, Cloud SQL), Kubernetes manifests, deploy workflow, small Go service |
| `cli_tool/` | Small Python command line tool (click, three commands, a JSON file): the `cli` project shape |
| `multi_module/` | Three Python packages with their own manifests, each importing the previous one, and no entry point: the `monorepo` shape |
| `haskell_only/` | Source in a language with no analyzer: must come out as stated uncertainty, no symbols and no flows |
| `dynamic_dispatch/` | A plugin host that picks its handler with `importlib.import_module` and `getattr`: calls must be reported as unresolved |

The last four back `backend/tests/unit/test_depth_coverage_fixtures.py` (phase 5 task 10). The empty-repository and
oversized-file cases are generated in `tmp_path` by that test, not committed.

Some files intentionally contain an obviously fake secret or a prompt-injection-style sentence so that redaction and
trust labelling can be exercised. Do not "fix" them.
