# Stage 1: Build UI
FROM node:20-slim AS ui-builder
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

# Stage 2: Python base
FROM python:3.14-slim-trixie AS base
FROM base AS builder

RUN apt-get update && apt-get -y upgrade
RUN apt-get update && apt-get install -y git
COPY --from=ghcr.io/astral-sh/uv:0.9.17 /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=python3.13

WORKDIR /app
COPY uv.lock pyproject.toml /app/
RUN --mount=type=cache,target=/root/.cache/uv \
  uv sync --frozen --no-install-project --no-dev
#COPY README.md /app
#COPY src /app/src
#COPY *.yaml /app
COPY . /app

RUN ls -al /app
RUN --mount=type=cache,target=/root/.cache/uv \
  uv sync --frozen --no-dev

# Stage 3: Final image
FROM base
COPY --from=builder /app /app

# Copy UI build artifacts
COPY --from=ui-builder /ui/build /app/src/soliplex/ingester/server/static

ENV PATH="/app/.venv/bin:$PATH"

CMD ["si-cli", "serve", "--host=0.0.0.0"]
