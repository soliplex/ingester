# AGENTS.md

Instructions for AI coding agents working with Soliplex Ingester.

## Project Overview

Document ingestion and RAG pipeline system. Processes documents through
configurable workflows (validate, parse, chunk, embed, store) using async
workers and stores results in LanceDB vector databases.

Stack: Python 3.12+, FastAPI, SQLModel, Pydantic v2, Typer CLI

## Setup and Commands

```bash
uv sync                                        # Install dependencies
uv run --env-file .env si-cli serve --reload   # Run dev server
uv run pytest                                  # Run tests
uv run ruff format . && uv run ruff check .    # Format and lint
si-cli bootstrap                               # Initialize config files
si-cli db-init                                 # Initialize database
si-diag status running                         # Show running workflow steps
si-diag status recent hour                     # Recent activity
```

## Project Structure

```text
src/soliplex/ingester/
├── cli.py              # CLI entry point (si-cli)
├── diag_cli.py         # Diagnostic CLI entry point (si-diag)
├── server/             # FastAPI app and routes
│   └── routes/         # API endpoint modules
└── lib/
    ├── config.py       # Pydantic settings
    ├── models.py       # SQLModel database models
    ├── operations.py   # Document CRUD operations
    ├── workflow.py     # Workflow step handlers
    ├── dal.py          # Data access layer (storage backends)
    ├── auth.py         # Authentication (API key, OAuth2 proxy)
    ├── rag.py          # HaikuRAG integration
    ├── processing.py   # Document processing utilities
    └── wf/             # Workflow execution engine
        ├── runner.py   # Async worker
        ├── operations.py
        └── registry.py # Workflow/param loading from YAML

config/
├── workflows/*.yaml    # Workflow definitions
├── params/*.yaml       # System parameter sets (source: app)
└── user_params/*.yaml  # User-uploaded parameter sets (source: user)

tests/
├── unit/              # Unit tests (50% coverage min)
└── functional/        # Integration tests (skipped by default)
```

## Code Conventions

### Python Style

- Ruff enforced, 126 char line length
- snake_case for functions/variables, PascalCase for classes
- Type annotations required (Python 3.12+ syntax)
- Single-line imports, grouped: stdlib, third-party, local
- Ruff target version: py313

### Async Requirements

All I/O operations must use async/await:

```python
# Database access
async with get_session() as session:
    result = await session.exec(query)
    await session.commit()

# Storage operations
from soliplex.ingester.lib.dal import get_operator
operator = get_operator(store_type)
await operator.write(path, data)
```

### Import Paths

Use `soliplex.ingester` (dot notation), not `soliplex_ingester`:

```python
# Correct
from soliplex.ingester.lib.models import Document
from soliplex.ingester.lib.config import get_settings

# Wrong
from soliplex_ingester.lib.models import Document
```

## Testing

```bash
uv run pytest                                              # All unit tests
uv run pytest --cov=soliplex.ingester --cov-report=term-missing  # With coverage
uv run pytest tests/unit/test_operations.py                # Specific file
uv run pytest -k "test_batch"                              # Pattern match
```

Requirements:

- 50% branch coverage minimum enforced
- Mock external services (Docling, HaikuRAG, Ollama)
- Unit tests in `tests/unit/test_*.py`
- Functional tests in `tests/functional/` (skipped by default)
- Test database: in-memory SQLite per test function

## Database

Development: SQLite with aiosqlite
Production: PostgreSQL with psycopg

```bash
si-cli db-init

# Connection string format
DOC_DB_URL="sqlite+aiosqlite:///./db/documents.db"
DOC_DB_URL="postgresql+psycopg://user:pass@host:5432/soliplex"
```

## Key Models

| Model | Purpose |
|-------|---------|
| DocumentBatch | Groups documents for processing |
| Document | Unique documents by SHA256 hash |
| DocumentURI | Maps URIs to documents |
| WorkflowRun | Single workflow execution |
| RunStep | Individual step in workflow |
| RunGroup | Groups workflow runs for a batch |
| SyncState | Incremental sync tracking |

## API Routes

| Prefix | Module | Purpose |
|--------|--------|---------|
| /api/v1/batch | routes/batch.py | Batch management |
| /api/v1/document | routes/document.py | Document ingestion |
| /api/v1/workflow | routes/workflow.py | Workflow control |
| /api/v1/lancedb | routes/lancedb.py | Vector DB management |
| /api/v1/stats | routes/stats.py | Statistics |
| /api/v1/sync-state | routes/sync.py | Sync state tracking |

## Configuration

Required: `DOC_DB_URL`

Key optional settings:

- `DOCLING_SERVER_URL` - Document parsing service
- `OLLAMA_BASE_URL` - Embedding model server
- `FILE_STORE_TARGET` - Storage backend (fs, s3, db)
- `LANCEDB_DIR` - Vector database location
- `PARAM_DIR` / `USER_PARAM_DIR` - Parameter set directories (must differ)
- `WORKFLOW_DIR` - Workflow definitions directory

See `docs/CONFIGURATION.md` for full reference.

## Critical Warnings

- Do not change `FILE_STORE_TARGET` after documents are ingested
- Do not modify workflows while runs are in progress
- Never commit secrets -- use environment variables
- Always use DAL from `lib/dal.py` for artifact storage
- `PARAM_DIR` and `USER_PARAM_DIR` must be different directories

## File Organization

When adding features:

- Database models go in `lib/models.py`
- API endpoints go in `server/routes/`
- Workflow step handlers go in `lib/workflow.py`
- Storage operations go through `lib/dal.py`
- Tests go in `tests/unit/` or `tests/functional/`

## Documentation

Detailed docs in `docs/` folder:

- GETTING_STARTED.md - Quick start tutorial
- ARCHITECTURE.md - System design
- API.md - REST endpoint reference
- WORKFLOWS.md - Workflow configuration
- DATABASE.md - Schema reference
- CONFIGURATION.md - Environment variables
- CLI.md - Command reference (si-cli and si-diag)
- AUTHENTICATION.md - API key and OAuth2 proxy setup
- DOCKER.md - Docker Compose deployment
- PARAMETER_SETS.md - Parameter configuration

## Documentation Standards

All markdown files are linted by pymarkdown via pre-commit. After editing
any `.md` file, run:

```bash
pre-commit run --all-files pymarkdown                # Lint all markdown
pre-commit run --files docs/MYFILE.md pymarkdown     # Lint specific file
```

Common rules enforced (disabled: MD013, MD024, MD033, MD036, MD041, MD060):

- MD022: Blank line required after every heading
- MD025: Only one top-level heading per file
- MD031: Blank lines required before and after fenced code blocks
- MD032: Blank lines required before and after lists
- MD034: No bare URLs -- wrap in angle brackets or markdown links
- MD040: Fenced code blocks must specify a language

## Commit Standards

When asked to commit:

- Use conventional commit format
- Include `Co-Authored-By: Claude <noreply@anthropic.com>` trailer
- Stage specific files, avoid `git add -A`
- Never commit .env files or secrets
