FROM python:3.13-slim-trixie AS base
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

FROM base
COPY --from=builder /app /app
ENV PATH="/app/.venv/bin:$PATH"

cmd ["si-cli", "serve", "--host=0.0.0.0"]
