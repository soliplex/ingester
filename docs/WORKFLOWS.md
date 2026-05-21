# Workflow System Documentation

## Overview

The Soliplex Ingester workflow system orchestrates multi-step document processing pipelines. Each document flows through a series of configurable steps, with automatic retry logic, status tracking, and parallel processing support.

## Core Concepts

### Workflow Definition

A **WorkflowDefinition** specifies the processing pipeline for documents. It defines:

- Unique ID and name
- Item steps (processing stages)
- Lifecycle event handlers
- Retry policies

Defined in YAML files at: `config/workflows/*.yaml`

### Workflow Run

A **WorkflowRun** represents a single execution of a workflow for one document. It tracks:
- Status (PENDING → RUNNING → COMPLETED/FAILED)
- Start and completion timestamps
- Priority level
- Associated document and batch

### Run Group

A **RunGroup** aggregates multiple workflow runs that were started together (e.g., all documents in a batch). It provides:
- Group-level status tracking
- Aggregate statistics
- Batch coordination

### Run Step

A **RunStep** is one stage of execution within a workflow run. Each step:
- Executes a specific handler method
- Has its own status and retry counter
- Produces artifacts stored in the file system
- Updates database on completion/failure

## Workflow Step Types

The system supports these predefined step types:

### INGEST

- **Purpose:** Load raw document into the system
- **Input:** File bytes or URI
- **Output:** Document stored in file system
- **Artifact:** `ArtifactType.DOC`

### VALIDATE

- **Purpose:** Check document format and readability
- **Input:** Raw document
- **Output:** Validation result
- **Handler:** `soliplex.ingester.lib.workflow.validate_document`

### PARSE

- **Purpose:** Extract text, structure, and metadata
- **Input:** Raw document
- **Output:** Markdown and JSON representations
- **Artifacts:** `ArtifactType.PARSED_MD`, `ArtifactType.PARSED_JSON`
- **Handler:** `soliplex.ingester.lib.workflow.parse_document`
- **Service:** Docling server

### CHUNK

- **Purpose:** Split document into semantic chunks
- **Input:** Parsed markdown
- **Output:** Array of text chunks
- **Artifact:** `ArtifactType.CHUNKS`
- **Handler:** `soliplex.ingester.lib.workflow.chunk_document`

### EMBED

- **Purpose:** Generate vector embeddings for chunks
- **Input:** Text chunks
- **Output:** Embedding vectors
- **Artifact:** `ArtifactType.EMBEDDINGS`
- **Handler:** `soliplex.ingester.lib.workflow.embed_document`

### STORE

- **Purpose:** Save embeddings to RAG system
- **Input:** Embeddings
- **Output:** RAG document ID
- **Artifact:** `ArtifactType.RAG`
- **Handler:** `soliplex.ingester.lib.workflow.save_to_rag`
- **Backend:** LanceDB + HaikuRAG

### ENRICH

- **Purpose:** Add metadata or perform additional processing
- **Input:** Document and existing artifacts
- **Output:** Enhanced metadata
- **Handler:** Custom (user-defined)

### ROUTE

- **Purpose:** Conditional logic for workflow branching
- **Input:** Document state
- **Output:** Routing decision
- **Handler:** Custom (user-defined)

## Workflow Configuration

### Workflow Definition YAML

Example: `config/workflows/batch.yaml`

```yaml
id: batch
name: Batch Workflow
meta: {}

item_steps:
  validate:
    name: docling validate
    retries: 3
    method: soliplex.ingester.lib.workflow.validate_document
    parameters: {}

  parse:
    name: docling parse
    retries: 3
    method: soliplex.ingester.lib.workflow.parse_document
    parameters: {}

  chunk:
    name: docling chunk
    retries: 3
    method: soliplex.ingester.lib.workflow.chunk_document
    parameters: {}

  embed:
    name: embeddings
    retries: 3
    method: soliplex.ingester.lib.workflow.embed_document
    parameters: {}

  store:
    name: save to rag
    retries: 3
    method: soliplex.ingester.lib.workflow.save_to_rag
    parameters: {}

lifecycle_events:
  group_start:
    - name: log group start
      method: soliplex.ingester.lib.workflow.log_event
      retries: 1
      parameters:
        message: "Starting workflow group"
```

### Parameter Set YAML

Parameters control step behavior without changing the workflow definition.

Example: `config/params/default.yaml`

```yaml
id: default
name: Default Parameters
meta:
  description: Standard processing parameters

config:
  parse:
    format: markdown
    ocr_enabled: true

  chunk:
    chunk_size: 512
    chunk_overlap: 50
    separator: "\n\n"

  embed:
    model: text-embedding-3-small
    batch_size: 1000

  store:
    data_dir: lancedb
    collection_name: documents
```

## Workflow Execution

### Creating Workflows

**For a Batch:**

```bash
curl -X POST "http://localhost:8000/api/v1/batch/start-workflows" \
  -d "batch_id=1" \
  -d "workflow_definition_id=batch" \
  -d "param_id=default"
```

**For a Single Document:**

```bash
curl -X POST "http://localhost:8000/api/v1/workflow/" \
  -d "doc_id=sha256-abc123..." \
  -d "workflow_definition_id=batch"
```

### Worker Processing

A `Worker` runs a set of typed consumer pools. Each consumer claims
work via an atomic SQL-layer claim:

1. Consumer mints a fresh UUID lease token
2. `claim_next_step(worker_id, lease_token, allowed_types=...)` runs
   an atomic `UPDATE-FROM-SELECT-FOR-UPDATE-SKIP-LOCKED`:
   - Eligibility filters: lowest unfinished step number per
     workflow run, status `PENDING` or `ERROR`, `retry < retries`,
     no sibling `RUNNING` step
   - Per-type filter: `step_type IN allowed_types` (each consumer
     pool only claims its types)
   - Resource-key filter: skip steps whose `resource_key` is
     currently held by a non-expired `ResourceLock` row
3. The claimed row is stamped `status=RUNNING`, `worker_id=…`,
   `lease_token=…` in the same transaction
4. If the step declared a `resource_key`, the worker acquires the
   `ResourceLock` row (heartbeat-refreshed)
5. Handler executes; artifacts are written to file storage
6. Terminal writes are lease-gated:
   - `complete_step(step_id, lease)` → COMPLETED if the lease still
     matches; deletes any `ResourceLock` held by that lease in the
     same transaction
   - `error_step(step_id, lease, message)` → increments retry,
     returns `ERROR` (retries remain) or `FAILED` (exhausted).
     `FAILED` cascades pending siblings to `CANCELLED`.
   - `release_step(step_id, lease)` → back to `PENDING` without
     incrementing retry (cooperative release used on graceful
     shutdown)
7. `recompute_run_status` derives the run's status from current
   step counts; `try_complete_run_group` atomically transitions
   the group and fires `GROUP_END` exactly once across workers

A reaped worker (checkin timestamp older than
`WORKER_CHECKIN_TIMEOUT`) has its `RUNNING` steps reset to
`PENDING` and its `lease_token` / resource locks cleared by another
worker's reaper loop. The reaper always skips the caller, so a
worker can never reap itself.

Start a worker:

```bash
si-cli worker
```

Or via server (the FastAPI lifespan constructs a `Worker` on
startup and calls `Worker.stop(timeout=30s)` on shutdown):

```bash
si-cli serve
```

### Consumer Pools

The server lifespan and `si-cli worker` build a default pool layout
from settings:

| Pool      | Concurrency                           | Source                         |
|-----------|---------------------------------------|--------------------------------|
| `parse`   | `DOCLING_CONCURRENCY` (default 3)     | parse steps only               |
| `store`   | `WORKER_TASK_COUNT` (default 5)       | save_to_rag / STORE steps only |
| `*`       | `WORKER_TASK_COUNT` (default 5)       | catch-all for other step types |

The `"*"` pool auto-excludes any step type explicitly pinned to a
named pool, so a parse step never gets picked up by the catch-all.
Custom layouts can be passed directly:

```python
from soliplex.ingester.lib.wf.runner import Worker, WorkerConfig

worker = Worker(WorkerConfig(consumers={
    "parse": 8,
    "store": 4,
    "*": 2,
}))
await worker.start()
# ... do work ...
await worker.stop(timeout=30.0)
```

Per-type concurrency is bounded at the database level — a `parse`
consumer only claims `parse` steps, so we never claim a step just
to release it because of a local rate limit. The module-level
HTTP semaphore that previously serialized Docling requests has
been removed; set `consumers["parse"]` to the desired Docling
concurrency instead.

### Graceful Shutdown

`Worker.stop(timeout=30.0)`:

1. Signals consumers to stop claiming new work
2. Waits up to *timeout* for in-flight steps to finish
3. Cancels any consumer still mid-step; the `CancelledError`
   handler calls `release_step` so the row comes back to `PENDING`
   immediately, gated on the lease so it can never bounce a
   fresh claimant
4. Drains lifecycle subscribers
5. Deletes the worker's `WorkerCheckin` row so siblings see the
   departure without waiting the full `WORKER_CHECKIN_TIMEOUT`

The FastAPI lifespan calls this on shutdown; `si-cli worker`
handles SIGINT/SIGTERM the same way. Restart-recovery latency
drops from `WORKER_CHECKIN_TIMEOUT` to the in-flight step's
remaining duration.

### Status Transitions

**Valid Transitions:**
- PENDING → RUNNING (claim)
- RUNNING → COMPLETED (success)
- RUNNING → ERROR (failure, retries remain)
- RUNNING → PENDING (cooperative release on shutdown)
- ERROR → RUNNING (retry claim)
- ERROR → FAILED (after max retries)
- PENDING → CANCELLED (cascaded by a sibling step's FAILED)

**Invalid Transitions:**
- COMPLETED → RUNNING (no re-running completed steps)
- FAILED → RUNNING (use the retry endpoint to reset to PENDING)
- A terminal write whose lease token no longer matches is silently
  ignored — the row is owned by a fresh claimant

## Lifecycle Events

Lifecycle events allow custom logic at key points:

### Event Types

- `GROUP_START` - Run group begins
- `GROUP_END` - Run group completes
- `ITEM_START` - Workflow run begins
- `ITEM_END` - Workflow run completes
- `ITEM_FAILED` - Workflow run fails
- `STEP_START` - Step begins
- `STEP_END` - Step completes
- `STEP_FAILED` - Step fails

### Event Handler Example

```yaml
lifecycle_events:
  item_failed:
    - name: notify on failure
      method: myapp.notifications.send_alert
      retries: 3
      parameters:
        channel: "#alerts"
        message: "Document processing failed"
```

### Example: Vacuum Workflow

The `batch_split_vacuum` workflow (`config/workflows/batch_split_vacuum.yaml`) demonstrates a practical use of lifecycle events. It extends the standard `batch_split` workflow by adding a `group_end` handler that vacuums and checksums the LanceDB database after all documents in a group have been processed.

```yaml
id: batch_split_vacuum
meta: {}
name: Batch workflow with splitting, vacuum and hash checking
lifecycle_events:
  group_end:
    - name: start span
      method: soliplex.ingester.example.run_end
      retries: 1
      parameters: {}
item_steps:
  validate:
    name: validate document
    retries: 3
    method: soliplex.ingester.lib.workflow.validate_document
    parameters: {}
  parse:
    name: docling parse
    retries: 3
    method: soliplex.ingester.lib.workflow.split_parse_document
    parameters: {}
  chunk:
    name: docling chunk
    retries: 3
    method: soliplex.ingester.lib.workflow.chunk_document
    parameters: {}
  embed:
    name: embeddings
    retries: 3
    method: soliplex.ingester.lib.workflow.embed_document
    parameters: {}
  store:
    name: save to rag
    retries: 3
    method: soliplex.ingester.lib.workflow.save_to_rag
    parameters: {}
```

The `run_end` handler (in `src/soliplex/ingester/example/__init__.py`) performs two operations:

1. **Vacuum** - Calls `HaikuRAGApp.vacuum()` to compact the LanceDB database, reclaiming disk space from deleted or updated records
2. **Hash** - Computes a SHA-256 hash of all database files and writes it to `{db_name}.sha256`, providing a way to detect if the database was modified outside the workflow (e.g., by CLI operations)

You can also vacuum a database on demand via the REST API:

```bash
curl "http://localhost:8000/api/v1/lancedb/vacuum?db=my_database"
```

**Note:** LanceDB `auto_vacuum` is explicitly disabled in the HaikuRAG storage configuration because it caused reliability issues. Use the vacuum lifecycle event or the API endpoint instead for controlled compaction.

All RAG-DB writers — workflow `save_to_rag` steps, the web vacuum
endpoint, the `si-diag` CLI, and `end_group` lifecycle vacuums —
coordinate via the cross-subsystem `ResourceLock` table. STORE-type
steps stamp a `resource_key` (e.g. `rag:/abs/path/to/db`) at run
creation; the claim layer skips the step while the lock is held, so
a `save_to_rag` step is never claimed while its DB is being
vacuumed.

Direct-from-Python writers (CLI vacuum, lifecycle vacuum) can use
the `hold_rag_lock` context manager:

```python
from soliplex.ingester.lib.rag import hold_rag_lock
from soliplex.ingester.lib.models import ResourceLockKind

async with hold_rag_lock(
    db_path,
    holder_kind=ResourceLockKind.LIFECYCLE,
    max_wait=60,  # seconds; None = wait forever; 0 = fail fast
):
    # do exclusive work on the DB
    ...
```

---

## Retry Logic

### Automatic Retries

Steps automatically retry on error:
- Configurable retry count per step
- Exponential backoff (implementation dependent)
- Status: ERROR during retries, FAILED when exhausted

### Manual Retry

Reset failed steps for a run group:

```bash
curl -X POST "http://localhost:8000/api/v1/workflow/retry" \
  -d "run_group_id=5"
```

This resets all FAILED steps to PENDING for re-processing.

## Monitoring

### Check Run Group Status

```bash
curl "http://localhost:8000/api/v1/workflow/run-groups/5/stats"
```

Returns:

```json
{
  "total_runs": 100,
  "completed": 95,
  "running": 3,
  "pending": 0,
  "failed": 2,
  "average_duration": 45.3,
  "group_status": "RUNNING"
}
```

### Check Specific Workflow Run

```bash
curl "http://localhost:8000/api/v1/workflow/runs/42"
```

Returns workflow run with all steps and their status.

### Query Steps by Status

```bash
curl "http://localhost:8000/api/v1/workflow/steps?status=FAILED"
```

Lists all failed steps for investigation.

## Custom Step Handlers

### Handler Signature

```python
async def custom_handler(
    run_step: RunStep,
    workflow_run: WorkflowRun,
    doc: Document,
    step_params: dict[str, Any]
) -> dict[str, Any]:
    """
    Custom workflow step handler.

    Args:
        run_step: Current step being executed
        workflow_run: Parent workflow run
        doc: Document being processed
        step_params: Parameters from config

    Returns:
        Dictionary with result metadata

    Raises:
        Exception: On failure (will trigger retry)
    """
    # Your processing logic here
    result_data = await process_document(doc)

    # Store artifacts if needed
    await store_artifact(
        doc.hash,
        ArtifactType.CUSTOM,
        result_data
    )

    return {"status": "success", "items_processed": 42}
```

### Registering Custom Handler

1. **Define the handler** in your Python module
2. **Add to workflow YAML:**

   ```yaml
   item_steps:
     custom_step:
       name: My Custom Step
       retries: 2
       method: myapp.handlers.custom_handler
       parameters:
         param1: value1
   ```

3. **Ensure module is importable** by the worker process

## Worker Configuration

### Environment Variables

- `DOCLING_CONCURRENCY` - Size of the dedicated `parse` consumer
  pool (default: 3). Replaces the previous in-process HTTP
  semaphore.
- `WORKER_TASK_COUNT` - Size of the `store` pool and the catch-all
  `"*"` pool (default: 5)
- `INGEST_WORKER_CONCURRENCY` - Legacy ceiling, retained for
  reference; current code derives pool sizes from
  `DOCLING_CONCURRENCY` and `WORKER_TASK_COUNT`
- `INGEST_QUEUE_CONCURRENCY` - Max concurrent queue operations
  (default: 20)
- `WORKER_CHECKIN_INTERVAL` - Heartbeat interval in seconds
  (default: 120). Also drives `ResourceLock` TTL refresh cadence.
- `WORKER_CHECKIN_TIMEOUT` - Worker timeout in seconds (default:
  600). The reaper considers a worker dead at this threshold.

### Metrics

The `Worker` accepts a `metrics: Metrics` argument implementing
the protocol `incr(name, value=1, **labels)` /
`observe(name, value, **labels)`. The default `LoggingMetrics`
emits at DEBUG; wire a Prometheus or OpenTelemetry adapter for
production:

| Counter                | When emitted                                            |
|------------------------|---------------------------------------------------------|
| `claim_attempts`       | Each claim attempt (labelled by `pool`)                 |
| `claim_success`        | A step was claimed                                      |
| `claim_idle`           | No work was available                                   |
| `claim_error`          | The claim query raised                                  |
| `step_completed`       | Successful terminal write                               |
| `step_error`           | Retryable failure                                       |
| `step_failed`          | Retries exhausted                                       |
| `step_released`        | Cooperative release on shutdown                         |
| `step_reset_by_reaper` | A dead worker's step was reset to PENDING               |
| `worker_reaped`        | A peer worker was reaped                                |
| `lease_lost`           | Terminal write found a non-matching lease (labelled `phase`) |

| Histogram         | Description                          |
|-------------------|--------------------------------------|
| `claim_duration`  | Wall time of `claim_next_step`       |
| `step_duration`   | Wall time of the full step handler   |

### Multiple Workers

Run multiple workers for increased throughput:

```bash
# Terminal 1
si-cli worker

# Terminal 2
si-cli worker

# Terminal 3
si-cli worker
```

Database-level coordination prevents duplicate processing:

- Atomic claim with `FOR UPDATE SKIP LOCKED` (PostgreSQL) or
  WAL-serialized writes (SQLite)
- Lease tokens ensure stale workers can't double-finalize
- `ResourceLock` serializes RAG-DB writers across workers and
  out-of-band callers (CLI, web)

## Artifact Storage

### Storage Paths

Artifacts are stored under `FILE_STORE_DIR` with subdirectories:

- `document_store_dir/` - Raw documents
- `parsed_markdown_store_dir/` - Parsed markdown
- `parsed_json_store_dir/` - Parsed JSON
- `chunks_store_dir/` - Text chunks
- `embeddings_store_dir/` - Embedding vectors

### File Naming

Files are named by document hash:

```text
{storage_dir}/{hash}
```

Example:

```text
file_store/markdown/sha256-abc123def456...
```

### Storage Backends

Configure via `FILE_STORE_TARGET`:
- `fs` - Local filesystem (default)
- `s3` - S3-compatible storage (via OpenDAL)
- Other OpenDAL-supported backends

## Best Practices

### Workflow Design

1. **Keep steps atomic** - Each step should do one thing well
2. **Make steps idempotent** - Re-running a step should be safe
3. **Use appropriate retries** - 3 retries for transient errors, 1 for validation
4. **Store intermediate results** - Save artifacts for debugging and recovery

### Error Handling

1. **Raise exceptions** for retriable errors
2. **Log context** before raising (worker ID, document hash, step)
3. **Use status_meta** to store error details
4. **Monitor failed steps** and investigate patterns

### Performance

1. **Tune concurrency** based on available resources
2. **Batch operations** where possible (embeddings)
3. **Use priorities** for urgent documents
4. **Monitor worker health** via heartbeat table

### Testing

1. **Test handlers independently** with mock data
2. **Use small batches** for integration testing
3. **Verify artifact storage** after each step
4. **Test retry logic** by simulating failures

## Troubleshooting

### Stuck Workflows

**Symptom:** Workflows remain in RUNNING status indefinitely

**Solution:**
1. Check worker logs for exceptions
2. Query stuck steps: `SELECT * FROM runstep WHERE status='RUNNING' AND start_date < NOW() - INTERVAL '1 hour'`
3. Check worker heartbeat: `SELECT * FROM workercheckin`
4. Restart workers if stale — `Worker.stop()` releases in-flight
   steps to PENDING immediately; the reaper handles ungraceful
   exits after `WORKER_CHECKIN_TIMEOUT`

### Steps Stuck Behind a Resource Lock

**Symptom:** STORE-type steps stay PENDING; a `ResourceLock` row
points at a holder that no longer exists.

**Solution:**
1. Inspect the lock: `SELECT * FROM resourcelock WHERE resource_key='rag:/path/to/db'`
2. Worker-held lock rows are cleared when the step completes,
   errors, is released, or the holder is reaped via
   `WorkerCheckin`. CLI/web/lifecycle holders use a real TTL and
   are cleared opportunistically by `acquire_resource_lock` or by
   `sweep_expired_resource_locks`. If the row is genuinely stuck,
   break it from the CLI:

   ```bash
   si-diag lancedb vacuum my_database --force
   ```

   `--force` calls `force_release_resource_lock` (audit-logged)
   before retrying.

### Failed Steps

**Symptom:** Steps transition to FAILED status

**Solution:**
1. Query step details: `GET /api/v1/workflow/steps?status=FAILED`
2. Check `status_message` and `status_meta` for error info
3. Fix underlying issue (service down, invalid config, etc.)
4. Retry: `POST /api/v1/workflow/retry`

### Slow Processing

**Symptom:** Low throughput, long durations

**Solution:**
1. Check Docling server response time
2. Increase `INGEST_WORKER_CONCURRENCY`
3. Run multiple workers
4. Verify database performance (add indexes if needed)
5. Check network latency to external services

### Duplicate Processing

**Symptom:** Same document processed multiple times

**Solution:**
1. Verify database locking is working (`FOR UPDATE`)
2. Check for unique constraint violations in logs
3. Ensure workers use distinct worker IDs
4. Verify step status transitions are atomic
