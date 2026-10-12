# Unfold

Paste a GitHub URL or upload a ZIP and get a guide, architecture diagrams and a chat that answers from the repository's own code, with citations to files and lines. Uploaded code is parsed, never executed.

- Backend: Python 3.12, FastAPI, one-shot worker processes. Local stack: Postgres, Redis, Qdrant, Neo4j, MinIO. AWS profile: DynamoDB, S3, SQS, Lambda, Fargate (`docs/infra/deployment.md`).
- Frontend: Next.js 16 / React 19 (`frontend/`).
- Models: Gemini through the LLM gateway, with a per-job budget.

## Quickstart (local)

Needs Docker, [uv](https://docs.astral.sh/uv/), Node 20+ and at least one `GEMINI_API_KEY_1`.

```bash
cp .env.example .env            # set GEMINI_API_KEY_1 and the store passwords
docker compose up -d            # Postgres, Redis, Qdrant, Neo4j, MinIO
cd backend
uv sync
uv run alembic upgrade head
uv run unfold-api               # API on the port in .env
cd ../frontend
npm install
npm run dev                     # http://localhost:3010
```

Submitting a repository starts a one-shot worker process; progress shows on the project page.

## Checks

```bash
uv run --project backend python scripts/check.py                  # lint, types, migrations, unit, frontend
uv run --project backend python scripts/check.py --integration    # adds tests against the real stores
```

Playwright journeys live in `frontend/e2e` (`docs/design/frontend-e2e.md`).

## Where to read next

| Topic | File |
|---|---|
| Architecture | `docs/HLD.md`, `docs/LLD.md`, `docs/adr/` |
| AWS deployment, cost, operations | `docs/infra/` |
| Running it in production (deploy, roll back, drain the queue, rotate keys) | `docs/infra/runbook.md` |
| Security review and red team | `docs/security/` |
| Guide quality evals | `docs/design/quality-regression.md` |
| Agent and contributor rules | `CLAUDE.md`, `backend/CLAUDE.md` |
