# syntax=docker/dockerfile:1

# ---------- Stage 1: Build UI ----------
FROM node:25-slim AS ui-builder
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

# ---------- Stage 2: Python base ----------
FROM python:3.13-slim-trixie AS base

ARG APP_UID=1000
ARG APP_GID=1000

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /uvx /bin/

RUN groupadd -g ${APP_GID} appuser && \
    useradd -u ${APP_UID} -g ${APP_GID} -m -s /bin/bash appuser

# ---------- Stage 3: Builder ----------
FROM base AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=python3.13

COPY uv.lock pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY . .

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------- development: full toolchain, expects bind mount ----------
FROM builder AS development

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

RUN chown -R appuser:appuser /app

USER appuser

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/docs')"]

CMD ["si-cli", "serve", "--host=0.0.0.0", "--reload"]

# ---------- production: minimal, non-root, default target ----------
FROM base AS production

COPY --from=builder --chown=appuser:appuser /app /app

# Copy UI build artifacts
COPY --from=ui-builder --chown=appuser:appuser /ui/build /app/src/soliplex/ingester/server/static

USER appuser

ENV PATH="/app/.venv/bin:$PATH"

LABEL org.opencontainers.image.title="soliplex-ingester" \
      org.opencontainers.image.description="Ingestion service for Soliplex" \
      org.opencontainers.image.vendor="Enfold Systems" \
      org.opencontainers.image.authors="Enfold Systems <info@enfoldsystems.net>"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/docs')"]

CMD ["si-cli", "serve", "--host=0.0.0.0"]
