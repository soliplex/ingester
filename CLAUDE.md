# Project Context

This is a Python project to load documents into a RAG system using haiku.rag and docling. The application uses workflows and parameters defined in YAML files to configure the process of converting documents into docling format and then performing chunking, embedding and storing into haiku.rag.

**Key Design Principles:**
- All steps must be retryable and able to recover from application or processing failures
- Progress of each document through workflows is tracked for observability and debugging
- Intermediate artifacts (docling documents, markdown files, chunks, embedding vectors) are stored using a configurable multi-backend system (database, filesystem, S3)

**Integration Points:**
- Svelte UI in the `ui/` directory with its own CLAUDE.md for frontend development
- REST API endpoints for document management and workflow control
- Data ingestion via `/api/v1/document/upload` endpoint
- Agent processes managed by github.com/soliplex/ingester-agents

---

# Quick Reference

## Documentation Map

This project has comprehensive documentation in the `docs/` folder:

- **[Getting Started](docs/GETTING_STARTED.md)** - Installation, first batch, deployment (15-30 min tutorial)
- **[Architecture](docs/ARCHITECTURE.md)** - System design, components, data flow (15-20 min read)
- **[API Reference](docs/API.md)** - All REST endpoints with examples (reference guide)
- **[Workflows](docs/WORKFLOWS.md)** - Workflow system, custom steps, lifecycle events (25-30 min read)
- **[Database](docs/DATABASE.md)** - Schema, models, relationships, queries (reference guide)
- **[Configuration](docs/CONFIGURATION.md)** - All environment variables and settings (reference + guide)
- **[CLI Reference](docs/CLI.md)** - Command-line interface commands (reference guide)

## Common Tasks Quick Links

When working on specific tasks, jump directly to:

- **Add API endpoint** → [API.md - Adding Endpoints](docs/API.md#adding-endpoints)
- **Create custom workflow step** → [WORKFLOWS.md - Custom Handlers](docs/WORKFLOWS.md#custom-step-handlers)
- **Configure environment** → [CONFIGURATION.md](docs/CONFIGURATION.md)
- **Troubleshoot workflows** → [WORKFLOWS.md - Troubleshooting](docs/WORKFLOWS.md#troubleshooting)
- **Query database** → [DATABASE.md - Query Examples](docs/DATABASE.md#query-examples)
- **View lifecycle history** → [API.md - Lifecycle Endpoint](docs/API.md#get-apiv1workflowrunsworkflow_idlifecycle)
- **Deploy to production** → [CLI.md - Deployment](docs/CLI.md#deployment)
- **UI development** → [ui/CLAUDE.md](ui/CLAUDE.md)

---

# Key Technologies

## Core Framework
* **Language:** Python 3.12+ (requires 3.12+ for modern type annotations)
* **Web Framework:** FastAPI 0.120+ with uvicorn ASGI server
* **CLI Framework:** Typer (typer-slim 0.20+)
* **Data Validation:** Pydantic v2, pydantic-settings 2.11+

## Database & Persistence
* **ORM:** SQLModel 0.0.27+ (combines SQLAlchemy + Pydantic)
* **Async Database Drivers:**
  - aiosqlite (development, excludes version 0.22.0 due to bug)
  - psycopg 3.2+ with binary and pool support (production)
* **Migrations:** Alembic 1.17+ (installed, but migrations not yet implemented - currently uses SQLModel.metadata.create_all())
* **Vector Database:** LanceDB 0.25.3 ⚠️ **PINNED via haiku-rag dependency - exact version required for compatibility**
* **Dev Analytics:** duckdb 1.4+ (development only)

## Storage Abstraction
* **Multi-Backend Storage:** OpenDAL 0.46+ (filesystem, S3, database)
* **File System Abstraction:** fsspec 2025.10+ (underlying storage interface)
* **Cloud Storage Support:** S3-compatible backends (AWS S3, MinIO, etc.)

## Document Processing
* **Document Parser:** Docling (external HTTP service)
* **Docling Models:** docling-core 2.51+
* **RAG System:** haiku-rag-slim 0.23.0+ (HaikuRAG client)
* **PDF Processing:**
  - pypdf 6.4+
  - pdf-splitter (custom fork from github.com/runyaga/pdf-splitter)

## Async Operations & Utilities
* **Async File I/O:** aiofiles 25.1+
* **Async HTTP Client:** aiohttp 3.13+
* **Async LRU Cache:** async-lru 2.0+
* **Retry Logic:** tenacity 9.1+ (exponential backoff with retry)
* **WebSocket Support:** websockets 15.0+ (real-time UI updates)

## Development & Testing
* **Testing Framework:** pytest 8.4+, pytest-asyncio, pytest-env
* **Coverage:** pytest-cov 7.0+, coverage 7.11+ (50% minimum required)
* **Linting & Formatting:** ruff 0.14+ (replaces black, flake8, isort, pyupgrade)
* **Type Checking:** Built-in Python type hints (validated by ruff)

---

# Project Structure

```
soliplex_ingester/
├── config/
│   ├── workflows/              # Workflow YAML definitions
│   │   ├── batch.yaml          # Standard batch processing workflow
│   │   ├── batch_split.yaml    # Batch workflow with PDF splitting (default)
│   │   ├── interactive.yaml    # Interactive processing workflow
│   │   └── test_*.yaml         # Test workflows
│   └── params/                 # Parameter set YAML files
│       └── default.yaml        # Default parameter configuration
│
├── src/soliplex/ingester/
│   ├── lib/
│   │   ├── config.py           # Pydantic settings (env vars, config)
│   │   ├── models.py           # SQLModel database models + enums
│   │   ├── dal.py              # Data Access Layer & storage operators
│   │   ├── operations.py       # Core document CRUD operations
│   │   ├── workflow.py         # Built-in workflow step handlers
│   │   ├── docling.py          # Docling service HTTP client
│   │   ├── rag.py              # HaikuRAG client integration
│   │   └── wf/                 # Workflow execution engine
│   │       ├── registry.py     # Workflow/param YAML loader & registry
│   │       ├── operations.py   # Workflow run CRUD operations
│   │       └── runner.py       # Worker process & execution engine
│   │
│   ├── server/
│   │   ├── __init__.py         # FastAPI app factory (create_app)
│   │   └── routes/             # API endpoint modules
│   │       ├── batch.py        # Batch management APIs (/api/v1/batch/*)
│   │       ├── document.py     # Document operations (/api/v1/document/*)
│   │       ├── workflow.py     # Workflow control (/api/v1/workflow/*)
│   │       └── stats.py        # System statistics (/api/v1/stats/*)
│   │
│   ├── cli.py                  # Typer CLI commands (si-cli)
│   └── example/                # Example custom workflow implementations
│       └── __init__.py         # Custom step handlers example
│
├── ui/                         # Svelte 5 frontend application
│   ├── src/
│   │   ├── lib/
│   │   │   └── config/
│   │   │       └── api.ts      # FastAPI backend configuration
│   │   ├── routes/             # SvelteKit routes
│   │   └── ...                 # (see ui/CLAUDE.md for details)
│   ├── CLAUDE.md               # UI-specific development guide
│   └── package.json            # UI dependencies & scripts
│
├── tests/
│   ├── unit/                   # Fast unit tests (default test suite)
│   │   ├── test_workflow.py    # Workflow system tests
│   │   ├── test_operations.py  # Operations tests
│   │   ├── test_rag_config.py  # RAG configuration tests
│   │   └── test_*.py           # Additional unit tests
│   └── functional/             # Integration tests (not in default run)
│
├── docs/                       # Comprehensive documentation
│   ├── README.md               # Documentation index
│   ├── GETTING_STARTED.md      # Installation & quick start
│   ├── ARCHITECTURE.md         # System design overview
│   ├── API.md                  # REST API reference
│   ├── WORKFLOWS.md            # Workflow system guide
│   ├── DATABASE.md             # Database schema reference
│   ├── CONFIGURATION.md        # Environment variables reference
│   └── CLI.md                  # CLI command reference
│
├── pyproject.toml              # Project metadata, dependencies, tool config
├── README.md                   # Main project README with doc links
├── CLAUDE.md                   # This file - AI assistant instructions
├── LICENSE                     # Project license
└── .github/
    └── workflows/              # GitHub Actions CI/CD
        └── build-docs.yml      # Documentation build workflow
```

---

# Configuration Quick Reference

## Essential Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DOC_DB_URL` | ✅ | - | Database connection (sqlite+aiosqlite:// or postgresql+psycopg://) |
| `DOCLING_SERVER_URL` | No | `http://localhost:5001/v1` | Docling service endpoint |
| `DOCLING_HTTP_TIMEOUT` | No | `600` | Docling HTTP timeout in seconds |
| `LOG_LEVEL` | No | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) |
| `FILE_STORE_TARGET` | No | `fs` | Storage backend: `fs`, `db`, or `s3` |
| `FILE_STORE_DIR` | No | `file_store` | Base directory for file storage |
| `LANCEDB_DIR` | No | `lancedb` | LanceDB vector database directory (supports filesystem paths and S3 URIs) |
| `WORKFLOW_DIR` | No | `config/workflows` | Workflow YAML directory |
| `DEFAULT_WORKFLOW_ID` | No | `batch_split` | Default workflow name |
| `PARAM_DIR` | No | `config/params` | Parameter sets directory |
| `DEFAULT_PARAM_ID` | No | `default` | Default parameter set name |
| `INGEST_QUEUE_CONCURRENCY` | No | `20` | Max concurrent queue operations |
| `INGEST_WORKER_CONCURRENCY` | No | `10` | Max concurrent workflow steps per worker |
| `DOCLING_CONCURRENCY` | No | `3` | Max concurrent Docling requests |
| `WORKER_CHECKIN_INTERVAL` | No | `120` | Worker heartbeat interval in seconds |
| `WORKER_CHECKIN_TIMEOUT` | No | `600` | Worker timeout threshold in seconds |
| `WORKER_TASK_COUNT` | No | `5` | Number of workflow steps to fetch per query |
| `EMBED_BATCH_SIZE` | No | `1000` | Batch size for embedding operations |
| `INPUT_S3__*` | No | - | Source S3 config (BUCKET, ENDPOINT_URL, ACCESS_KEY_ID, ACCESS_SECRET, REGION) |
| `ARTIFACT_S3__*` | No | - | Artifact S3 config (BUCKET, ENDPOINT_URL, ACCESS_KEY_ID, ACCESS_SECRET, REGION) |
| `DO_RAG` | No | `True` | Enable/disable HaikuRAG (for testing) |

**S3 Configuration Notes:**
- **LanceDB S3:** When using S3 URIs for `LANCEDB_DIR` (e.g., `s3://bucket/path`), standard AWS environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`) must be configured.
- **Artifact/Input S3:** For artifact storage backends, use nested environment variables with `__` delimiter (e.g., `ARTIFACT_S3__BUCKET=my-bucket`, `INPUT_S3__ENDPOINT_URL=http://127.0.0.1:8333`).
- See [CONFIGURATION.md - S3 Configuration Overview](docs/CONFIGURATION.md#s3-configuration-overview) for detailed comparison of both S3 systems.

**Complete reference:** See [CONFIGURATION.md](docs/CONFIGURATION.md) for all environment variables with detailed descriptions.

---

# Development Commands

## Essential Commands

*   **Install dependencies:** `uv sync`
*   **Run the application (dev):** `uv run --env-file .env si-cli serve --reload`
*   **Run the application (prod):** `si-cli serve --host 0.0.0.0 --workers 4`
*   **Run worker only:** `si-cli worker` *(use for additional workers; serve includes one by default)*
*   **Initialize database:** `si-cli db-init`
*   **Bootstrap (setup all configs):** `si-cli bootstrap`
*   **Initialize environment file:** `si-cli init-env`
*   **Run tests:** `uv run pytest`
*   **Run tests with coverage:** `uv run pytest --cov=soliplex --cov-report=html`
*   **Format code:** `uv run ruff format .`
*   **Lint code:** `uv run ruff check .`

## Utility Commands

*   **Validate configuration:** `si-cli validate-settings`
*   **List workflows:** `si-cli list-workflows`
*   **View workflow definition:** `si-cli dump-workflow batch`
*   **List parameter sets:** `si-cli list-param-sets`
*   **Validate HaikuRAG batch:** `si-cli validate-haiku 1`

**Full reference:** See [CLI.md](docs/CLI.md) for all commands with options and examples.

---

# Development Workflows

## Adding a New API Endpoint

**Quick Pattern:**
1. Choose route module: `server/routes/batch.py`, `document.py`, `workflow.py`, or `stats.py`
2. Define Pydantic response model in `lib/models.py` if needed (requests can be simple primitives)
3. Create async endpoint with `AsyncSession` dependency
4. Register router in `server/__init__.py` if creating new module
5. Write unit tests in `tests/unit/`

**Example:**
```python
# In server/routes/stats.py
from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession
from soliplex.ingester.lib.models import get_session, MyResponse

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])

@router.get("/my-endpoint", response_model=MyResponse)
async def get_my_stats(
    session: AsyncSession = Depends(get_session)
) -> MyResponse:
    """Get custom statistics."""
    async with session:
        # Query database
        result = await session.exec(...)
        return MyResponse.model_validate(result)
```

**Detailed guide:** [API.md - Adding Endpoints](docs/API.md#adding-endpoints)

## Creating a Custom Workflow Step

**Quick Pattern:**
1. Create async function with standard signature (see below)
2. Add to workflow YAML in `config/workflows/`
3. Ensure module is importable (add to PYTHONPATH or install as package)

**Function Signature:**
```python
from soliplex.ingester.lib.models import StepConfig
from typing import Any

async def my_custom_step(
    batch_id: int | None = None,
    doc_hash: str | None = None,
    source: str | None = None,
    step_config: StepConfig | None = None,
) -> dict[str, Any]:
    """
    Custom workflow step handler.

    Returns
    -------
    dict[str, Any]
        Result metadata (logged to database)
    """
    # Access parameters from YAML
    my_param = step_config.params.get("my_param", "default")

    # Perform processing
    result = await my_processing_logic(doc_hash, my_param)

    # Return metadata
    return {"status": "success", "items_processed": len(result)}
```

**YAML Configuration:**
```yaml
# config/workflows/my_workflow.yaml
id: my_workflow
name: My Custom Workflow
item_steps:
  my_step:
    name: My Custom Step
    retries: 3
    method: mymodule.handlers.my_custom_step
    parameters:
      my_param: value
```

**Detailed guide:** [WORKFLOWS.md - Custom Step Handlers](docs/WORKFLOWS.md#custom-step-handlers)

## Working with Database

**Quick Pattern:**
```python
from soliplex.ingester.lib.models import get_session, Document
from sqlmodel import select

# Simple query
async with get_session() as session:
    query = select(Document).where(Document.hash == doc_hash)
    result = await session.exec(query)
    doc = result.first()

# Update with commit
async with get_session() as session:
    query = select(Document).where(Document.hash == doc_hash)
    result = await session.exec(query)
    doc = result.first()
    doc.status = "processed"
    session.add(doc)
    await session.commit()
```

**Detailed patterns:** [DATABASE.md - Query Examples](docs/DATABASE.md#query-examples)

---

# Code Style & Conventions

*   Follow PEP8 guidelines with 126 character line length (configured in ruff)
*   Use snake_case for functions/variables and PascalCase for classes
*   Prefer `async`/`await` patterns throughout the codebase, especially for I/O operations
*   Write docstrings for all functions using the numpy style
*   Use full type annotations (Python 3.12+ syntax)
*   Ensure all new features include corresponding unit tests in `/tests/unit/` directory
*   Project and test configuration should be included in pyproject.toml where possible

## Import Organization
*   Use single-line imports (configured in ruff isort)
*   Group imports: stdlib, third-party, local
*   Example:
    ```python
    import asyncio
    from typing import Any

    from fastapi import APIRouter
    from sqlmodel import select

    from soliplex.ingester.lib.models import Document
    ```

## Testing Requirements
*   Unit tests required for all new features (50% coverage minimum enforced)
*   Use pytest fixtures from `conftest.py`
*   Mock external services (Docling, HaikuRAG) in unit tests
*   Test files: `tests/unit/test_*.py`
*   Coverage excludes: `cli.py`, `docling.py`, `app.py`, `example/` (note: `rag.py` is NOT excluded)

---

# AI Assistant Instructions

## Documentation Usage Guidelines

**Always check docs/ first** before answering questions about:
- Architecture and system design → [ARCHITECTURE.md](docs/ARCHITECTURE.md)
- API endpoints → [API.md](docs/API.md)
- Workflow system → [WORKFLOWS.md](docs/WORKFLOWS.md)
- Configuration → [CONFIGURATION.md](docs/CONFIGURATION.md)

**Link to relevant docs** when answering questions rather than duplicating content.

## Code Modification Guidelines

- **Read before editing:** Always use Read tool on files before suggesting changes
- **Preserve async patterns:** All I/O operations must use async/await
- **Follow type annotations:** Add type hints to all new functions (Python 3.12+ syntax)
- **Use existing patterns:** Study similar code before creating new endpoints/steps
- **Test your changes:** Write or update unit tests for new functionality
- **Don't introduce new libraries** without explicit user approval

## File Organization Rules

- **Database models:** Add to `lib/models.py`
- **API endpoints:** Add to appropriate file in `server/routes/`
- **Workflow handlers:** Add to `lib/workflow.py` (built-in) or custom module
- **Utility functions:** Add to `lib/operations.py` or create new focused module
- **Tests:** Mirror source structure in `tests/unit/`

## Storage Operations

**Always use DAL (Data Access Layer)** from `lib/dal.py` for artifact storage:
- Use `get_operator(store_type)` to get storage operator for specific artifact types
- Supported types: `raw`, `markdown`, `json`, `chunks`, `embeddings`
- Abstracts filesystem, S3, and database storage automatically
- Handles `FILE_STORE_TARGET` configuration without additional code

**Never use** direct file I/O (`open()`, `pathlib`) for artifact storage - always go through DAL.

## S3 Storage Systems

This project has **two separate S3 configuration systems** - do not confuse them:

**1. LanceDB S3 (Vector Storage):**
- Configured via: `LANCEDB_DIR=s3://bucket/path` + standard AWS env vars
- Environment variables: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `AWS_ENDPOINT` (optional), `AWS_ALLOW_HTTP` (optional)
- Used by: HaikuRAG/LanceDB for vector embeddings
- Code location: `lib/rag.py:75-93`

**2. Artifact S3 (Processing Artifacts):**
- Configured via: Nested Pydantic settings with `__` delimiter
- Environment variables: `ARTIFACT_S3__BUCKET`, `ARTIFACT_S3__ACCESS_SECRET`, `INPUT_S3__BUCKET`, etc.
- Used by: DAL layer for intermediate files (documents, markdown, chunks)
- Code location: `lib/dal.py:96-117`, `lib/config.py:7-34`

**Key Difference:** Field names differ - LanceDB uses `AWS_SECRET_ACCESS_KEY` (AWS standard), Artifact uses `ARTIFACT_S3__ACCESS_SECRET` (Pydantic nested config).

## Database Access Patterns

**In API endpoints (preferred):**
```python
from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession
from soliplex.ingester.lib.models import get_session

@router.get("/endpoint")
async def my_endpoint(session: AsyncSession = Depends(get_session)):
    # session auto-managed by FastAPI
    result = await session.exec(query)
    return result
```

**In standalone functions:**
```python
from soliplex.ingester.lib.models import get_session
from sqlmodel import select

async with get_session() as session:
    query = select(Document).where(Document.hash == doc_hash)
    result = await session.exec(query)
    doc = result.first()
    if doc:
        doc.status = "processed"
        session.add(doc)
        await session.commit()  # Required for modifications
```

## Error Handling Pattern

Follow this standard error handling pattern used throughout the codebase:

```python
from fastapi import Response, status
import logging

logger = logging.getLogger(__name__)

@router.post("/endpoint")
async def my_endpoint(response: Response):
    try:
        result = await perform_operation()
    except KeyError as e:
        logger.exception("Operation failed")
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"error": str(e)}
    except Exception as ex:
        logger.exception("Operation failed")
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"error": str(ex)}
    else:
        return {"status": "success", "data": result}
```

## File Upload Pattern

When creating file upload endpoints, use FastAPI Form data (see `server/routes/document.py:34-45`):

```python
from fastapi import UploadFile, Form
import json

@router.post("/upload")
async def upload_file(
    file: UploadFile = None,           # Optional file
    field1: str = Form(...),            # Required field
    field2: int = Form(0),              # Optional with default
    json_field: str = Form("{}"),       # JSON as string
):
    file_bytes = await file.read() if file else None
    try:
        meta = json.loads(json_field)   # Parse JSON field
        if not isinstance(meta, dict):
            raise TypeError("Metadata must be a dictionary")
    except json.JSONDecodeError:
        return {"error": "metadata should be valid JSON object"}
    # Process upload...
```

## Safety & Security

- **Never commit secrets** - use environment variables
- **Validate user input** - use Pydantic models for API requests
- **Use parameterized queries** - SQLModel prevents SQL injection
- **Handle file uploads carefully** - validate file types and sizes

## When to Ask for Clarification

- Multiple valid approaches exist (ask user to choose)
- Requirements are ambiguous
- Changes affect core workflow execution logic
- Breaking changes to existing APIs
- New dependencies need to be added

## Worker Architecture

Workers automatically check in every `WORKER_CHECKIN_INTERVAL` seconds (default: 120) to maintain health status:
- Checkin records stored in database for monitoring
- Workers considered stale after `WORKER_CHECKIN_TIMEOUT` (default: 600 seconds)
- Stats API endpoint (`/api/v1/stats/workers`) shows active workers with recent checkins
- Enables monitoring of distributed worker deployments
- Workflow steps include retry logic configured in YAML (`retries` field)

## Documentation Access Strategy

**Read files directly** when:
- User asks about a specific file/function/endpoint you can locate
- Question requires checking 1-3 specific code locations
- You need to verify a specific configuration value
- Looking for a specific class definition or function

**Use Task tool (subagent_type='claude-code-guide')** when:
- User asks "Can Claude Code do X?" or "How do I use Claude Code for Y?"
- Questions about Claude Code CLI features, hooks, or MCP servers
- Questions about the Claude Agent SDK

**Use Task tool (subagent_type='Explore')** when:
- Question requires understanding overall architecture
- Need to trace functionality across multiple files
- "Where is X handled?" questions without obvious file location
- Questions like "How does the workflow system work?"
- User asks about codebase structure or component relationships

## Project Initialization Workflow

When helping users set up the project for the first time, follow this sequence:

1. **Installation:** `uv sync` or `pip install -e .`
2. **Quick setup (recommended):** `si-cli bootstrap` - combines steps 3-5
3. **Manual setup - Environment:** `si-cli init-env` (auto-generates .env file)
4. **Manual setup - Config files:** `si-cli init-config` (creates workflow/param files)
5. **Manual setup - HaikuRAG:** `si-cli init-haiku` (initializes RAG configuration)
6. **Database initialization:** `si-cli db-init` (creates database tables)
7. **Validation:** `si-cli validate-settings` (verify all configuration)

The `bootstrap` command is preferred as it automates the configuration setup.

---

# Specific Instructions for Claude

*   When creating new API endpoints, follow the existing pattern in `soliplex.ingester.server.routes`
*   Use existing Pydantic models where possible for responses. Requests can be simple primitives
*   If a task involves database operations, ensure the functions are asynchronous
*   Do not introduce new libraries without explicit approval
*   If you need to run any Python code, use the correct concurrent execution pattern within a single message
*   Move detailed, reusable instructions to project-specific skills to avoid bloating this file

---

# Troubleshooting Quick Reference

## Common Issues

**Database connection failed**
- Check `DOC_DB_URL` environment variable
- For SQLite: Ensure directory exists and is writable
- For PostgreSQL: Verify server is running and credentials are correct

**Workflow steps stuck in RUNNING**
- Check worker health: `curl http://localhost:8000/api/v1/stats/workers`
- View recent checkins to see if workers are alive
- Query stuck steps: See [WORKFLOWS.md - Troubleshooting](docs/WORKFLOWS.md#troubleshooting)

**Docling service unavailable**
- Verify `DOCLING_SERVER_URL` points to running Docling instance
- Test connection: `curl $DOCLING_SERVER_URL/health`

**LanceDB version conflicts**
- LanceDB is pinned to 0.25.3 - do not upgrade
- If issues persist, remove `lancedb/` directory and re-run workflows

**Import errors for custom workflow steps**
- Ensure custom module is in PYTHONPATH
- Or install as package: `uv add -e /path/to/custom-module`
- Check method path in workflow YAML matches module path

**Tests failing with coverage errors**
- Minimum 50% coverage required
- Check coverage report: `uv run pytest --cov=soliplex --cov-report=html`
- Open `htmlcov/index.html` to see uncovered lines

**Detailed troubleshooting:** See [WORKFLOWS.md - Troubleshooting](docs/WORKFLOWS.md#troubleshooting)

---

# Failure Modes

*   Do not modify the project structure unless specifically instructed
*   Avoid using blocking I/O operations in the main application code
*   Do not upgrade LanceDB version without explicit approval (pinned to 0.25.3)
*   Do not change `FILE_STORE_TARGET` after documents are ingested (data will be lost)
*   Do not modify workflow definitions while runs are in progress (can cause inconsistent state)
