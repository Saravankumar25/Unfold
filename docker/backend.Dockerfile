# syntax=docker/dockerfile:1.7
# One Dockerfile, four runtime targets (ADR 0010, 0011). Build context is the repository root:
#   docker build -f docker/backend.Dockerfile --target api-lambda .
#
#   api-lambda  FastAPI behind the Lambda Web Adapter (response streaming). Retrieval models, no analysis stack,
#               no Chromium, no local-store clients. Pushed to ECR, run by the API Lambda.
#   worker      One-shot analysis task (Fargate) and the three control handlers (Lambda, via awslambdaric).
#   exporter    worker + Chromium (PDF export task).
#   api         The same API with the local-stack clients (Postgres, Redis, MinIO, Qdrant, Neo4j) for development.
#
# Each target syncs only its dependency groups from `uv.lock` (`--no-default-groups --group ...`), so an image holds
# what it runs and nothing else. Base images are pinned by digest; bump them deliberately.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.10@sha256:2bb3ebca0a796a155094a27773d290c4b074572e6107f171d88d086682fd2500
ARG LWA_IMAGE=public.ecr.aws/awsguru/aws-lambda-adapter:1.1.0@sha256:17cfd08eff1dfea3f6a9a1e9c65fdac80aa4919b6085e746615530f43f57d2f1

FROM ${UV_IMAGE} AS uv
FROM ${LWA_IMAGE} AS lwa

# ---------------------------------------------------------------- build base: lockfile + sources
FROM ${PYTHON_IMAGE} AS build-base
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/src ./src
COPY backend/README.md* ./

FROM build-base AS build-api
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-default-groups --group ai --no-editable

FROM build-base AS build-api-local
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-default-groups --group ai --group local --no-editable

FROM build-base AS build-worker
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-default-groups --group ai --group analysis --group lambda-runtime --no-editable

FROM build-base AS build-exporter
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-default-groups --group ai --group analysis --group lambda-runtime \
        --group export --no-editable

# ---------------------------------------------------------------- runtime base
FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH \
    APP_ENV=production LOG_FORMAT=json \
    MODEL_CACHE_DIR=/opt/unfold/models \
    XDG_CACHE_HOME=/opt/unfold/cache
RUN groupadd --system --gid 10001 unfold \
    && useradd --system --uid 10001 --gid unfold --home-dir /app --shell /usr/sbin/nologin unfold \
    && mkdir -p /opt/unfold/models /opt/unfold/cache
WORKDIR /app
COPY docker/prefetch.py /app/docker/prefetch.py

# ---------------------------------------------------------------- api-lambda
FROM runtime AS api-lambda
COPY --from=build-api /app/.venv /app/.venv
# Retrieval models are baked in: a Lambda has no persistent cache and must not download at cold start.
RUN python /app/docker/prefetch.py models && chown -R unfold:unfold /opt/unfold
COPY --from=lwa /lambda-adapter /opt/extensions/lambda-adapter
# Lambda runs the image read-only apart from /tmp; the settings below mirror the function configuration
ENV HOME=/tmp XDG_CACHE_HOME=/tmp \
    PORT=8000 AWS_LWA_PORT=8000 AWS_LWA_READINESS_CHECK_PATH=/health AWS_LWA_INVOKE_MODE=response_stream
USER unfold
EXPOSE 8000
CMD ["uvicorn", "unfold.api.aws:create_aws_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--no-proxy-headers", "--no-server-header"]

# ---------------------------------------------------------------- api (local stack)
FROM runtime AS api
COPY --from=build-api-local /app/.venv /app/.venv
COPY backend/alembic.ini /app/backend/alembic.ini
COPY backend/migrations /app/backend/migrations
RUN python /app/docker/prefetch.py models && chown -R unfold:unfold /opt/unfold
USER unfold
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]
CMD ["uvicorn", "unfold.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--no-proxy-headers", "--no-server-header"]

# ---------------------------------------------------------------- worker (analysis task; control Lambdas)
FROM runtime AS worker
COPY --from=build-worker /app/.venv /app/.venv
RUN python /app/docker/prefetch.py models grammars && chown -R unfold:unfold /opt/unfold
USER unfold
# Fargate: `analysis <job_id>` / `cleanup <project_id>`; one task per container run, exits when done.
# Lambda: the control module overrides the entry point with awslambdaric and sets the handler as the command.
ENTRYPOINT ["python", "-m", "unfold.worker.run"]

# ---------------------------------------------------------------- exporter (PDF export task)
FROM runtime AS exporter
COPY --from=build-exporter /app/.venv /app/.venv
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/unfold/ms-playwright
RUN python /app/docker/prefetch.py models grammars \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && chown -R unfold:unfold /opt/unfold
USER unfold
ENTRYPOINT ["python", "-m", "unfold.worker.run"]
