# Unfold

Paste a GitHub URL or ZIP, get a guide, architecture diagrams and grounded chat about the repo.
Backend: Python 3.12, FastAPI, one-shot worker processes, Postgres, Qdrant, Neo4j, Redis, MinIO. Frontend: Next.js 16 / React 19.
Target runtime (scale to zero on AWS): see `six-phase-plan.md`. Repo state and findings: `PROJECT_AUDIT.md`.

## Commands

| Purpose | Command |
|---|---|
| Whole gate (the definition of green) | `python scripts/check.py` (`--only backend`, `--integration`) |
| Data stores | `docker compose up -d` (ports and passwords come from the git-ignored `.env`) |
| Backend unit + security tests | `cd backend && uv run pytest` |
| Backend integration tests | `cd backend && uv run pytest -m integration` (needs the stores) |
| Lint / types / migrations | `uv run ruff check src tests` · `uv run mypy` · `uv run alembic check` |
| Frontend | `cd frontend && npm run lint && npm run typecheck && npm test && npm run build` |

Detail for the backend lives in `backend/CLAUDE.md`; architecture in `docs/HLD.md` and `docs/LLD.md`;
decisions in `docs/adr/`.

## Architecture rules (enforced by `backend/tests/unit/test_architecture.py`)

- Application code depends on `unfold.ports` (`BlobStore`, `JobQueue`, `RateLimiter`, `ProgressStore`).
  Only adapters import `redis` or `minio`.
- `unfold.api` and `unfold.worker` are the composition roots; library code receives its stores as arguments.
- All model calls go through the LLM gateway with a per-job budget. Default models: Gemini 3.5-flash-lite,
  3.5-flash, 3.6-flash (2.5 models are retired for new keys; Pro has no free quota).
- Never execute repository code. Never put repository text in a prompt without redaction and fencing.
- No mocks, stubs or fake fallbacks in production paths; raise a typed `UnfoldError` instead.

## Working rules

- One worktree per phase under `.claude/worktrees/` (git-ignored), at most two alive, branch `phase-N-<name>`.
  Run `git worktree list` and `git status` at the start and the end of a session.
- Merge to `main`, re-run the gate on `main`, push, then `git worktree remove` and `git branch -d`.
  Before removing anything check `git branch --merged main` and `git log origin/main..<branch>`; never force.
- Commits are authored by `udarshmarthala` with no AI co-author trailer. Push target is
  `github.com/udarshmarthala/unfold`; use that account's token (`gh auth token -u udarshmarthala`).
- Never commit `.env`, build output or caches. One frontend and one API dev server at a time.
- Production refuses to start with `COOKIE_SECURE=false`, default credentials or `TRUSTED_PROXY_IPS=*`.

## graphify

A knowledge graph lives in `graphify-out/`.
- For codebase questions run `graphify query "<question>"` first; use `graphify path "<A>" "<B>"` and
  `graphify explain "<concept>"` for relationships and single concepts.
- Read `graphify-out/GRAPH_REPORT.md` only for broad architecture review.
- After changing code run `graphify update .` (AST only, no API cost).
