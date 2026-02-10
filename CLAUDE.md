# Project Context

Python project to load documents into a RAG system using haiku.rag and docling. Uses workflows and parameters defined in YAML files to configure document processing (chunking, embedding, storing).

**Integration Points:**

- Svelte UI in `ui/` directory (see [ui/CLAUDE.md](ui/CLAUDE.md))
- REST API endpoints for document management and workflow control
- Agent processes managed by github.com/soliplex/ingester-agents

---

## Documentation

Comprehensive docs in `docs/` folder - **always check these first**:

| Topic | File |
|-------|------|
| Architecture | [ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| API Reference | [API.md](docs/API.md) |
| Workflows | [WORKFLOWS.md](docs/WORKFLOWS.md) |
| Database | [DATABASE.md](docs/DATABASE.md) |
| Configuration | [CONFIGURATION.md](docs/CONFIGURATION.md) |
| Parameter Sets | [PARAMETER_SETS.md](docs/PARAMETER_SETS.md) |
| CLI | [CLI.md](docs/CLI.md) |

---

## Quick Reference

```bash
uv sync                                    # Install dependencies
uv run --env-file .env si-cli serve --reload  # Run dev server
uv run pytest                              # Run tests
uv run ruff format . && uv run ruff check .   # Format & lint
si-cli bootstrap                           # Setup all configs
```

## Key Technologies

- **Python 3.12+**, FastAPI, SQLModel, Pydantic v2
- **Database:** SQLite (dev) / PostgreSQL (prod), LanceDB 0.25.3 (pinned)
- **Storage:** OpenDAL (filesystem, S3, database)
- **Testing:** pytest, 50% coverage minimum

## Critical Warnings

- **Do not upgrade LanceDB** - pinned to 0.25.3
- **Do not change FILE_STORE_TARGET** after documents ingested
- **Do not modify workflows** while runs in progress
- **Never commit secrets** - use environment variables
