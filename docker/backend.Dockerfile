# syntax=docker/dockerfile:1.7
# One Python image, two runtime targets (HLD §2): `api` (FastAPI + chat) and `worker` (one-shot analysis task + Chromium).
# Build context is the repository root:  docker build -f docker/backend.Dockerfile --target api .

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.12.10

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ---------------------------------------------------------------- deps: resolved, locked, no dev tools
FROM python:${PYTHON_VERSION}-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
COPY backend/README.md* ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

# ---------------------------------------------------------------- shared runtime base
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH \
    APP_ENV=production LOG_FORMAT=json \
    MODEL_CACHE_DIR=/opt/unfold/models \
    XDG_CACHE_HOME=/opt/unfold/cache
RUN groupadd --system --gid 10001 unfold \
    && useradd --system --uid 10001 --gid unfold --home-dir /app --shell /usr/sbin/nologin unfold \
    && mkdir -p /opt/unfold/models /opt/unfold/cache
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY backend/alembic.ini /app/backend/alembic.ini
COPY backend/migrations /app/backend/migrations
COPY docker/prefetch.py /app/docker/prefetch.py
# Retrieval models are used by both processes (chat retrieval runs in the api).
RUN python /app/docker/prefetch.py models && chown -R unfold:unfold /opt/unfold

# ---------------------------------------------------------------- api
FROM runtime AS api
USER unfold
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]
CMD ["uvicorn", "unfold.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--no-proxy-headers", "--no-server-header"]

# ---------------------------------------------------------------- worker (analysis / export / maintenance)
FROM runtime AS worker
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/unfold/ms-playwright
RUN playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && python /app/docker/prefetch.py grammars \
    && chown -R unfold:unfold /opt/unfold
USER unfold
CMD ["arq", "unfold.worker.settings.WorkerSettings"]
