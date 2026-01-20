# Soliplex Ingester API Reference

## Base URL

All API endpoints are prefixed with `/api/v1/`.

## Authentication

Authentication is enforced when `API_KEY_ENABLED=true` or `AUTH_TRUST_PROXY_HEADERS=true` in environment settings. All endpoints require the `get_current_user` dependency.

---

## Document Endpoints

### GET /api/v1/document/

Get documents by source or batch ID.

**Query Parameters:**
- `source` (string, optional) - Source identifier to filter documents
- `batch_id` (integer, optional) - Batch ID to filter documents

**Response:**
- `200 OK` - Array of DocumentURI objects
- `400 Bad Request` - Neither source nor batch_id provided

**Example:**
```bash
curl "http://localhost:8000/api/v1/document/?batch_id=1"
```

---

### POST /api/v1/document/ingest-document

Ingest a new document into the system.

**Content-Type:** `multipart/form-data`

**Form Parameters:**
- `file` (file, optional) - Document file to upload
- `input_uri` (string, optional) - URI to fetch document from
- `mime_type` (string, optional) - MIME type of the document
- `source_uri` (string, required) - Source URI/path identifier
- `source` (string, required) - Source system identifier
- `batch_id` (integer, required) - Batch ID to assign document
- `doc_meta` (string, optional) - JSON string of metadata (default: `{}`)
- `priority` (integer, optional) - Processing priority (default: 0)

**Response:**
- `201 Created` - Document ingested successfully (new document)
- `203 Non-Authoritative Information` - Document already exists in a different batch
- `400 Bad Request` - Invalid parameters or metadata
- `500 Internal Server Error` - Processing error

**Success Response Body:**
```json
{
  "batch_id": 1,
  "document_uri": "/path/to/doc.pdf",
  "document_hash": "sha256-abc123...",
  "source": "filesystem",
  "uri_id": 42
}
```

**Notes:**
- The `batch_id` in the response reflects the batch where the document URI actually resides
- If a document with the same hash already exists in a different batch, the response returns `203` with the original batch ID
- This prevents duplicate processing while informing the caller that the document was previously ingested

**Example:**
```bash
curl -X POST "http://localhost:8000/api/v1/document/ingest-document" \
  -F "file=@document.pdf" \
  -F "source_uri=/documents/report.pdf" \
  -F "source=filesystem" \
  -F "batch_id=1" \
  -F "doc_meta={\"author\":\"John Doe\"}"
```

---

### POST /api/v1/document/cleanup-orphans

Delete orphaned documents with no URI references.

**Response:**
- `200 OK` - Cleanup successful with statistics
- `500 Internal Server Error` - Processing error

**Success Response Body:**
```json
{
  "message": "Orphaned documents cleaned up",
  "statistics": {
    "deleted_documents": 5,
    "deleted_history": 12
  }
}
```

**Example:**
```bash
curl -X POST "http://localhost:8000/api/v1/document/cleanup-orphans"
```

---

### DELETE /api/v1/document/by-uri

Delete a DocumentURI by URI and source with cascading deletion.

If only one DocumentURI references the underlying document, all associated records are deleted including workflow runs, steps, lifecycle history, artifacts, and the document itself.

If multiple DocumentURIs reference the same document, only the specified DocumentURI and its history are deleted; the document is preserved.

**Query Parameters:**
- `uri` (string, required) - The document URI to delete
- `source` (string, required) - The source system identifier

**Response:**
- `200 OK` - Deletion successful with statistics
- `404 Not Found` - DocumentURI not found
- `500 Internal Server Error` - Processing error

**Success Response Body:**
```json
{
  "message": "DocumentURI deleted successfully",
  "uri": "/documents/report.pdf",
  "source": "filesystem",
  "statistics": {
    "deleted_document_uris": 1,
    "deleted_uri_history": 3,
    "deleted_documents": 1,
    "deleted_workflow_runs": 2,
    "deleted_run_steps": 10,
    "deleted_lifecycle_history": 6,
    "total_deleted": 23
  }
}
```

**Notes:**
- When `deleted_documents` is 0, other DocumentURIs still reference the document
- All deletions occur within a single transaction for atomicity
- File artifacts are also deleted from configured storage (filesystem, S3, or database)

**Example:**
```bash
curl -X DELETE "http://localhost:8000/api/v1/document/by-uri?uri=/documents/report.pdf&source=filesystem"
```

---

## Batch Endpoints

### GET /api/v1/batch/

List all document batches.

**Response:**
- `200 OK` - Array of DocumentBatch objects

**Example:**
```bash
curl "http://localhost:8000/api/v1/batch/"
```

---

### POST /api/v1/batch/

Create a new document batch.

**Content-Type:** `application/x-www-form-urlencoded`

**Form Parameters:**
- `source` (string, required) - Source system identifier
- `name` (string, required) - Human-readable batch name

**Response:**
- `201 Created` - Batch created successfully

**Response Body:**
```json
{
  "batch_id": 1
}
```

**Example:**
```bash
curl -X POST "http://localhost:8000/api/v1/batch/" \
  -d "source=filesystem" \
  -d "name=Q4 Reports"
```

---

### POST /api/v1/batch/start-workflows

Start workflow processing for all documents in a batch.

**Content-Type:** `application/x-www-form-urlencoded`

**Form Parameters:**
- `batch_id` (integer, required) - Batch ID to process
- `workflow_definition_id` (string, optional) - Workflow to use (default: from config)
- `priority` (integer, optional) - Processing priority (default: 0)
- `param_id` (string, optional) - Parameter set ID (default: from config)

**Response:**
- `201 Created` - Workflows started successfully
- `404 Not Found` - Batch not found
- `500 Internal Server Error` - Processing error

**Response Body:**
```json
{
  "message": "Workflows started",
  "workflows": 10,
  "run_group": 5
}
```

**Example:**
```bash
curl -X POST "http://localhost:8000/api/v1/batch/start-workflows" \
  -d "batch_id=1" \
  -d "workflow_definition_id=batch" \
  -d "param_id=default"
```

---

### GET /api/v1/batch/status

Get detailed status for a batch.

**Query Parameters:**
- `batch_id` (integer, required) - Batch ID

**Response:**
- `200 OK` - Batch status details
- `404 Not Found` - Batch not found

**Response Body:**
```json
{
  "batch": {
    "id": 1,
    "name": "Q4 Reports",
    "source": "filesystem",
    "start_date": "2025-01-15T10:00:00",
    "completed_date": null
  },
  "document_count": 10,
  "workflow_count": {
    "COMPLETED": 7,
    "RUNNING": 2,
    "PENDING": 1
  },
  "workflows": [...],
  "parsed": 7,
  "remaining": 3
}
```

**Example:**
```bash
curl "http://localhost:8000/api/v1/batch/status?batch_id=1"
```

---

### GET /api/v1/batch/{batch_id}/steps

Get all workflow steps for a batch.

**Path Parameters:**
- `batch_id` (integer, required) - Batch ID

**Response:**
- `200 OK` - Array of RunStep objects
- `500 Internal Server Error` - Processing error

**Example:**
```bash
curl "http://localhost:8000/api/v1/batch/1/steps"
```

---

## Workflow Endpoints

### GET /api/v1/workflow/

Get workflow runs with optional pagination.

**Query Parameters:**
- `batch_id` (integer, optional) - Filter by batch ID
- `include_steps` (boolean, optional) - Include step details (default: false)
- `include_doc_info` (boolean, optional) - Include document info (default: false)
- `page` (integer, optional) - Page number (1-indexed)
- `rows_per_page` (integer, optional) - Results per page (default: 10 when paginated)

**Response:**
- `200 OK` - Array of WorkflowRun objects (unpaginated) or PaginatedResponse (paginated)

**Paginated Response Body:**
```json
{
  "items": [...],
  "total": 100,
  "page": 1,
  "rows_per_page": 10,
  "total_pages": 10
}
```

**Example:**
```bash
curl "http://localhost:8000/api/v1/workflow/?batch_id=1&page=1&rows_per_page=20"
```

---

### GET /api/v1/workflow/by-status

Get workflow runs filtered by status with optional pagination.

**Query Parameters:**
- `status` (enum, required) - One of: PENDING, RUNNING, COMPLETED, ERROR, FAILED
- `batch_id` (integer, optional) - Filter by batch ID
- `include_doc_info` (boolean, optional) - Include document info (default: false)
- `page` (integer, optional) - Page number (1-indexed)
- `rows_per_page` (integer, optional) - Results per page

**Response:**
- `200 OK` - Array of WorkflowRun objects or PaginatedResponse

**Example:**
```bash
curl "http://localhost:8000/api/v1/workflow/by-status?status=FAILED"
```

---

### GET /api/v1/workflow/definitions

List all available workflow definitions.

**Response:**
- `200 OK` - Array of workflow definition summaries

**Response Body:**
```json
[
  {
    "id": "batch",
    "name": "Batch Workflow"
  },
  {
    "id": "interactive",
    "name": "Interactive Workflow"
  }
]
```

**Example:**
```bash
curl "http://localhost:8000/api/v1/workflow/definitions"
```

---

### GET /api/v1/workflow/definitions/{workflow_id}

Get workflow definition YAML content by ID.

**Path Parameters:**
- `workflow_id` (string, required) - Workflow definition ID

**Response:**
- `200 OK` - YAML content (Content-Type: text/yaml)
- `404 Not Found` - Workflow definition not found

**Example:**
```bash
curl "http://localhost:8000/api/v1/workflow/definitions/batch"
```

---

### GET /api/v1/workflow/param-sets

List all available parameter sets.

**Response:**
- `200 OK` - Array of parameter set summaries

**Response Body:**
```json
[
  {
    "id": "default",
    "name": "Default Parameters",
    "source": "app"
  },
  {
    "id": "high_quality",
    "name": "High Quality Processing",
    "source": "user"
  }
]
```

**Example:**
```bash
curl "http://localhost:8000/api/v1/workflow/param-sets"
```

---

### GET /api/v1/workflow/param-sets/{set_id}

Get parameter set YAML content by ID.

**Path Parameters:**
- `set_id` (string, required) - Parameter set ID

**Response:**
- `200 OK` - YAML content (Content-Type: text/yaml)
- `404 Not Found` - Parameter set not found

**Example:**
```bash
curl "http://localhost:8000/api/v1/workflow/param-sets/default"
```

---

### GET /api/v1/workflow/param_sets/target/{target}

Get parameter sets that target a specific LanceDB directory.

**Path Parameters:**
- `target` (string, required) - LanceDB data directory path

**Response:**
- `200 OK` - Array of matching WorkflowParams objects

**Example:**
```bash
curl "http://localhost:8000/api/v1/workflow/param_sets/target/lancedb"
```

---

### POST /api/v1/workflow/param-sets

Upload a new parameter set from YAML content.

**Content-Type:** `application/x-www-form-urlencoded`

**Form Parameters:**
- `yaml_content` (string, required) - Raw YAML content

**Response:**
- `201 Created` - Parameter set created successfully
- `400 Bad Request` - Invalid YAML syntax or format
- `409 Conflict` - Parameter set with same ID already exists
- `500 Internal Server Error` - Processing error

**Success Response Body:**
```json
{
  "message": "Parameter set created successfully",
  "id": "my_params",
  "file_path": "/path/to/params/my_params.yaml"
}
```

**Notes:**
- Uploaded parameter sets have `source` set to "user"
- The parameter set ID is taken from the YAML content

**Example:**
```bash
curl -X POST "http://localhost:8000/api/v1/workflow/param-sets" \
  -d "yaml_content=id: my_params\nname: My Parameters\nconfig:\n  parse:\n    format: markdown"
```

---

### DELETE /api/v1/workflow/param-sets/{set_id}

Delete a user-uploaded parameter set.

**Path Parameters:**
- `set_id` (string, required) - Parameter set ID to delete

**Response:**
- `200 OK` - Parameter set deleted successfully
- `403 Forbidden` - Cannot delete built-in parameter sets
- `404 Not Found` - Parameter set not found
- `500 Internal Server Error` - Processing error

**Notes:**
- Only parameter sets with `source="user"` can be deleted
- Built-in parameter sets cannot be deleted via API

**Example:**
```bash
curl -X DELETE "http://localhost:8000/api/v1/workflow/param-sets/my_params"
```

---

### GET /api/v1/workflow/steps

Get workflow steps filtered by status.

**Query Parameters:**
- `status` (enum, required) - One of: PENDING, RUNNING, COMPLETED, ERROR, FAILED

**Response:**
- `200 OK` - Array of RunStep objects

**Example:**
```bash
curl "http://localhost:8000/api/v1/workflow/steps?status=RUNNING"
```

---

### GET /api/v1/workflow/run-groups

Get workflow run groups, optionally filtered by batch ID.

**Query Parameters:**
- `batch_id` (integer, optional) - Filter by batch ID

**Response:**
- `200 OK` - Array of RunGroup objects
- `500 Internal Server Error` - Processing error

**Example:**
```bash
curl "http://localhost:8000/api/v1/workflow/run-groups?batch_id=1"
```

---

### GET /api/v1/workflow/run_groups/{run_group_id}

Get specific run group by ID.

**Path Parameters:**
- `run_group_id` (integer, required) - Run group ID

**Response:**
- `200 OK` - RunGroup object
- `500 Internal Server Error` - Processing error

**Example:**
```bash
curl "http://localhost:8000/api/v1/workflow/run_groups/5"
```

---

### DELETE /api/v1/workflow/run_groups/{run_group_id}

Delete a run group and all dependent records.

**Path Parameters:**
- `run_group_id` (integer, required) - Run group ID to delete

**Response:**
- `200 OK` - Run group deleted successfully
- `404 Not Found` - Run group does not exist
- `500 Internal Server Error` - Processing error

**Response Body:**
```json
{
  "message": "RunGroup 5 deleted successfully",
  "statistics": {
    "deleted_runsteps": 150,
    "deleted_lifecyclehistory": 45,
    "deleted_workflowruns": 10,
    "deleted_rungroups": 1,
    "total_deleted": 206
  }
}
```

**Notes:**
- Works with both SQLite and PostgreSQL databases
- Deletes all dependent records: RunSteps, LifecycleHistory, WorkflowRuns, and the RunGroup
- The deletion is performed within a transaction and rolled back if any error occurs

**Example:**
```bash
curl -X DELETE "http://localhost:8000/api/v1/workflow/run_groups/5"
```

---

### GET /api/v1/workflow/run_groups/{run_group_id}/stats

Get statistics for a run group.

**Path Parameters:**
- `run_group_id` (integer, required) - Run group ID

**Response:**
- `200 OK` - Statistics object with status counts
- `500 Internal Server Error` - Processing error

**Example:**
```bash
curl "http://localhost:8000/api/v1/workflow/run_groups/5/stats"
```

---

### GET /api/v1/workflow/runs

Get workflow runs for a batch.

**Query Parameters:**
- `batch_id` (integer, required) - Batch ID

**Response:**
- `200 OK` - Array of WorkflowRun objects

**Example:**
```bash
curl "http://localhost:8000/api/v1/workflow/runs?batch_id=1"
```

---

### GET /api/v1/workflow/runs/{workflow_id}

Get specific workflow run by ID, including steps.

**Path Parameters:**
- `workflow_id` (integer, required) - Workflow run ID

**Response:**
- `200 OK` - WorkflowRun object with steps array

**Example:**
```bash
curl "http://localhost:8000/api/v1/workflow/runs/42"
```

---

### GET /api/v1/workflow/runs/{workflow_id}/lifecycle

Get lifecycle history events for a specific workflow run.

**Path Parameters:**
- `workflow_id` (integer, required) - Workflow run ID

**Response:**
- `200 OK` - Array of LifecycleHistory objects ordered by start_date
- `400 Bad Request` - Invalid workflow ID
- `500 Internal Server Error` - Processing error

**Response Body:**
```json
[
  {
    "id": 1,
    "event": "item_start",
    "handler_name": null,
    "run_group_id": 5,
    "workflow_run_id": 42,
    "step_id": null,
    "start_date": "2025-01-15T10:00:00",
    "completed_date": "2025-01-15T10:01:30",
    "status": "COMPLETED",
    "status_date": "2025-01-15T10:01:30",
    "status_message": "Item processing completed successfully",
    "status_meta": {}
  }
]
```

**Event Types:**
- `group_start` / `group_end` - Run group lifecycle
- `item_start` / `item_end` / `item_failed` - Item processing lifecycle
- `step_start` / `step_end` / `step_failed` - Individual step lifecycle

**Example:**
```bash
curl "http://localhost:8000/api/v1/workflow/runs/42/lifecycle"
```

---

### POST /api/v1/workflow/

Start a new workflow run for a single document.

**Content-Type:** `application/x-www-form-urlencoded`

**Form Parameters:**
- `doc_id` (string, required) - Document hash to process
- `workflow_definiton_id` (string, optional) - Workflow to use
- `param_id` (string, optional) - Parameter set ID
- `priority` (integer, optional) - Processing priority (default: 0)

**Response:**
- `201 Created` - Workflow run created
- `500 Internal Server Error` - Processing error

**Example:**
```bash
curl -X POST "http://localhost:8000/api/v1/workflow/" \
  -d "doc_id=sha256-abc123..." \
  -d "workflow_definiton_id=batch" \
  -d "priority=10"
```

---

### POST /api/v1/workflow/retry

Retry failed workflow steps for a run group.

**Content-Type:** `application/x-www-form-urlencoded`

**Form Parameters:**
- `run_group_id` (integer, required) - Run group ID to retry

**Response:**
- `201 Created` - Failed steps reset successfully
- `500 Internal Server Error` - Processing error

**Example:**
```bash
curl -X POST "http://localhost:8000/api/v1/workflow/retry" \
  -d "run_group_id=5"
```

---

## Source Status Endpoint

### POST /api/v1/source-status

Check document status for a source system.

**Content-Type:** `application/x-www-form-urlencoded`

**Form Parameters:**
- `source` (string, required) - Source system identifier
- `hashes` (string, required) - JSON object mapping URIs to hashes

**Response:**
- `200 OK` - Status object indicating new/changed/deleted documents

**Example:**
```bash
curl -X POST "http://localhost:8000/api/v1/source-status" \
  -d "source=filesystem" \
  -d 'hashes={"file1.pdf":"sha256-abc","file2.pdf":"sha256-def"}'
```

---

## Stats Endpoints

### GET /api/v1/stats/durations

Get workflow durations by run group.

**Query Parameters:**
- `run_group_id` (integer, required) - Run group ID

**Response:**
- `200 OK` - Duration statistics
- `500 Internal Server Error` - Processing error

**Example:**
```bash
curl "http://localhost:8000/api/v1/stats/durations?run_group_id=5"
```

---

### GET /api/v1/stats/step-stats

Get workflow step statistics by run group.

**Query Parameters:**
- `run_group_id` (integer, required) - Run group ID

**Response:**
- `200 OK` - Step statistics
- `500 Internal Server Error` - Processing error

**Example:**
```bash
curl "http://localhost:8000/api/v1/stats/step-stats?run_group_id=5"
```

---

## LanceDB Endpoints

### GET /api/v1/lancedb/list

List all LanceDB vector databases in the configured directory.

**Response:**
- `200 OK` - List of databases with metadata

**Response Body:**
```json
{
  "status": "ok",
  "lancedb_dir": "/data/lancedb",
  "database_count": 2,
  "databases": [
    {
      "name": "default",
      "path": "default",
      "size_bytes": 1048576,
      "size_human": "1.00 MB"
    }
  ]
}
```

**Example:**
```bash
curl "http://localhost:8000/api/v1/lancedb/list"
```

---

### GET /api/v1/lancedb/info

Get detailed information about a specific LanceDB database.

**Query Parameters:**
- `db` (string, required) - Database name relative to lancedb_dir

**Response:**
- `200 OK` - Database information
- `404 Not Found` - Database does not exist
- `500 Internal Server Error` - Failed to open database

**Response Body:**
```json
{
  "status": "ok",
  "path": "/data/lancedb/default",
  "versions": {
    "lancedb": "0.25.3",
    "haiku_rag": "0.25.0",
    "stored_version": "0.25.0"
  },
  "embeddings": {
    "provider": "openai",
    "model": "text-embedding-3-small",
    "vector_dim": 1536
  },
  "documents": {
    "count": 100,
    "size_bytes": 512000,
    "size_human": "500.00 KB",
    "versions": 5
  },
  "chunks": {
    "count": 1500,
    "size_bytes": 2048000,
    "size_human": "2.00 MB",
    "versions": 5
  },
  "vector_index": {
    "exists": true,
    "indexed_rows": 1450,
    "unindexed_rows": 50
  },
  "tables": ["documents", "chunks", "settings"]
}
```

**Example:**
```bash
curl "http://localhost:8000/api/v1/lancedb/info?db=default"
```

**Note:** The `db` parameter supports nested paths (e.g., `project/data`).

---

### GET /api/v1/lancedb/vacuum

Optimize and clean up database tables to reduce disk usage.

**Query Parameters:**
- `db` (string, required) - Database name relative to lancedb_dir

**Response:**
- `200 OK` - Vacuum completed successfully
- `500 Internal Server Error` - Vacuum failed

**Response Body:**
```json
{
  "status": "ok"
}
```

**Example:**
```bash
curl "http://localhost:8000/api/v1/lancedb/vacuum?db=default"
```

**Note:** Vacuum removes deleted rows and compacts table files. Run periodically after bulk deletions.

---

### GET /api/v1/lancedb/documents

List documents stored in a LanceDB database.

**Query Parameters:**
- `db` (string, required) - Database name relative to lancedb_dir
- `limit` (integer, optional) - Maximum number of documents to return
- `offset` (integer, optional) - Number of documents to skip
- `filter` (string, optional) - SQL WHERE clause to filter documents

**Response:**
- `200 OK` - List of documents
- `404 Not Found` - Database does not exist
- `500 Internal Server Error` - Query error

**Response Body:**
```json
{
  "status": "ok",
  "path": "/data/lancedb/default",
  "document_count": 10,
  "documents": [
    {
      "id": "doc-abc123",
      "uri": "/documents/report.pdf",
      "title": "Q4 Financial Report",
      "created_at": "2025-01-15T10:00:00",
      "updated_at": "2025-01-15T12:00:00",
      "chunk_count": 25,
      "metadata": {"author": "John Doe"}
    }
  ]
}
```

**Example:**
```bash
curl "http://localhost:8000/api/v1/lancedb/documents?db=default&limit=10"
```

**Example with filter:**
```bash
curl "http://localhost:8000/api/v1/lancedb/documents?db=default&filter=uri%20LIKE%20'%25report%25'"
```

---

## Data Models

### DocumentBatch
```json
{
  "id": 1,
  "name": "Q4 Reports",
  "source": "filesystem",
  "start_date": "2025-01-15T10:00:00",
  "completed_date": null,
  "batch_params": {},
  "duration": null
}
```

### Document
```json
{
  "hash": "sha256-abc123...",
  "mime_type": "application/pdf",
  "file_size": 1024000,
  "doc_meta": {"author": "John Doe"}
}
```

### DocumentURI
```json
{
  "id": 42,
  "doc_hash": "sha256-abc123...",
  "uri": "/documents/report.pdf",
  "source": "filesystem",
  "version": 1,
  "batch_id": 1
}
```

### WorkflowRun
```json
{
  "id": 100,
  "workflow_definition_id": "batch",
  "run_group_id": 5,
  "batch_id": 1,
  "doc_id": "sha256-abc123...",
  "priority": 0,
  "created_date": "2025-01-15T10:00:00",
  "start_date": "2025-01-15T10:01:00",
  "completed_date": null,
  "status": "RUNNING",
  "status_date": "2025-01-15T10:05:00",
  "status_message": null,
  "status_meta": {},
  "run_params": {},
  "duration": null
}
```

### RunStep
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
  "status_message": null,
  "status_meta": {},
  "worker_id": "worker-abc-123",
  "duration": null
}
```

### RunGroup
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

### LifecycleHistory
```json
{
  "id": 1,
  "event": "item_start",
  "handler_name": null,
  "run_group_id": 5,
  "workflow_run_id": 42,
  "step_id": null,
  "start_date": "2025-01-15T10:00:00",
  "completed_date": "2025-01-15T10:01:30",
  "status": "COMPLETED",
  "status_date": "2025-01-15T10:01:30",
  "status_message": null,
  "status_meta": {}
}
```

### RunStatus Enum
- `PENDING` - Not yet started
- `RUNNING` - Currently executing
- `COMPLETED` - Finished successfully
- `ERROR` - Failed but will retry
- `FAILED` - Permanently failed after all retries

### WorkflowStepType Enum
- `ingest` - Load document
- `validate` - Validate document
- `parse` - Extract text/structure
- `chunk` - Split into chunks
- `embed` - Generate embeddings
- `store` - Save to RAG system
- `enrich` - Add metadata
- `route` - Conditional routing

---

## Error Responses

All error responses follow this format:

```json
{
  "error": "Error message describing what went wrong",
  "status_code": 400
}
```

Common HTTP status codes:
- `400 Bad Request` - Invalid parameters
- `403 Forbidden` - Permission denied
- `404 Not Found` - Resource not found
- `409 Conflict` - Duplicate resource
- `500 Internal Server Error` - Server-side error

---

## OpenAPI/Swagger Documentation

Interactive API documentation is available at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
