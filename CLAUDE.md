# Project Context

Python project to load documents into a RAG system using haiku.rag and docling. Uses workflows and parameters defined in YAML files to configure document processing (chunking, embedding, storing).

**Key Design Principles:**
- All steps retryable and recoverable from failures
- Progress tracked for observability and debugging
- Artifacts stored via configurable multi-backend system (database, filesystem, S3)

**Integration Points:**
- Svelte UI in `ui/` directory (see [ui/CLAUDE.md](ui/CLAUDE.md))
- REST API endpoints for document management and workflow control
- Agent processes managed by github.com/soliplex/ingester-agents

---

# Documentation

Comprehensive docs in `docs/` folder - **always check these first**:

| Topic | File | Use For |
|-------|------|---------|
| Getting Started | [GETTING_STARTED.md](docs/GETTING_STARTED.md) | Installation, first batch |
| Architecture | [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, data flow |
| API Reference | [API.md](docs/API.md) | REST endpoints, adding endpoints |
| Workflows | [WORKFLOWS.md](docs/WORKFLOWS.md) | Custom steps, troubleshooting |
| Database | [DATABASE.md](docs/DATABASE.md) | Schema, query examples |
| Configuration | [CONFIGURATION.md](docs/CONFIGURATION.md) | Environment variables |
| Authentication | [AUTHENTICATION.md](docs/AUTHENTICATION.md) | OIDC setup, OAuth2 Proxy |
| CLI | [CLI.md](docs/CLI.md) | Command reference |

---

# Quick Reference

## Essential Commands

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

## Project Structure

```
src/soliplex/ingester/
├── lib/
│   ├── config.py      # Pydantic settings
│   ├── models.py      # SQLModel database models
│   ├── dal.py         # Data Access Layer & storage
│   ├── operations.py  # Document CRUD operations
│   ├── workflow.py    # Built-in step handlers
│   └── wf/            # Workflow execution engine
├── server/routes/
│   ├── batch.py       # Batch management endpoints
│   ├── document.py    # Document ingestion endpoints
│   ├── workflow.py    # Workflow control endpoints
│   ├── lancedb.py     # Vector database management
│   └── stats.py       # System statistics endpoints
└── cli.py             # CLI commands
```

## Essential Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DOC_DB_URL` | Yes | Database connection string |
| `DOCLING_SERVER_URL` | No | Docling parsing service endpoint |
| `DOCLING_CHUNK_SERVER_URL` | No | Docling chunking service endpoint (can differ from parsing) |
| `OLLAMA_BASE_URL` | No | Ollama server URL for embeddings |
| `OLLAMA_BASE_URL_DOCLING` | No | Ollama server URL for Docling chunking (can differ for load distribution) |
| `FILE_STORE_TARGET` | No | Storage: `fs`, `db`, or `s3` |
| `LANCEDB_DIR` | No | Vector database directory |

See [CONFIGURATION.md](docs/CONFIGURATION.md) for complete list.

---

# Code Style

- PEP8 with 126 char line length (ruff configured)
- snake_case functions/variables, PascalCase classes
- Always use `async`/`await` for I/O operations
- Type annotations required (Python 3.12+ syntax)
- numpy-style docstrings
- Single-line imports, grouped: stdlib → third-party → local

## Testing

- Unit tests in `tests/unit/test_*.py`
- 50% coverage minimum enforced
- Mock external services (Docling, HaikuRAG)

---

# AI Assistant Guidelines

## Before Making Changes

1. **Read files first** - Always read before editing
2. **Check docs/** - Reference existing documentation
3. **Follow patterns** - Study similar code in codebase
4. **Preserve async** - All I/O must use async/await

## File Organization

- Database models → `lib/models.py`
- API endpoints → `server/routes/`
- Workflow handlers → `lib/workflow.py`
- Tests → `tests/unit/`

## Storage Operations

**Always use DAL** from `lib/dal.py` for artifact storage:
```python
from soliplex.ingester.lib.dal import get_operator
operator = get_operator(store_type)  # raw, markdown, json, chunks, embeddings
```
Never use direct file I/O for artifacts.

## Database Access

```python
from soliplex.ingester.lib.models import get_session

async with get_session() as session:
    result = await session.exec(query)
    await session.commit()  # Required for modifications
```

## When to Ask for Clarification

- Multiple valid approaches exist
- Changes affect core workflow execution
- Breaking changes to APIs
- New dependencies needed

---

# Critical Warnings

- **Do not upgrade LanceDB** - pinned to 0.25.3
- **Do not change FILE_STORE_TARGET** after documents ingested
- **Do not modify workflows** while runs in progress
- **Never commit secrets** - use environment variables

---

# Troubleshooting

| Issue | Solution |
|-------|----------|
| Database connection failed | Check `DOC_DB_URL` env var |
| Steps stuck in RUNNING | Check worker health via `/api/v1/stats/workers` |
| Docling unavailable | Verify `DOCLING_SERVER_URL` |
| LanceDB conflicts | Remove `lancedb/` directory, re-run |

See [WORKFLOWS.md - Troubleshooting](docs/WORKFLOWS.md#troubleshooting) for details.
