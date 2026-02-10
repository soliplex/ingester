# AGENTS.md

Instructions for AI coding agents working with Soliplex Ingester.

## Project Overview

Document ingestion and RAG pipeline system. Processes documents through configurable workflows (validate, parse, chunk, embed, store) using async workers and stores results in LanceDB vector databases.

**Stack:** Python 3.12+, FastAPI, SQLModel, Pydantic v2, Typer CLI

## Setup and Commands

```bash
uv sync                                        # Install dependencies
uv run --env-file .env si-cli serve --reload   # Run dev server
uv run pytest                                  # Run tests
uv run ruff format . && uv run ruff check .    # Format & lint
uv run mypy src/                               # Type checking
si-cli bootstrap                               # Initialize config files
si-cli db-init                                 # Initialize database
```

## Project Structure

```text
src/soliplex/ingester/
├── cli.py              # CLI entry point (si-cli)
├── server/             # FastAPI app and routes
│   └── routes/         # API endpoint modules
└── lib/
    ├── config.py       # Pydantic settings
    ├── models.py       # SQLModel database models
    ├── operations.py   # Document CRUD operations
    ├── workflow.py     # Workflow step handlers
    ├── dal.py          # Data access layer (storage)
    └── wf/             # Workflow execution engine
        ├── runner.py   # Async worker
        ├── operations.py
        └── registry.py # Workflow/param loading from dual directories

config/
├── workflows/*.yaml    # Workflow definitions
├── params/*.yaml       # System parameter sets (source: app)
└── user_params/*.yaml  # User-uploaded parameter sets (source: user)

tests/
├── unit/              # Unit tests (50% coverage min)
└── functional/        # Integration tests
```

## Code Style

- PEP8 with 126 char line length (ruff configured)
- snake_case for functions/variables, PascalCase for classes
- Type annotations required (Python 3.12+ syntax)
- numpy-style docstrings
- Single-line imports, grouped: stdlib, third-party, local

## Async Requirements

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

## Import Paths

Use `soliplex.ingester` (dot notation), not `soliplex_ingester`:

```python
from soliplex.ingester.lib.models import Document
from soliplex.ingester.lib.config import get_settings
```

## Testing

```bash
uv run pytest                                              # Run all
uv run pytest --cov=soliplex.ingester --cov-report=term-missing  # With coverage
uv run pytest tests/unit/test_operations.py                # Specific file
uv run pytest -k "test_batch"                              # Pattern match
```

- 50% coverage minimum enforced
- Mock external services (Docling, HaikuRAG, Ollama)
- Unit tests in `tests/unit/test_*.py`
- Functional tests in `tests/functional/`

## Configuration

Required: `DOC_DB_URL`

Key optional settings:
- `DOCLING_SERVER_URL` - Document parsing service
- `OLLAMA_BASE_URL` - Embedding model server
- `FILE_STORE_TARGET` - Storage backend (fs, s3)
- `LANCEDB_DIR` - Vector database location
- `PARAM_DIR` / `USER_PARAM_DIR` - System and user parameter set directories (must differ)
- `WORKFLOW_DIR` - Workflow definitions directory

See [CONFIGURATION.md](docs/CONFIGURATION.md) for full reference.

## File Organization

When adding features:
- Database models go in `lib/models.py`
- API endpoints go in `server/routes/`
- Workflow step handlers go in `lib/workflow.py`
- Storage operations use DAL from `lib/dal.py` (never direct file I/O for artifacts)
- Tests go in `tests/unit/` or `tests/functional/`

## Critical Warnings

- Do not upgrade LanceDB beyond 0.25.3 (pinned version)
- Do not change FILE_STORE_TARGET after documents ingested
- Do not modify workflows while runs in progress
- Never commit secrets - use environment variables
- Always use DAL from `lib/dal.py` for artifact storage

## Documentation Standards

All markdown files are linted by pymarkdown via pre-commit. After editing any `.md` file, run:

```bash
pre-commit run --all-files pymarkdown                # Lint all markdown
pre-commit run --files docs/MYFILE.md pymarkdown     # Lint specific file
```

Common rules enforced (disabled: MD013, MD024, MD033, MD036, MD041, MD060):

- **MD022:** Blank line required after every heading
- **MD025:** Only one top-level `#` heading per file
- **MD031:** Blank lines required before and after fenced code blocks
- **MD032:** Blank lines required before and after lists
- **MD034:** No bare URLs — wrap in angle brackets `<URL>` or markdown links
- **MD040:** Fenced code blocks must specify a language (e.g. `` ```bash ``, `` ```python ``)

## Commit Standards

When asked to commit:
- Use conventional commit format
- Include `Co-Authored-By: Claude <noreply@anthropic.com>` trailer
- Stage specific files, avoid `git add -A`
- Never commit .env files or secrets
