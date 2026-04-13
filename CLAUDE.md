# Soliplex Ingester

Document ingestion and RAG pipeline. Processes documents through configurable
workflows (validate, parse, chunk, embed, store) using async workers and stores
results in LanceDB vector databases.

## Documentation

Detailed docs in `docs/` -- check these first:

| Topic | File |
|-------|------|
| Getting Started | [GETTING_STARTED.md](docs/GETTING_STARTED.md) |
| Architecture | [ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| API Reference | [API.md](docs/API.md) |
| Workflows | [WORKFLOWS.md](docs/WORKFLOWS.md) |
| Database | [DATABASE.md](docs/DATABASE.md) |
| Configuration | [CONFIGURATION.md](docs/CONFIGURATION.md) |
| Parameter Sets | [PARAMETER_SETS.md](docs/PARAMETER_SETS.md) |
| CLI Reference | [CLI.md](docs/CLI.md) |
| Authentication | [AUTHENTICATION.md](docs/AUTHENTICATION.md) |
| Docker Deployment | [DOCKER.md](docs/DOCKER.md) |

## Quick Reference

```bash
uv sync                                       # Install dependencies
uv run --env-file .env si-cli serve --reload  # Run dev server
uv run pytest                                 # Run tests (50% coverage min)
uv run ruff format . && uv run ruff check .   # Format and lint
si-cli bootstrap                              # Setup all configs
si-cli db-init                                # Initialize database

# Diagnostics (read-only)
si-diag batch list                            # List batches
si-diag document find "pattern"               # Search documents by URI
si-diag status running                        # Currently running steps
si-diag status recent hour                    # Recent activity
```

## Key Technologies

- Python 3.12+, FastAPI, SQLModel, Pydantic v2, Typer CLI
- Database: SQLite (dev) / PostgreSQL (prod), LanceDB (vectors)
- Storage backends: filesystem, S3, database (via OpenDAL)
- Testing: pytest with 50% branch coverage minimum

## Critical Warnings

- Do not change `FILE_STORE_TARGET` after documents are ingested
- Do not modify workflows while runs are in progress
- Never commit secrets -- use environment variables
- `PARAM_DIR` and `USER_PARAM_DIR` must be different directories
