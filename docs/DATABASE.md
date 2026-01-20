# Database Models and Schema

## Overview

Soliplex Ingester uses SQLModel (built on SQLAlchemy) for database modeling with async support. The system supports both SQLite (development) and PostgreSQL (production).

Database models defined in: `src/soliplex/ingester/lib/models.py`

## Database Connection

### Configuration

Set via environment variable:
```bash
DOC_DB_URL="sqlite+aiosqlite:///./db/documents.db"
# or
DOC_DB_URL="postgresql+asyncpg://user:pass@localhost/soliplex"
```

### Database Manager

The `Database` class manages engine lifecycle and session creation with automatic connection pooling.

```python
from soliplex.ingester.lib.models import Database

# Initialize once at application startup
await Database.initialize()

# Or with custom URL (for testing)
await Database.initialize("sqlite+aiosqlite:///:memory:")

# Get sessions anywhere in the app
async with Database.session() as session:
    result = await session.exec(select(Document))
    # Transaction auto-commits on success, rollback on exception

# Cleanup at shutdown
await Database.close()

# Reset and reinitialize (primarily for testing)
await Database.reset(url)
```

### Backwards-Compatible Functions

```python
from soliplex.ingester.lib.models import get_session, get_engine

async with get_session() as session:
    result = await session.exec(select(Document))

engine = await get_engine()
```

## Core Models

### DocumentBatch

Represents a batch of documents ingested together.

**Table:** `documentbatch`

**Fields:**
- `id` (int, primary key) - Auto-increment batch ID
- `name` (str) - Human-readable batch name
- `source` (str) - Source system identifier
- `start_date` (datetime) - When batch processing started
- `completed_date` (datetime, nullable) - When batch completed
- `batch_params` (dict[str, str]) - JSON metadata

**Computed Fields:**
- `duration` (float) - Processing time in seconds (None if not completed)

**Example:**
```json
{
  "id": 1,
  "name": "Q4 Financial Reports",
  "source": "sharepoint",
  "start_date": "2025-01-15T10:00:00",
  "completed_date": "2025-01-15T12:30:00",
  "batch_params": {"department": "finance"},
  "duration": 9000.0
}
```

---

### Document

Represents a unique document identified by content hash.

**Table:** `document`

**Fields:**
- `hash` (str, primary key) - SHA256 content hash (format: "sha256-...")
- `mime_type` (str) - Document MIME type
- `file_size` (int, nullable) - Size in bytes
- `doc_meta` (dict[str, str]) - JSON metadata

**Relationships:**
- Multiple `DocumentURI` records can reference the same document

**Deduplication:**
Documents are deduplicated by hash. If the same file is ingested multiple times, only one Document record exists.

**Example:**
```json
{
  "hash": "sha256-a1b2c3d4e5f6...",
  "mime_type": "application/pdf",
  "file_size": 1024000,
  "doc_meta": {
    "author": "John Doe",
    "title": "Q4 Report"
  }
}
```

---

### DocumentURI

Maps source URIs to document hashes, allowing multiple URIs to reference the same document.

**Table:** `documenturi`

**Fields:**
- `id` (int, primary key) - Auto-increment ID
- `doc_hash` (str, foreign key) - References `document.hash`
- `uri` (str) - Source system path/identifier
- `source` (str) - Source system name
- `version` (int) - Version number (increments on changes)
- `batch_id` (int, foreign key, nullable) - Associated batch

**Constraints:**
- Unique constraint on `(uri, source)` - One active URI per source

**Use Cases:**
- Track document locations across source systems
- Detect when a document at a URI has changed (hash mismatch)
- Support document versioning

**Example:**
```json
{
  "id": 42,
  "doc_hash": "sha256-a1b2c3d4e5f6...",
  "uri": "/sharepoint/finance/q4-report.pdf",
  "source": "sharepoint",
  "version": 2,
  "batch_id": 1
}
```

---

### DocumentURIHistory

Tracks historical versions of documents at specific URIs.

**Table:** `documenturihistory`

**Fields:**
- `id` (int, primary key) - Auto-increment ID
- `doc_uri_id` (int, foreign key) - References `documenturi.id`
- `version` (int) - Version number
- `hash` (str) - Document hash at this version
- `process_date` (datetime) - When this version was processed
- `action` (str) - Action taken ("created", "updated", "deleted")
- `batch_id` (int, foreign key, nullable) - Associated batch
- `histmeta` (dict[str, str]) - JSON metadata

**Use Cases:**
- Audit trail of document changes
- Rollback to previous versions
- Compliance and record-keeping

**Example:**
```json
{
  "id": 100,
  "doc_uri_id": 42,
  "version": 1,
  "hash": "sha256-old-hash...",
  "process_date": "2025-01-10T10:00:00",
  "action": "created",
  "batch_id": 1,
  "histmeta": {"user": "importer"}
}
```

---

### DocumentBytes

Stores raw file bytes and artifacts in the database.

**Table:** `documentbytes`

**Fields:**
- `hash` (str, primary key) - Document hash
- `artifact_type` (str, primary key) - Type of artifact
- `storage_root` (str, primary key) - Storage location identifier
- `file_size` (int, nullable) - Size in bytes (auto-computed from file_bytes)
- `file_bytes` (bytes) - Raw binary data

**Artifact Types:**
- `document` - Raw document
- `parsed_markdown` - Extracted markdown
- `parsed_json` - Structured JSON
- `chunks` - Text chunks
- `embeddings` - Vector embeddings
- `rag` - RAG metadata

**Note:** For production, consider using file storage instead of database storage for large binaries.

**Example:**
```json
{
  "hash": "sha256-a1b2c3d4e5f6...",
  "artifact_type": "parsed_markdown",
  "storage_root": "db",
  "file_size": 50000,
  "file_bytes": "..."
}
```

---

## Workflow Models

### RunGroup

Groups related workflow runs together.

**Table:** `rungroup`

**Fields:**
- `id` (int, primary key) - Auto-increment ID
- `name` (str, nullable) - Optional group name
- `workflow_definition_id` (str) - Workflow used
- `param_definition_id` (str) - Parameter set used
- `batch_id` (int, foreign key, nullable) - Associated batch
- `created_date` (datetime) - When group was created
- `start_date` (datetime) - When first run started
- `completed_date` (datetime, nullable) - When all runs completed
- `status` (RunStatus) - Overall group status
- `status_date` (datetime, nullable) - When status last changed
- `status_message` (str, nullable) - Status description
- `status_meta` (dict[str, str]) - JSON metadata

**Relationships:**
- Has many `WorkflowRun` records
- Has many `LifecycleHistory` records

**Example:**
```json
{
  "id": 5,
  "name": "Batch 1 Processing",
  "workflow_definition_id": "batch",
  "param_definition_id": "default",
  "batch_id": 1,
  "created_date": "2025-01-15T10:00:00",
  "start_date": "2025-01-15T10:01:00",
  "completed_date": null,
  "status": "RUNNING",
  "status_date": "2025-01-15T10:30:00",
  "status_message": "Processing documents",
  "status_meta": {}
}
```

---

### WorkflowRun

Represents a single workflow execution for one document.

**Table:** `workflowrun`

**Fields:**
- `id` (int, primary key) - Auto-increment ID
- `workflow_definition_id` (str) - Workflow definition ID
- `run_group_id` (int, foreign key) - Parent group
- `batch_id` (int, foreign key) - Associated batch
- `doc_id` (str) - Document hash being processed
- `priority` (int) - Processing priority (higher = more urgent)
- `created_date` (datetime) - When run was created
- `start_date` (datetime) - When first step started
- `completed_date` (datetime, nullable) - When all steps completed
- `status` (RunStatus) - Current status
- `status_date` (datetime, nullable) - When status last changed
- `status_message` (str, nullable) - Status description
- `status_meta` (dict[str, str]) - JSON metadata
- `run_params` (dict[str, str|int|bool]) - Runtime parameters

**Computed Fields:**
- `duration` (float) - Processing time in seconds (None if not completed)

**Relationships:**
- Has many `RunStep` records
- Belongs to `RunGroup`
- References `Document` via `doc_id`

**Example:**
```json
{
  "id": 100,
  "workflow_definition_id": "batch",
  "run_group_id": 5,
  "batch_id": 1,
  "doc_id": "sha256-a1b2c3d4e5f6...",
  "priority": 0,
  "created_date": "2025-01-15T10:00:00",
  "start_date": "2025-01-15T10:01:00",
  "completed_date": null,
  "status": "RUNNING",
  "status_date": "2025-01-15T10:05:00",
  "status_message": "Processing step 3 of 5",
  "status_meta": {},
  "run_params": {},
  "duration": null
}
```

---

### RunStep

Represents one step within a workflow run.

**Table:** `runstep`

**Fields:**
- `id` (int, primary key) - Auto-increment ID
- `workflow_run_id` (int, foreign key) - Parent workflow run
- `workflow_step_number` (int) - Step sequence number
- `workflow_step_name` (str) - Step name/identifier
- `step_config_id` (int, foreign key) - Configuration used
- `step_type` (WorkflowStepType) - Type of step
- `is_last_step` (bool) - Whether this is the final step
- `created_date` (datetime) - When step was created
- `priority` (int) - Processing priority
- `start_date` (datetime, nullable) - When step started executing
- `status_date` (datetime, nullable) - When status last changed
- `completed_date` (datetime, nullable) - When step completed
- `retry` (int) - Current retry attempt (0-indexed)
- `retries` (int) - Maximum retry attempts
- `status` (RunStatus) - Current status
- `status_message` (str, nullable) - Status description
- `status_meta` (dict[str, str]) - JSON metadata
- `worker_id` (str, nullable) - Worker processing this step

**Computed Fields:**
- `duration` (float) - Execution time in seconds (None if not completed)

**Relationships:**
- Belongs to `WorkflowRun`
- References `StepConfig`

**Example:**
```json
{
  "id": 500,
  "workflow_run_id": 100,
  "workflow_step_number": 2,
  "workflow_step_name": "parse",
  "step_config_id": 10,
  "step_type": "parse",
  "is_last_step": false,
  "created_date": "2025-01-15T10:01:00",
  "priority": 0,
  "start_date": "2025-01-15T10:02:00",
  "status_date": "2025-01-15T10:05:00",
  "completed_date": null,
  "retry": 0,
  "retries": 1,
  "status": "RUNNING",
  "status_message": "Parsing with Docling",
  "status_meta": {},
  "worker_id": "worker-abc-123",
  "duration": null
}
```

---

### StepConfig

Stores step configuration for reuse and tracking.

**Table:** `stepconfig`

**Fields:**
- `id` (int, primary key) - Auto-increment ID
- `created_date` (datetime, nullable) - When config was created
- `step_type` (WorkflowStepType) - Type of step
- `config_json` (dict[str, str|int|bool], nullable) - Step parameters
- `cuml_config_json` (str, nullable) - Cumulative config from previous steps

**Use Cases:**
- Deduplicate identical configurations
- Track which configuration was used for each run
- Audit changes to processing parameters

**Example:**
```json
{
  "id": 10,
  "created_date": "2025-01-15T09:00:00",
  "step_type": "parse",
  "config_json": {
    "format": "markdown",
    "ocr_enabled": true
  },
  "cuml_config_json": "{\"validate\":{...},\"parse\":{...}}"
}
```

---

### ConfigSet

Represents a complete parameter set configuration.

**Table:** `configset`

**Fields:**
- `id` (int, primary key) - Auto-increment ID
- `yaml_id` (str) - Parameter set ID from YAML
- `yaml_contents` (str) - Full YAML contents
- `created_date` (datetime, nullable) - When loaded

**Relationships:**
- Has many `ConfigSetItem` records (junction table)
- Links to multiple `StepConfig` records

**Use Cases:**
- Track which parameter sets were used
- Reproduce exact configurations
- Version control for processing parameters

---

### ConfigSetItem

Junction table linking config sets to step configs.

**Table:** `configsetitem`

**Fields:**
- `config_set_id` (int, primary key, foreign key) - References `configset.id`
- `config_id` (int, primary key, foreign key) - References `stepconfig.id`

---

### LifecycleHistory

Tracks lifecycle events during workflow execution.

**Table:** `lifecyclehistory`

**Fields:**
- `id` (int, primary key) - Auto-increment ID
- `event` (LifeCycleEvent) - Type of event
- `handler_name` (str, nullable) - Name of the handler processing the event
- `run_group_id` (int, foreign key) - Associated run group
- `workflow_run_id` (int, foreign key) - Associated workflow run
- `step_id` (int, nullable) - Associated step (if applicable)
- `start_date` (datetime) - When event started
- `completed_date` (datetime, nullable) - When event completed
- `status` (RunStatus) - Event status
- `status_date` (datetime, nullable) - When status changed
- `status_message` (str, nullable) - Status description
- `status_meta` (dict[str, str]) - JSON metadata

**Event Types:**
- `group_start` / `group_end`
- `item_start` / `item_end` / `item_failed`
- `step_start` / `step_end` / `step_failed`

**Use Cases:**
- Audit trail of workflow execution
- Performance monitoring
- Debugging workflow issues

---

### WorkerCheckin

Tracks worker health and activity.

**Table:** `workercheckin`

**Fields:**
- `id` (str, primary key) - Worker identifier
- `first_checkin` (datetime) - When worker first registered
- `last_checkin` (datetime) - Most recent heartbeat

**Constraints:**
- Unique constraint on `id`

**Use Cases:**
- Monitor active workers
- Detect stale/crashed workers
- Worker load balancing

**Example:**
```json
{
  "id": "worker-abc-123",
  "first_checkin": "2025-01-15T10:00:00",
  "last_checkin": "2025-01-15T10:30:00"
}
```

---

## Enums

### RunStatus

Workflow and step status values.

```python
class RunStatus(str, Enum):
    PENDING = "PENDING"      # Not yet started
    RUNNING = "RUNNING"      # Currently executing
    COMPLETED = "COMPLETED"  # Finished successfully
    ERROR = "ERROR"          # Failed but still retrying
    FAILED = "FAILED"        # Permanently failed
```

### WorkflowStepType

Types of workflow steps.

```python
class WorkflowStepType(str, Enum):
    INGEST = "ingest"
    VALIDATE = "validate"
    PARSE = "parse"
    CHUNK = "chunk"
    EMBED = "embed"
    STORE = "store"
    ENRICH = "enrich"
    ROUTE = "route"
```

### ArtifactType

Types of stored artifacts.

```python
class ArtifactType(Enum):
    DOC = "document"
    PARSED_MD = "parsed_markdown"
    PARSED_JSON = "parsed_json"
    CHUNKS = "chunks"
    EMBEDDINGS = "embeddings"
    RAG = "rag"
```

### LifeCycleEvent

Workflow lifecycle events.

```python
class LifeCycleEvent(str, Enum):
    GROUP_START = "group_start"
    GROUP_END = "group_end"
    ITEM_START = "item_start"
    ITEM_END = "item_end"
    ITEM_FAILED = "item_failed"
    STEP_START = "step_start"
    STEP_END = "step_end"
    STEP_FAILED = "step_failed"
```

---

## Artifact Mapping

**Workflow Steps to Artifacts:**
- INGEST - DOC
- PARSE - PARSED_MD, PARSED_JSON
- CHUNK - CHUNKS
- EMBED - EMBEDDINGS
- STORE - RAG

---

## Relationships Diagram

```
DocumentBatch
    | (1:N)
DocumentURI --> Document (N:1)
    |
DocumentURIHistory

DocumentBatch
    | (1:N)
RunGroup
    | (1:N)
WorkflowRun --> Document (N:1)
    | (1:N)
RunStep --> StepConfig (N:1)

ConfigSet
    | (N:M via ConfigSetItem)
StepConfig

RunGroup --> LifecycleHistory (1:N)
WorkflowRun --> LifecycleHistory (1:N)
```

---

## Response Models

### DocumentInfo

API response model for document information.

```python
class DocumentInfo(BaseModel):
    uri: str | None = None
    source: str | None = None
    file_size: int | None = None
    mime_type: str | None = None
```

### WorkflowRunWithDetails

Response model for workflow run with optional steps and document info.

```python
class WorkflowRunWithDetails(BaseModel):
    workflow_run: WorkflowRun
    steps: list[RunStep] | None = None
    document_info: DocumentInfo | None = None
```

### PaginatedResponse

Generic paginated response model.

```python
class PaginatedResponse[T](BaseModel):
    items: list[T]
    total: int
    page: int
    rows_per_page: int
    total_pages: int
```

---

## Database Initialization

### Using CLI

```bash
si-cli db-init
```

This creates tables and runs migrations.

### Using Alembic Directly

```bash
alembic upgrade head
```

### Programmatic

```python
from soliplex.ingester.lib.models import Database

# Initialize with default URL from settings
await Database.initialize()

# Or with custom URL
await Database.initialize("sqlite+aiosqlite:///:memory:")
```

---

## Python Cascading Delete Functions

### delete_run_group

Cascading deletion function for run groups and all dependent records.

**Location:** `src/soliplex/ingester/lib/wf/operations.py`

**Signature:**
```python
async def delete_run_group(run_group_id: int) -> dict[str, int]
```

**Database Compatibility:**
- SQLite (via aiosqlite)
- PostgreSQL (via asyncpg)

**Behavior:**
1. Verifies the RunGroup exists (raises `NotFoundError` if not found)
2. Retrieves all WorkflowRun IDs for the RunGroup
3. Deletes all RunStep records for those WorkflowRuns
4. Deletes all LifecycleHistory records (for both RunGroup and WorkflowRuns)
5. Deletes all WorkflowRun records in the group
6. Deletes the RunGroup itself
7. Returns deletion statistics

All operations occur within a single database transaction to ensure atomicity.

**Usage:**
```python
from soliplex.ingester.lib.wf.operations import delete_run_group, NotFoundError

# Delete a run group and all dependent records
result = await delete_run_group(run_group_id=5)

print(f"Deleted {result['deleted_rungroups']} run group(s)")
print(f"Deleted {result['deleted_workflowruns']} workflow run(s)")
print(f"Deleted {result['deleted_runsteps']} run step(s)")
print(f"Deleted {result['deleted_lifecyclehistory']} lifecycle history record(s)")
print(f"Total records deleted: {result['total_deleted']}")
```

**Returns:**
```python
{
    "deleted_runsteps": 150,
    "deleted_lifecyclehistory": 45,
    "deleted_workflowruns": 10,
    "deleted_rungroups": 1,
    "total_deleted": 206
}
```

**Raises:**
- `NotFoundError` - If the RunGroup with the specified ID does not exist

---

### delete_document_uri_by_uri

Cascading deletion function for DocumentURI and all dependent records.

**Location:** `src/soliplex/ingester/lib/operations.py`

**Signature:**
```python
async def delete_document_uri_by_uri(uri: str, source: str) -> dict[str, int]
```

**Behavior:**
1. Finds the DocumentURI by `uri` and `source`
2. Counts how many DocumentURIs reference the same document hash
3. If only one URI references the document (cascade delete):
   - Deletes all RunStep records for WorkflowRuns with this doc_id
   - Deletes all LifecycleHistory records for those WorkflowRuns
   - Deletes all WorkflowRun records with this doc_id
   - Deletes all DocumentBytes artifacts for this hash
   - Deletes file artifacts from storage
   - Deletes the DocumentURIHistory records
   - Deletes the DocumentURI record
   - Deletes the Document record
4. If multiple URIs reference the document (preserve document):
   - Deletes only the DocumentURIHistory records for this URI
   - Deletes only the DocumentURI record
   - Preserves the Document and all workflow-related records

**Returns:**
```python
{
    "deleted_document_uris": 1,
    "deleted_uri_history": 3,
    "deleted_documents": 1,
    "deleted_workflow_runs": 2,
    "deleted_run_steps": 10,
    "deleted_lifecycle_history": 6,
    "total_deleted": 23
}
```

**Usage:**
```python
from soliplex.ingester.lib.operations import delete_document_uri_by_uri
from soliplex.ingester.lib.operations import DocumentURINotFoundError

try:
    stats = await delete_document_uri_by_uri(
        uri="/documents/report.pdf",
        source="filesystem"
    )
    print(f"Total deleted: {stats['total_deleted']}")
except DocumentURINotFoundError as e:
    print(f"Error: {e}")
```

**Notes:**
- All deletions occur within a single transaction
- Works with both SQLite and PostgreSQL
- Raises `DocumentURINotFoundError` if the URI/source combination does not exist
- Used by the `DELETE /api/v1/document/by-uri` endpoint

---

## Migrations

### Location
`src/soliplex/ingester/migrations/`

### Configuration
`alembic.ini` (project root)

### Create Migration
```bash
alembic revision --autogenerate -m "description"
```

### Apply Migration
```bash
alembic upgrade head
```

### Rollback
```bash
alembic downgrade -1
```

---

## Indexes

Consider adding these indexes for production:

```sql
-- Workflow processing queries
CREATE INDEX idx_runstep_status ON runstep(status, priority DESC);
CREATE INDEX idx_workflowrun_status ON workflowrun(status, batch_id);
CREATE INDEX idx_rungroup_batch ON rungroup(batch_id);

-- Document lookups
CREATE INDEX idx_documenturi_source ON documenturi(source);

-- Worker monitoring
CREATE INDEX idx_runstep_worker ON runstep(worker_id);
CREATE INDEX idx_workercheckin_last ON workercheckin(last_checkin);
```

---

## Backup and Maintenance

### SQLite Backup
```bash
sqlite3 db/documents.db ".backup backup.db"
```

### PostgreSQL Backup
```bash
pg_dump -h localhost -U user soliplex > backup.sql
```

### Vacuum (SQLite)
```bash
sqlite3 db/documents.db "VACUUM;"
```

### Analyze (PostgreSQL)
```bash
psql -h localhost -U user -d soliplex -c "ANALYZE;"
```

---

## Query Examples

### Find Failed Workflows
```python
from soliplex.ingester.lib.models import WorkflowRun, RunStatus, get_session
from sqlmodel import select

async with get_session() as session:
    query = select(WorkflowRun).where(WorkflowRun.status == RunStatus.FAILED)
    results = await session.exec(query)
    failed_runs = results.all()
```

### Get Batch Statistics
```python
from sqlmodel import func, select

async with get_session() as session:
    query = select(
        func.count(WorkflowRun.id).label("total"),
        WorkflowRun.status
    ).where(
        WorkflowRun.batch_id == batch_id
    ).group_by(WorkflowRun.status)

    results = await session.exec(query)
    stats = {row.status: row.total for row in results}
```

### Find Stale Workers
```python
from datetime import datetime, timedelta

cutoff = datetime.now() - timedelta(seconds=600)
query = select(WorkerCheckin).where(WorkerCheckin.last_checkin < cutoff)
stale_workers = await session.exec(query)
```
