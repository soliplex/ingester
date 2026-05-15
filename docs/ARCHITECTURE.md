# Soliplex Ingester Architecture

## Overview

Soliplex Ingester is a document processing and RAG (Retrieval-Augmented Generation) ingestion system designed to handle large-scale document workflows. It provides a FastAPI-based REST API, workflow orchestration, and integration with document parsing and embedding services.

## System Components

### 1. FastAPI Server

The server provides REST API endpoints for document and workflow management:

- **Document Routes** (`/api/v1/document/*`) - Document upload, retrieval, and management
- **Batch Routes** (`/api/v1/batch/*`) - Batch processing operations
- **Workflow Routes** (`/api/v1/workflow/*`) - Workflow execution and monitoring
- **Stats Routes** (`/api/v1/stats/*`) - System statistics and metrics

Server entry point: `src/soliplex/ingester/server/__init__.py:30`

### 2. Workflow System

The workflow system orchestrates multi-step document processing pipelines:

```text
Document → Validate → Parse → Chunk → Embed → Store
```

**Workflow Components:**

- **WorkflowDefinition** - Defines the steps and lifecycle events for a workflow
- **WorkflowRun** - Represents a single execution instance for one document
- **RunGroup** - Groups multiple workflow runs together
- **RunStep** - Individual step execution within a workflow run

**Step Types:**

- `INGEST` - Load document into system
- `VALIDATE` - Validate document format and content
- `PARSE` - Extract text and structure from document
- `CHUNK` - Split document into semantic chunks
- `EMBED` - Generate vector embeddings
- `STORE` - Save to RAG system (LanceDB + HaikuRAG)
- `ENRICH` - Add metadata or additional processing
- `ROUTE` - Conditional routing logic

Implementation: `src/soliplex/ingester/lib/wf/`

### 3. Worker System

Workflow execution is owned by a class-based orchestrator (`Worker`)
that runs a set of typed consumer pools backed by an atomic
claim-with-lease persistence layer:

- **Typed consumer pools** — `Worker(WorkerConfig(consumers={"parse": 4,
  "store": 8, "*": 2}))`. Each pool calls `operations.claim_next_step`
  with an `allowed_types` filter, so per-step-type concurrency is
  bounded at the database level instead of an in-process semaphore.
  The `"*"` pool is a catch-all for any step type not explicitly
  pinned.
- **Atomic claim + lease tokens** — every claim is an
  `UPDATE-FROM-SELECT-FOR-UPDATE-SKIP-LOCKED` that stamps the row
  with a UUID lease. `complete_step` / `error_step` / `release_step`
  are gated on a matching lease, so a worker reaped between claim
  and write can never bounce a fresh claimant.
- **`ResourceLock` rendezvous** — STORE-type steps stamp a
  `resource_key` (typically `rag:<abs-db-path>`) at run-creation time.
  The claim layer skips a step whose key is currently held, and the
  worker acquires the lock for the duration of execution. The same
  table is the rendezvous point for the web vacuum endpoint, the
  `si-diag` CLI, and `end_group` lifecycle vacuums.
- **Graceful shutdown** — `Worker.stop(timeout)` signals consumers to
  stop claiming, waits for in-flight work, then cancels remaining
  consumers. Cancelled consumers call `release_step` so the row
  returns to PENDING immediately rather than waiting for the
  worker-checkin timeout. The worker also deletes its own checkin
  row on shutdown.
- **Event-driven lifecycle bus** — `LifecycleBus` fires hooks on
  fire-and-forget tasks; a slow `STEP_START` handler can never
  block step execution. `GROUP_END` is emitted by a coordinator
  that subscribes to step-end events and consults
  `operations.try_complete_run_group(group_id)` for exactly-once
  semantics across N workers.
- **Self-skipping reaper** — `reap_dead_workers(my_id, threshold)`
  always excludes the caller, eliminating the self-reaping race
  where a stalled checkin loop would cause a worker to reset its
  own in-flight steps. Reaped workers' resource locks are cleared
  alongside.
- **Pluggable metrics** — `Metrics` protocol with a default
  `LoggingMetrics` no-op. Counters: `claim_attempts`,
  `claim_success`, `claim_idle`, `claim_error`, `claim_lost_race`,
  `step_completed`, `step_error`, `step_failed`, `step_released`,
  `worker_reaped`, `lease_lost`, `resource_lock_swept`. Histograms:
  `claim_duration`, `step_duration`.

Two workers can coexist in one process because nothing in the runner
is module-global anymore; legacy module-level `start_worker` /
`stop_worker` / `get_worker_id` shims are preserved for the few
existing callers but new code should construct a `Worker` directly.

Worker implementation: `src/soliplex/ingester/lib/wf/runner.py`
Persistence seam: `src/soliplex/ingester/lib/wf/operations.py`

### 4. Storage Layer

**Database:**

- SQLModel + SQLAlchemy with async support
- Supports SQLite (dev) and PostgreSQL (production)
- Alembic for migrations

**File Storage:**

- Configurable backends (filesystem, S3-compatible via OpenDAL)
- Separate storage locations for different artifact types:
  - Raw documents
  - Parsed markdown
  - Parsed JSON
  - Chunks
  - Embeddings

**Vector Storage:**

- LanceDB for vector embeddings
- HaikuRAG client for retrieval operations

### 5. Document Processing Pipeline

```mermaid
graph LR
    A[Upload Document] --> B[Create DocumentURI]
    B --> C[Hash & Store as Document]
    C --> D[Queue Workflow Run]
    D --> E[Validate Step]
    E --> F[Parse with Docling]
    F --> G[Chunk Text]
    G --> H[Generate Embeddings]
    H --> I[Store in LanceDB]
    I --> J[Update RAG Index]
```

### 6. External Services

**Docling Server:**

- Document parsing service
- Extracts text, structure, and metadata
- Configurable via `DOCLING_SERVER_URL`

**HaikuRAG:**

- RAG backend for document retrieval
- Vector search and document management
- Optional (controlled by `DO_RAG` setting)

## Data Flow

### Document Ingestion Flow

1. **Upload** - Client uploads document via `/api/v1/document/upload`
2. **Hash & Dedupe** - System computes SHA256 hash, checks for duplicates
3. **Create URI** - Maps source URI to document hash
4. **Batch Assignment** - Associates document with processing batch
5. **Workflow Creation** - Creates WorkflowRun and RunSteps
6. **Worker Processing** - Workers pick up and execute steps
7. **Status Updates** - Database tracks step and run status
8. **Completion** - Document marked complete when all steps succeed

### Workflow Execution Flow

1. **Worker Startup** - `Worker.start()` spawns per-type consumer
   loops plus heartbeat / reaper / lock-sweeper background tasks
2. **Step Claim** - Consumer calls `claim_next_step` with a fresh
   lease token; atomic `UPDATE-FROM-SELECT-FOR-UPDATE-SKIP-LOCKED`
   marks the row RUNNING. Steps whose `resource_key` is currently
   locked are skipped at the SQL layer
3. **Resource Lock Acquire** - If the step declared a
   `resource_key`, the worker acquires the matching `ResourceLock`
   row (TTL-refreshed on heartbeat)
4. **Status Transition** - PENDING → RUNNING → COMPLETED / ERROR /
   FAILED; terminal writes are gated on the lease so a stale
   worker cannot double-finalize
5. **Step Execution** - Calls registered handler method
6. **Artifact Storage** - Saves intermediate results
7. **Retry Logic** - `error_step` increments retry; elevates to
   FAILED when `retry >= retries` and cascades pending siblings to
   CANCELLED
8. **Run Completion** - `recompute_run_status` derives the run
   status from current step counts (idempotent)
9. **Group Completion** - `try_complete_run_group` is an atomic
   conditional update; the worker whose update affected a row fires
   `GROUP_END` exactly once

## Configuration

Configuration via environment variables with `pydantic-settings`:

- Database connection
- File storage paths
- Worker concurrency settings
- External service URLs
- Workflow and parameter directories

See `src/soliplex/ingester/lib/config.py:15` for full configuration schema.

## Scalability

**Horizontal Scaling:**

- Multiple workers can run concurrently
- Database row-level locking prevents duplicate processing
- Stateless API servers can be load balanced

**Vertical Scaling:**

- Configurable concurrency per worker
- Batch size controls for embedding operations
- Connection pooling for database access

**Workflow Parallelism:**

- Multiple workflows can process simultaneously
- Steps within a workflow run sequentially
- Different documents process independently

## Technology Stack

- **Web Framework:** FastAPI 0.120+
- **Database ORM:** SQLModel 0.0.27+
- **Async Runtime:** asyncio
- **CLI:** Typer
- **Document Parsing:** Docling
- **Vector DB:** LanceDB 0.25+
- **RAG:** HaikuRAG
- **Storage:** OpenDAL (multi-backend support)

## Extension Points

**Custom Workflow Steps:**
Define custom step handlers by:

1. Creating a new async function matching the EventHandler signature
2. Registering in workflow YAML configuration
3. Implementing retry logic and error handling

**Custom Storage Backends:**
Configure via `FILE_STORE_TARGET` environment variable and OpenDAL configuration.

**Custom Lifecycle Events:**
Add event handlers in workflow configuration to respond to:

- `GROUP_START` / `GROUP_END`
- `ITEM_START` / `ITEM_END`
- `STEP_START` / `STEP_END`
- `ITEM_FAILED` / `STEP_FAILED`

## Monitoring

**Database Tables:**

- `workflowrun` - Track run status and duration
- `runstep` - Monitor individual step execution; carries
  `lease_token` and `resource_key` columns used by the claim layer
- `workercheckin` - Worker health and activity
- `lifecyclehistory` - Audit trail of events
- `resourcelock` - Cross-subsystem lock rendezvous for RAG-DB
  writers (workflow, web vacuum, CLI vacuum, lifecycle vacuum)

**Metrics Available:**

- Document processing throughput
- Step success/failure rates
- Worker utilization
- Processing durations
- Batch completion times

Access via `/api/v1/stats/*` endpoints.
