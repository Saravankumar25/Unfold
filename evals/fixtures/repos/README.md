# Fixture repositories (parse-only)

These directories are small but realistic repositories used by Unfold's repository-intelligence tests and, later,
as the golden evaluation set. They are **never executed, installed or imported** — Unfold only parses them.

| Fixture | Shape |
|---|---|
| `fastapi_rag/` | Python FastAPI RAG service (SQLAlchemy, Qdrant, BM25 + vector + RRF retrieval, arq worker, Docker Compose, CI) |
| `express_ts_prisma/` | TypeScript Express API with Prisma, JWT auth middleware and a small React frontend |
| `terraform_k8s/` | Infrastructure repository: Terraform modules (network, GKE, Cloud SQL), Kubernetes manifests, deploy workflow, small Go service |

Some files intentionally contain an obviously fake secret or a prompt-injection-style sentence so that redaction and
trust labelling can be exercised. Do not "fix" them.
