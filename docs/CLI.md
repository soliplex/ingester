# CLI Reference

## Overview

Soliplex Ingester provides two command-line interfaces built with Typer:

- **`si-cli`** - Main CLI for server management, worker processes, and database operations
- **`si-diag`** - Read-only diagnostic CLI for inspecting batches, documents, workflows, and system status

**Installation:**

After installing the package, both commands are available:

```bash
pip install -e .
si-cli --help
si-diag --help
```

**Entry Points:**

- `si-cli` → `src/soliplex/ingester/cli.py`
- `si-diag` → `src/soliplex/ingester/diag_cli.py`

---

## Global Options

All commands support these options:

```bash
si-cli --help           # Show help for all commands
si-cli COMMAND --help   # Show help for specific command
```

**Initialization:**

Before running any command, the CLI automatically:

1. Validates settings
2. Sets up logging based on `LOG_LEVEL`

---

## Commands

### validate-settings

Validate and display application settings.

**Usage:**

```bash
si-cli validate-settings
```

**Description:**

- Validates all environment variables
- Displays current configuration
- Exits with error code if validation fails

**Example Output:**

```text
doc_db_url='sqlite+aiosqlite:///./db/documents.db'
docling_server_url='http://localhost:5001/v1'
docling_http_timeout=600
log_level='INFO'
file_store_target='fs'
file_store_dir='file_store'
...
```

**Error Output:**

```text
invalid settings
{'type': 'missing', 'loc': ('doc_db_url',), 'msg': 'Field required'}
```

**Exit Codes:**

- `0` - Settings valid
- `1` - Validation failed

**Implementation:** `src/soliplex/ingester/cli.py:38`

---

### db-init

Initialize database tables and run migrations.

**Usage:**

```bash
si-cli db-init
```

**Description:**

- Creates all database tables using SQLModel metadata
- Runs Alembic migrations to latest version
- Idempotent (safe to run multiple times)

**Prerequisites:**

- `DOC_DB_URL` environment variable must be set
- Database server must be accessible
- User must have CREATE TABLE permissions

**Example:**

```bash
export DOC_DB_URL="sqlite+aiosqlite:///./db/documents.db"
si-cli db-init
```

**Notes:**

- For SQLite, creates database file if it doesn't exist
- For PostgreSQL, database must already exist
- Uses synchronous SQLAlchemy engine (not async)

**Implementation:** `src/soliplex/ingester/cli.py:68`

---

### serve

Start the FastAPI web server.

**Usage:**

```bash
si-cli serve [OPTIONS]
```

**Options:**

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--host` | `-h` | str | `127.0.0.1` | Bind address |
| `--port` | `-p` | int | `8000` | Port number |
| `--uds` | - | str | None | Unix domain socket path |
| `--fd` | - | int | None | File descriptor to bind |
| `--reload` | `-r` | bool | False | Auto-reload on file changes |
| `--workers` | - | int | None | Number of worker processes |
| `--access-log` | - | bool | None | Enable/disable access log |
| `--proxy-headers` | - | bool | None | Trust proxy headers |
| `--forwarded-allow-ips` | - | str | None | IPs to trust for proxy headers |

**Examples:**

**Basic server:**

```bash
si-cli serve
```

**Custom host and port:**

```bash
si-cli serve --host 0.0.0.0 --port 9000
```

**Development mode with auto-reload:**

```bash
si-cli serve --reload
```

**Production with multiple workers:**

```bash
si-cli serve --workers 4 --host 0.0.0.0
```

**Unix socket:**

```bash
si-cli serve --uds /tmp/soliplex.sock
```

**Behind proxy:**

```bash
si-cli serve --proxy-headers --forwarded-allow-ips "10.0.0.0/8"
```

**Reload Configuration:**

When `--reload` is enabled:

- Watches Python files in `soliplex.ingester` package
- Watches `*.yaml`, `*.yml`, `*.txt` files
- Automatically restarts on changes

**Worker Note:**

The server automatically starts a background worker on startup. The worker processes workflow steps concurrently with serving API requests.

**Environment Variables:**

- `WEB_CONCURRENCY` - Default number of workers if not specified

**Implementation:** `src/soliplex/ingester/cli.py:207`

---

### worker

Run a standalone workflow processing worker.

**Usage:**

```bash
si-cli worker
```

**Description:**

- Starts a worker that polls for pending workflow steps
- Executes steps according to workflow definitions
- Runs indefinitely until interrupted (Ctrl+C)

**Behavior:**

- Registers worker with unique ID in database
- Sends heartbeat every `WORKER_CHECKIN_INTERVAL` seconds
- Processes steps based on priority and availability
- Handles retries according to step configuration

**Example:**

```bash
si-cli worker
```

**Multiple Workers:**

Run multiple instances for increased throughput:

```bash
# Terminal 1
si-cli worker

# Terminal 2
si-cli worker

# Terminal 3
si-cli worker
```

**Graceful Shutdown:**

- Press `Ctrl+C` to stop worker
- Worker will finish current step before exiting
- Pending steps remain in database for other workers

**Monitoring:**

Check worker status via API:

```bash
curl http://localhost:8000/api/v1/workflow/steps?status=RUNNING
```

**Implementation:** `src/soliplex/ingester/cli.py`

---

### list-workflows

List all available workflow definitions.

**Usage:**

```bash
si-cli list-workflows
```

**Description:**

- Scans `WORKFLOW_DIR` for YAML files
- Displays workflow IDs

**Example:**

```bash
si-cli list-workflows
```

**Output:**

```text
batch
batch_split
interactive
```

**Implementation:** `src/soliplex/ingester/cli.py:189`

---

### dump-workflow

Display complete workflow definition.

**Usage:**

```bash
si-cli dump-workflow WORKFLOW_ID
```

**Arguments:**

- `WORKFLOW_ID` (str, required) - Workflow definition ID

**Description:**

- Loads workflow from YAML
- Displays as formatted JSON

**Example:**

```bash
si-cli dump-workflow batch
```

**Output:**

```json
{
  "id": "batch",
  "name": "Batch Workflow",
  "meta": {},
  "item_steps": {
    "validate": {
      "name": "docling validate",
      "retries": 3,
      "method": "soliplex.ingester.lib.workflow.validate_document",
      "parameters": {}
    },
    ...
  },
  "lifecycle_events": null
}
```

**Implementation:** `src/soliplex/ingester/cli.py:162`

---

### list-param-sets

List all available parameter sets.

**Usage:**

```bash
si-cli list-param-sets
```

**Description:**

- Scans `PARAM_DIR` for YAML files
- Displays parameter set IDs

**Example:**

```bash
si-cli list-param-sets
```

**Output:**

```text
default
high_quality
fast_processing
```

**Implementation:** `src/soliplex/ingester/cli.py:201`

---

### dump-param-set

Display complete parameter set configuration.

**Usage:**

```bash
si-cli dump-param-set [PARAM_ID]
```

**Arguments:**

- `PARAM_ID` (str, optional) - Parameter set ID (default: "default")

**Description:**

- Loads parameter set from YAML
- Displays as formatted JSON

**Example:**

```bash
si-cli dump-param-set default
```

**Output:**

```json
{
  "id": "default",
  "name": "Default Parameters",
  "meta": {},
  "config": {
    "parse": {
      "format": "markdown",
      "ocr_enabled": true
    },
    "chunk": {
      "chunk_size": 512,
      "chunk_overlap": 50
    },
    ...
  }
}
```

**Implementation:** `src/soliplex/ingester/cli.py:175`

---

### vacuum

Vacuum a LanceDB database to reclaim space from deleted rows.

**Usage:**

```bash
si-cli vacuum DB_NAME [OPTIONS]
```

**Arguments:**

- `DB_NAME` (str, required) - Name of the database directory under `LANCEDB_DIR`

**Options:**

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--sign` | bool | False | Write an HMAC-SHA512 signature after vacuuming (requires `LANCEDB_HMAC_KEY`) |

**Description:**

- Resolves the database path under the configured `LANCEDB_DIR`
- If the directory contains a `haiku.rag.lancedb` subfolder, vacuums that instead
- Automatically runs pending migrations before vacuuming if required
- Sets `vacuum_retention_seconds` to 0 to ensure all deleted data is reclaimed
- Will not create a new database — errors if the path does not exist
- Holds the cross-subsystem `ResourceLock` (holder_kind=`cli`)
  for the duration of the operation. Workflow `save_to_rag` steps
  for the same DB are blocked from claim until the lock is
  released, so the vacuum cannot race a writer.
- Default behaviour waits forever for the lock; if you need fail-fast
  semantics, prefer `si-diag lancedb vacuum` which uses `max_wait=0`
  and offers `--force`.

**Examples:**

```bash
si-cli vacuum my_database
si-cli vacuum my_database --sign
```

**Exit Codes:**

- `0` - Vacuum completed successfully
- `1` - Database not found or error during vacuum

**Implementation:** `src/soliplex/ingester/cli.py` → `src/soliplex/ingester/lib/rag.py:vacuum_db`

---

### vacuum-all

Vacuum every LanceDB database under the configured `LANCEDB_DIR`.

**Usage:**

```bash
si-cli vacuum-all [OPTIONS]
```

**Options:**

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--sign` | bool | False | Write an HMAC-SHA512 signature after vacuuming each database (requires `LANCEDB_HMAC_KEY`) |

**Description:**

- Scans `LANCEDB_DIR` for all database directories
- Vacuums each one sequentially
- Logs and continues on failure so one bad database does not block the rest

**Examples:**

```bash
si-cli vacuum-all
si-cli vacuum-all --sign
```

**Implementation:** `src/soliplex/ingester/cli.py` → `src/soliplex/ingester/lib/rag.py:vacuum_all`

---

### verify-db

Verify the HMAC-SHA512 signature of a LanceDB database.

**Usage:**

```bash
si-cli verify-db DB_NAME
```

**Arguments:**

- `DB_NAME` (str, required) - Name of the database directory under `LANCEDB_DIR`

**Description:**

- Reads the `.hmac` sidecar file next to the database directory
- Recomputes the HMAC-SHA512 over all files in the database
- Uses constant-time comparison to verify the signature
- Requires `LANCEDB_HMAC_KEY` environment variable (must be 64 bytes)

**Examples:**

```bash
si-cli verify-db my_database
```

**Exit Codes:**

- `0` - HMAC verification passed
- `1` - Verification failed, HMAC file not found, or key misconfigured

**Implementation:** `src/soliplex/ingester/cli.py` → `src/soliplex/ingester/lib/rag.py:verify_db`

---

## Usage Patterns

### Development Workflow

**1. Validate configuration:**

```bash
si-cli validate-settings
```

**2. Initialize database:**

```bash
si-cli db-init
```

**3. Start server with reload:**

```bash
si-cli serve --reload
```

**4. (Optional) Start additional workers:**

```bash
si-cli worker
```

---

### Production Deployment

**1. Validate configuration:**

```bash
si-cli validate-settings
```

**2. Run migrations:**

```bash
si-cli db-init
```

**3. Start server with multiple workers:**

```bash
si-cli serve --host 0.0.0.0 --port 8000 --workers 4
```

**4. Start dedicated worker processes:**

```bash
# In separate terminals/services
si-cli worker  # Process 1
si-cli worker  # Process 2
si-cli worker  # Process 3
```

---

### Batch Processing

**1. Create batch and ingest documents:**

```bash
# Use API or client library
curl -X POST http://localhost:8000/api/v1/batch/ \
  -d "source=filesystem" \
  -d "name=Test Batch"
```

**2. Start workflows:**

```bash
curl -X POST http://localhost:8000/api/v1/batch/start-workflows \
  -d "batch_id=1" \
  -d "workflow_definition_id=batch"
```

**3. Start workers to process:**

```bash
si-cli worker
```

**4. Monitor progress:**

```bash
curl http://localhost:8000/api/v1/batch/status?batch_id=1
```

---

### Configuration Management

**List available workflows:**

```bash
si-cli list-workflows
```

**Inspect workflow:**

```bash
si-cli dump-workflow batch
```

**List parameter sets:**

```bash
si-cli list-param-sets
```

**Inspect parameters:**

```bash
si-cli dump-param-set default
```

---

### Troubleshooting

**Check configuration:**

```bash
si-cli validate-settings
```

**Verify database connection:**

```bash
si-cli db-init
```

**Test server startup:**

```bash
si-cli serve --host localhost --port 8000
# Press Ctrl+C to stop
```

**Check worker connectivity:**

```bash
si-cli worker
# Should start without errors
# Press Ctrl+C to stop
```

---

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Configuration error / validation failed |
| `130` | Interrupted by user (Ctrl+C) |

---

## Environment Variables

The CLI respects all configuration environment variables. Key ones for CLI usage:

- `DOC_DB_URL` - Database connection (required)
- `LOG_LEVEL` - Logging verbosity (DEBUG, INFO, WARNING, ERROR)
- `WORKFLOW_DIR` - Workflow definitions directory
- `PARAM_DIR` - Parameter sets directory
- `DOCLING_SERVER_URL` - Docling service endpoint
- `LANCEDB_DIR` - Directory containing LanceDB databases (used by vacuum/verify commands)
- `LANCEDB_HMAC_KEY` - 64-byte key for HMAC-SHA512 database signing (used by `--sign` and `verify-db`)

See [CONFIGURATION.md](CONFIGURATION.md) for complete list.

---

## Logging

### Log Levels

Set via `LOG_LEVEL` environment variable:

```bash
LOG_LEVEL=DEBUG si-cli serve
```

**Levels:**

- `DEBUG` - Detailed diagnostic information
- `INFO` - General informational messages (default)
- `WARNING` - Warning messages
- `ERROR` - Error messages
- `CRITICAL` - Critical error messages

### Log Output

Logs are written to stderr. Redirect as needed:

```bash
si-cli worker 2>&1 | tee worker.log
```

### Log Format

Default format includes:

- Timestamp
- Log level
- Logger name
- Message

Example:

```text
2025-01-15 10:00:00,123 INFO soliplex.ingester.cli Starting server
2025-01-15 10:00:01,456 INFO soliplex.ingester.server Starting worker
```

---

## Signal Handling

### Graceful Shutdown

The CLI handles signals for graceful shutdown:

**Signals:**

- `SIGINT` (Ctrl+C) - Graceful shutdown
- `SIGTERM` - Graceful shutdown

**Behavior:**

1. Stop accepting new work
2. Complete current operations
3. Clean up resources
4. Exit with code 0

**Example:**

```bash
si-cli worker
# Press Ctrl+C
# Worker completes current step and exits
```

---

## Platform Notes

### Windows

On Windows, the CLI automatically sets the event loop policy for compatibility:

```python
if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
```

This is handled automatically; no user action required.

### Unix/Linux

No special configuration needed.

---

## Running with Python

If `si-cli` is not in PATH, run directly:

```bash
python -m soliplex.ingester.cli --help
```

---

## Docker Usage

**Dockerfile CMD examples:**

**Server:**

```dockerfile
CMD ["si-cli", "serve", "--host", "0.0.0.0", "--port", "8000"]
```

**Worker:**

```dockerfile
CMD ["si-cli", "worker"]
```

**Init container:**

```dockerfile
CMD ["si-cli", "db-init"]
```

---

## Systemd Service

**Example service file:**

**/etc/systemd/system/soliplex-ingester.service:**

```ini
[Unit]
Description=Soliplex Ingester API Server
After=network.target postgresql.service

[Service]
Type=simple
User=soliplex
Group=soliplex
WorkingDirectory=/opt/soliplex-ingester
EnvironmentFile=/etc/soliplex/config.env
ExecStart=/opt/soliplex-ingester/.venv/bin/si-cli serve --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

**/etc/systemd/system/soliplex-worker@.service:**

```ini
[Unit]
Description=Soliplex Ingester Worker %i
After=network.target postgresql.service

[Service]
Type=simple
User=soliplex
Group=soliplex
WorkingDirectory=/opt/soliplex-ingester
EnvironmentFile=/etc/soliplex/config.env
Environment="WORKER_ID=worker-%i"
ExecStart=/opt/soliplex-ingester/.venv/bin/si-cli worker
Restart=on-failure
RestartSec=10s

[Install]
WantedBy=multi-user.target
```

**Start services:**

```bash
sudo systemctl daemon-reload
sudo systemctl enable soliplex-ingester
sudo systemctl start soliplex-ingester
sudo systemctl enable soliplex-worker@{1..3}
sudo systemctl start soliplex-worker@{1..3}
```

---

## si-diag: Diagnostic CLI

The `si-diag` CLI provides read-only access to system state for debugging and monitoring. It uses the same database connection and configuration as `si-cli`.

**Entry Point:** `src/soliplex/ingester/diag_cli.py`

### Command Groups

| Group | Description |
|-------|-------------|
| `batch` | List batches |
| `document` | List, find, inspect, and view history of documents |
| `config` | List and inspect workflow definitions and parameter sets |
| `run-group` | List and inspect run groups |
| `workflow` | List and inspect workflow runs and steps |
| `status` | View running steps, recent activity, and aggregated details |
| `lancedb` | Vacuum, vacuum-all, and verify HMAC of LanceDB databases (with `--force` to break a stuck lock) |

---

### batch list

List all batches in the database.

```bash
si-diag batch list
```

**Output columns:** id, name, source, start_date, completed_date, duration

---

### document list

List document URIs filtered by source or batch ID.

```bash
si-diag document list --source filesystem
si-diag document list --batch-id 1
```

**Options:**

| Option | Type | Description |
|--------|------|-------------|
| `--source` | str | Filter by source |
| `--batch-id` | int | Filter by batch ID |

One of `--source` or `--batch-id` is required.

---

### document find

Search for document URIs by pattern (case-insensitive substring match).

```bash
si-diag document find "quarterly"
si-diag document find ".pdf"
```

**Arguments:**

- `PATTERN` (str, required) - Search pattern for URI

---

### document info

Display detailed information about a document by its hash.

```bash
si-diag document info sha256-abc123...
```

**Arguments:**

- `DOC_HASH` (str, required) - Document hash

**Shows:** mime_type, file_size, doc_meta (as JSON), and associated URIs.

---

### document history

Show DocumentURIHistory records for a document hash.

```bash
si-diag document history sha256-abc123...
```

**Arguments:**

- `DOC_HASH` (str, required) - Document hash

---

### config workflows

List all available workflow definitions from the workflow registry.

```bash
si-diag config workflows
```

---

### config params

List all available parameter sets from the parameter registry.

```bash
si-diag config params
```

---

### config workflow-def

Display a workflow definition as YAML.

```bash
si-diag config workflow-def batch
```

**Arguments:**

- `WF_ID` (str, required) - Workflow definition ID

---

### config param-def

Display a parameter set definition as YAML.

```bash
si-diag config param-def default
```

**Arguments:**

- `PARAM_ID` (str, required) - Parameter set ID

---

### run-group list

List run groups, optionally filtered by batch ID.

```bash
si-diag run-group list
si-diag run-group list --batch-id 1
```

**Options:**

| Option | Type | Description |
|--------|------|-------------|
| `--batch-id` | int | Filter by batch ID |

---

### run-group info

Display run group details including a status breakdown of workflow runs.

```bash
si-diag run-group info 1
```

**Arguments:**

- `RUN_GROUP_ID` (int, required) - Run group ID

---

### workflow list

List workflow runs for a run group.

```bash
si-diag workflow list 1
si-diag workflow list 1 --status FAILED
```

**Arguments:**

- `RUN_GROUP_ID` (int, required) - Run group ID

**Options:**

| Option | Type | Description |
|--------|------|-------------|
| `--status` | str | Filter by status (PENDING, RUNNING, COMPLETED, FAILED) |

---

### workflow info

Display workflow run info and its steps.

```bash
si-diag workflow info 42
si-diag workflow info 42 --status FAILED
```

**Arguments:**

- `WORKFLOW_RUN_ID` (int, required) - Workflow run ID

**Options:**

| Option | Type | Description |
|--------|------|-------------|
| `--status` | str | Filter steps by status |

---

### status running

List all run steps currently in RUNNING status with enriched context.

```bash
si-diag status running
```

**Output columns:** workflow_id, doc_hash, doc_uri, run_group, param_def_id, step_type, started, elapsed

---

### status recent

List steps with status updates within a time interval.

```bash
si-diag status recent minute
si-diag status recent hour --status COMPLETED
si-diag status recent day --status FAILED
```

**Arguments:**

- `INTERVAL` (str, default: "minute") - Time interval: minute, hour, day, week

**Options:**

| Option | Type | Description |
|--------|------|-------------|
| `--status` | str | Filter by status |

---

### status details

Display aggregated step details for a run group. **PostgreSQL only.**

```bash
si-diag status details 1
```

**Arguments:**

- `RUN_GROUP_ID` (int, required) - Run group ID

**Output columns:** batch_name, param_def_id, step_type, status, count, pages

**Note:** This command uses PostgreSQL-specific JSONB functions and will return an error on SQLite.

---

### lancedb vacuum

Vacuum a single LanceDB database. Fails fast (exit code 2) if the
cross-subsystem `ResourceLock` is held by another writer.

```bash
si-diag lancedb vacuum my_database
si-diag lancedb vacuum my_database --sign
si-diag lancedb vacuum my_database --force
```

**Arguments:**

- `DB_NAME` (str, required) - Name of the database directory under `LANCEDB_DIR`

**Options:**

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--sign` | bool | False | Write an HMAC-SHA512 signature after vacuuming (requires `LANCEDB_HMAC_KEY`) |
| `--force` | bool | False | Audit-log + drop the `ResourceLock` row before retrying (use when a holder has crashed) |

**Exit Codes:**

- `0` - Vacuum completed
- `1` - Database not found (used with `--force` resolution)
- `2` - Lock held by another writer; pass `--force` to break

**Notes:**

- Acquires the `ResourceLock` with `holder_kind=cli`, `max_wait=0`
- `--force` calls `force_release_resource_lock`, which emits a
  warning log line containing the previous holder for audit

---

### lancedb vacuum-all

Vacuum every database under `LANCEDB_DIR`. DBs whose lock is held
by another writer are skipped with a printed message; processing
continues with the remaining databases.

```bash
si-diag lancedb vacuum-all
si-diag lancedb vacuum-all --sign
```

**Options:**

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--sign` | bool | False | HMAC-sign each database after vacuuming |

---

### lancedb verify

Verify the HMAC-SHA512 signature of a LanceDB database against
its `.hmac` sidecar. Requires `LANCEDB_HMAC_KEY`.

```bash
si-diag lancedb verify my_database
```

**Arguments:**

- `DB_NAME` (str, required) - Name of the database directory under `LANCEDB_DIR`

**Exit Codes:**

- `0` - HMAC verification passed
- `1` - Verification failed or sidecar missing

---

## Future Commands

Commands planned for future releases:

- `si-cli batch create` - Create batch via CLI
- `si-cli batch ingest` - Ingest documents from directory
- `si-cli batch status` - Check batch status
- `si-cli workflow retry` - Retry failed workflows
- `si-cli stats` - Display system statistics
- `si-cli clean` - Clean up old data

---

## Getting Help

**Command help:**

```bash
si-cli --help
si-cli serve --help
si-cli worker --help
si-diag --help
si-diag status --help
```

**Report issues:**

- GitHub: <https://github.com/your-repo/soliplex-ingester/issues>
- Include `si-cli validate-settings` output
- Include relevant logs with `LOG_LEVEL=DEBUG`
