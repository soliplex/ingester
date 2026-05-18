# Configuration Guide

## Overview

Soliplex Ingester is configured via environment variables using Pydantic Settings. All configuration is defined in `src/soliplex/ingester/lib/config.py:15`.

## Environment Variables

### Database Configuration

#### DOC_DB_URL (required)

Database connection URL.

#### AUTO_CREATE_DATABASE

Automatically create database tables on initialization.

**Default:** `True`

**Example:**

```bash
AUTO_CREATE_DATABASE=false
```

**Notes:**

- When `true`, all SQLModel tables are created via `CREATE TABLE IF NOT EXISTS` during `Database.initialize()`
- Set to `false` when using a migration tool (e.g., Alembic) to manage schema changes
- Useful in production where schema should be controlled by migrations rather than auto-created

**SQLite Example:**

```bash
DOC_DB_URL="sqlite+aiosqlite:///./db/documents.db"
```

**PostgreSQL Example:**

```bash
DOC_DB_URL="postgresql+psycopg://username:password@localhost:5432/soliplex"
```

**Notes:**

- Must use async drivers (`aiosqlite` for SQLite or `psycopg` for PostgreSQL)
- SQLite uses relative or absolute file paths
- PostgreSQL requires credentials and network access

---

### External Services

#### DOCLING_SERVER_URL

Docling document parsing service endpoint.

**Default:** `http://localhost:5001/v1`

**Example:**

```bash
DOCLING_SERVER_URL="http://docling.internal.company.com/v1"
```

**Notes:**

- Used for document parsing (PDF, DOCX, etc.)
- Must be accessible from worker nodes
- Health check: `GET {url}/health`

#### DOCLING_CHUNK_SERVER_URL

Docling service endpoint for chunking operations.

**Default:** `http://localhost:5001/v1`

**Example:**

```bash
DOCLING_CHUNK_SERVER_URL="http://docling-chunker.internal.company.com/v1"
```

**Notes:**

- Used by haiku.rag for document chunking via docling-serve
- Can point to a different Docling instance than `DOCLING_SERVER_URL` for load distribution
- If not set, defaults to the same endpoint as parsing
- Useful when running separate Docling instances for parsing vs chunking

#### DOCLING_HTTP_TIMEOUT

HTTP timeout for Docling requests in seconds.

**Default:** `600` (10 minutes)

**Example:**

```bash
DOCLING_HTTP_TIMEOUT=300
```

**Notes:**

- Large documents may require longer timeouts
- Adjust based on document size and complexity

#### OLLAMA_BASE_URL

Ollama server endpoint for embedding generation.

**Default:** `http://ollama:11434`

**Example:**

```bash
OLLAMA_BASE_URL="http://ollama.internal.company.com:11434"
```

**Notes:**

- Used for generating document embeddings during the embed step
- Must be accessible from worker nodes
- The Ollama server should have the required embedding models loaded

#### OLLAMA_BASE_URL_DOCLING

Ollama server endpoint for Docling chunking operations.

**Default:** `http://ollama:11434`

**Example:**

```bash
OLLAMA_BASE_URL_DOCLING="http://ollama-chunker.internal.company.com:11434"
```

**Notes:**

- Used by docling-serve for document chunking operations
- Can point to a different Ollama instance than `OLLAMA_BASE_URL` for load distribution
- If not set, defaults to the same URL as `OLLAMA_BASE_URL`
- Useful when running separate Ollama instances to distribute model loading across servers

#### OPENAI_API_KEY

API key used by the OpenAI-backed embedding provider (via haiku.rag).

**Default:** None

**Example:**

```bash
OPENAI_API_KEY=sk-...
```

**Notes:**

- Required only when a param set selects the `openai` embedding provider; otherwise unused
- Treated as a secret (stored via `SecretStr`, supports `/run/secrets` directory — e.g. mount as `/run/secrets/openai_api_key`)
- When loaded, the value is exported to the `OPENAI_API_KEY` process environment variable at startup so the OpenAI SDK / haiku.rag can pick it up
- If `OPENAI_API_KEY` is already set in the environment, the existing value wins and the secret file is ignored
- Keep this value secure - do not commit to version control

#### EMBED_LLM_URL

Base URL for the OpenAI-compatible embeddings endpoint.

**Default:** None

**Examples:**

```bash
# Real OpenAI API
EMBED_LLM_URL=https://api.openai.com/v1

# Self-hosted vLLM (append /v1)
EMBED_LLM_URL=http://vllm.internal.company.com:8000/v1

# Any OpenAI-compatible gateway
EMBED_LLM_URL=http://llm-proxy.internal.company.com/v1
```

**Notes:**

- **Required** when a param set selects the `openai` embedding provider; startup embedding will fail with `embed_llm_url is not set` otherwise
- Ignored when the `ollama` provider is used (Ollama uses `OLLAMA_BASE_URL` instead)
- Must include the `/v1` suffix for vLLM and most OpenAI-compatible backends (haiku.rag does not append it automatically)
- Use this to point at self-hosted OpenAI-compatible servers (vLLM, text-embeddings-inference, LiteLLM, etc.) without touching param set YAML
- Paired with `OPENAI_API_KEY` for authentication; self-hosted backends that don't require auth still need the key set to any non-empty value

---

### Logging

#### LOG_LEVEL

Python logging level.

**Default:** `INFO`

**Options:** `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`

**Example:**

```bash
LOG_LEVEL=DEBUG
```

#### LOG_FORMAT

Log output format.

**Default:** `text`

**Options:**

- `text` - Human-readable log lines (default Python formatting)
- `json` - Structured JSON, one object per line

**Example:**

```bash
LOG_FORMAT=json
```

**Notes:**

- JSON format emits one JSON object per log line with fields: `timestamp`, `level`, `name`, `message`
- Any `extra` fields passed to logger calls (e.g., `log.info("msg", extra={"doc_id": 123})`) are included as top-level keys in the JSON output
- If an exception is attached, it appears in the `exception` field
- Recommended for production and container environments where logs are consumed by aggregation tools (ELK, Datadog, CloudWatch, etc.)

**JSON output example:**

```json
{"timestamp": "2026-03-23T14:05:12", "level": "INFO", "name": "soliplex.ingester", "message": "Processing document", "doc_id": 123, "batch_id": "abc"}
```

---

### File Storage

#### FILE_STORE_TARGET

Storage backend type.

**Default:** `fs` (filesystem)

**Options:**

- `fs` - Local filesystem
- `s3` - S3-compatible storage (requires OpenDAL config)

**Example:**

```bash
FILE_STORE_TARGET=s3
```

#### FILE_STORE_DIR

Base directory for file storage.

**Default:** `file_store`

**Example:**

```bash
FILE_STORE_DIR=/var/lib/soliplex/files
```

**Notes:**

- Used when `FILE_STORE_TARGET=fs`
- Must be writable by the application user
- Consider disk space requirements

#### DOCUMENT_STORE_DIR

Subdirectory for raw documents.

**Default:** `raw`

**Full Path:** `{FILE_STORE_DIR}/{DOCUMENT_STORE_DIR}`

#### PARSED_MARKDOWN_STORE_DIR

Subdirectory for parsed markdown.

**Default:** `markdown`

#### PARSED_JSON_STORE_DIR

Subdirectory for parsed JSON.

**Default:** `json`

#### CHUNKS_STORE_DIR

Subdirectory for text chunks.

**Default:** `chunks`

#### EMBEDDINGS_STORE_DIR

Subdirectory for embedding vectors.

**Default:** `embeddings`

#### FILE_PROTECTION_LEVEL

Protection level for file-stored artifacts. Controls integrity checking and encryption.

**Default:** `none`

**Options:**

- `none` - No protection (current default behavior)
- `hash` - SHA-512 hash verification: writes a `.hash` sidecar file alongside each artifact
- `hmac` - HMAC-SHA-512 verification: writes a `.hmac` sidecar file using a keyed hash (requires `FILE_SECRET`)
- `encrypt` - Fernet encryption: encrypts artifact bytes at rest using authenticated encryption (requires `FILE_SECRET`)

**Example:**

```bash
FILE_PROTECTION_LEVEL=encrypt
FILE_SECRET=my-strong-secret-key
```

**Notes:**

- Only applies when `FILE_STORE_TARGET=fs` (filesystem storage)
- `hmac` and `encrypt` modes require `FILE_SECRET` to be set
- A single `FILE_SECRET` is used for both HMAC and encryption; purpose-specific keys are derived internally via HKDF-SHA256
- Changing protection level does not retroactively modify existing files
- `hash` mode detects accidental corruption; `hmac` mode detects tampering; `encrypt` mode provides confidentiality

#### FILE_COMPRESSION_ARTIFACTS

Artifact types to compress at rest (filesystem storage only).

**Default:** `[]` (no compression)

**Example:**

```bash
FILE_COMPRESSION_ARTIFACTS=parsed_markdown,parsed_json,chunks
```

**Notes:**

- Comma-separated list of `ArtifactType` values:
  `document`, `parsed_markdown`, `parsed_json`, `chunks`,
  `embeddings`
- Only applies when `FILE_STORE_TARGET=fs`
- Stacks with `FILE_PROTECTION_LEVEL` — compressed artifacts can
  also be HMAC-signed or encrypted
- Existing uncompressed artifacts are not retroactively compressed

#### FILE_COMPRESSION_LEVEL

Compression level used when an artifact type is listed in
`FILE_COMPRESSION_ARTIFACTS`.

**Default:** `3`

**Example:**

```bash
FILE_COMPRESSION_LEVEL=6
```

**Notes:**

- Range and semantics depend on the underlying compressor (zstd)
- Higher values trade CPU for smaller files
- Defaults are chosen for good ratio with low CPU cost

#### FILE_SECRET

Master secret used for HMAC and encryption operations.

**Default:** None

**Example:**

```bash
FILE_SECRET=my-strong-secret-key
```

**Notes:**

- Required when `FILE_PROTECTION_LEVEL` is `hmac` or `encrypt`
- Treated as a secret (stored via `SecretStr`, supports `/run/secrets` directory)
- Recommended minimum length: 64 bytes (the HMAC-SHA-512 key size). Generate with: `openssl rand -hex 64`
- Purpose-specific keys are derived internally via HKDF, so any sufficiently long secret works for both HMAC and encryption
- Keep this value secure - do not commit to version control
- Changing the secret will make previously protected files unreadable

---

### Vector Database

#### LANCEDB_DIR

Directory for LanceDB vector storage.

**Default:** `lancedb`

**Filesystem Example:**

```bash
LANCEDB_DIR=/var/lib/soliplex/lancedb
```

**S3 Example:**

```bash
LANCEDB_DIR=s3://my-bucket/lancedb
```

**Notes:**

- Local filesystem paths or S3 URIs are supported
- When using S3, LanceDB requires standard AWS environment variables (see below)
- Stores vector embeddings for RAG retrieval
- Requires sufficient disk space (filesystem) or S3 bucket access
- Periodically compact for performance

**Required AWS Environment Variables for S3:**

```bash
AWS_ACCESS_KEY_ID=your_access_key_id
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_REGION=us-east-1
```

**Optional AWS Environment Variables:**

```bash
# For S3-compatible providers (MinIO, SeaweedFS, etc.)
AWS_ENDPOINT=http://127.0.0.1:8333

# Required when using HTTP to connect to endpoint
AWS_ALLOW_HTTP=1
```

---

### Worker Configuration

#### INGEST_QUEUE_CONCURRENCY

Maximum concurrent queue operations.

**Default:** `20`

**Example:**

```bash
INGEST_QUEUE_CONCURRENCY=50
```

**Notes:**

- Controls internal queue processing
- Higher values increase throughput but use more memory

#### INGEST_WORKER_CONCURRENCY

Legacy ceiling for total concurrent workflow steps per worker.

**Default:** `10`

**Example:**

```bash
INGEST_WORKER_CONCURRENCY=20
```

**Notes:**

- Retained for reference; the current `Worker` derives its
  consumer-pool layout from `DOCLING_CONCURRENCY` (parse pool) and
  `WORKER_TASK_COUNT` (store + catch-all pools)
- To set a global hard cap, construct a `Worker` directly with
  `WorkerConfig(consumers={"*": N})`

#### DOCLING_CONCURRENCY

Size of the dedicated `parse` consumer pool. Bounds concurrent
Docling requests at the database-claim layer.

**Default:** `3`

**Example:**

```bash
DOCLING_CONCURRENCY=5
```

**Notes:**

- Each consumer in the pool only claims `parse` steps, so
  Docling cannot be over-issued even under burst load
- Replaces the previous in-process HTTP semaphore in `lib/docling.py`
- Coordinate with Docling server capacity
- Set to `0` to disable the dedicated pool (parse steps fall back
  to the catch-all `"*"` pool)

#### WORKER_TASK_COUNT

Size of the `store` consumer pool and the catch-all `"*"` pool.

**Default:** `5`

**Example:**

```bash
WORKER_TASK_COUNT=10
```

**Notes:**

- Bounds concurrent `save_to_rag` (STORE) steps separately from
  Docling and the rest of the pipeline
- The `"*"` pool picks up step types that are not explicitly pinned
  (validate, chunk, embed, enrich, route)
- Higher values increase parallelism but also increase contention
  on the database claim path

#### WORKER_CHECKIN_INTERVAL

Worker heartbeat interval in seconds.

**Default:** `120` (2 minutes)

**Example:**

```bash
WORKER_CHECKIN_INTERVAL=60
```

**Notes:**

- How often workers update health status
- Lower values increase database load slightly
- Used for monitoring worker liveness

#### WORKER_CHECKIN_TIMEOUT

Worker timeout threshold in seconds.

**Default:** `600` (10 minutes)

**Example:**

```bash
WORKER_CHECKIN_TIMEOUT=300
```

**Notes:**

- When to consider a worker stale
- Should be significantly larger than `WORKER_CHECKIN_INTERVAL`
- Used for detecting crashed workers

#### EMBED_BATCH_SIZE

Batch size for embedding operations.

**Default:** `1000`

**Example:**

```bash
EMBED_BATCH_SIZE=500
```

**Notes:**

- Number of chunks to embed at once
- Higher values improve throughput
- Limited by embedding service capacity and memory

---

### Workflow Configuration

#### WORKFLOW_DIR

Directory containing workflow YAML definitions.

**Default:** `config/workflows`

**Example:**

```bash
WORKFLOW_DIR=/etc/soliplex/workflows
```

**Notes:**

- Scanned for `*.yaml` files at startup
- Hot-reload if `--reload` flag is used

#### DEFAULT_WORKFLOW_ID

Default workflow to use when not specified.

**Default:** `batch_split`

**Example:**

```bash
DEFAULT_WORKFLOW_ID=batch_split
```

**Notes:**

- Must match an `id` in workflow YAML files
- Used when API requests omit `workflow_definition_id`

#### PARAM_DIR

Directory containing system parameter set YAML files.

**Default:** `config/params`

**Example:**

```bash
PARAM_DIR=/etc/soliplex/params
```

#### USER_PARAM_DIR

Directory containing user-uploaded parameter set YAML files. Must be different from `PARAM_DIR`. Both directories are merged transparently at load time; parameter set IDs must be unique across both.

**Default:** `config/user_params`

**Example:**

```bash
USER_PARAM_DIR=/etc/soliplex/user_params
```

#### DEFAULT_PARAM_ID

Default parameter set to use when not specified.

**Default:** `default`

**Example:**

```bash
DEFAULT_PARAM_ID=high_quality
```

**Notes:**

- Must match an `id` in parameter YAML files

---

### Feature Flags

#### DO_RAG

Enable/disable HaikuRAG integration.

**Default:** `True`

**Example:**

```bash
DO_RAG=false
```

**Notes:**

- Set to `false` for testing without RAG backend
- When disabled, `store` step becomes a no-op
- Useful for CI/CD testing

---

### Authentication

#### API_KEY

Static API key for programmatic access.

**Default:** None (disabled)

**Example:**

```bash
API_KEY=your-secret-api-key-here
```

**Notes:**

- Generate with: `openssl rand -hex 32`
- Must also set `API_KEY_ENABLED=true` to enforce
- Clients pass via `Authorization: Bearer <token>` header
- Keep this value secure - do not commit to version control

#### API_KEY_ENABLED

Enable API key authentication.

**Default:** `False`

**Example:**

```bash
API_KEY_ENABLED=true
```

**Notes:**

- When `true`, all API requests require valid `Authorization: Bearer` header
- When `false`, API is open (or protected by OAuth2 Proxy)
- Can be combined with `AUTH_TRUST_PROXY_HEADERS` for hybrid auth

#### AUTH_TRUST_PROXY_HEADERS

Trust user identity headers from OAuth2 Proxy.

**Default:** `False`

**Example:**

```bash
AUTH_TRUST_PROXY_HEADERS=true
```

**Notes:**

- Enable when running behind OAuth2 Proxy
- Reads user identity from `X-Auth-Request-User`, `X-Auth-Request-Email` headers
- **Security:** Only enable when behind a trusted reverse proxy
- See [AUTHENTICATION.md](AUTHENTICATION.md) for OAuth2 Proxy setup

---

## Configuration File

While the system uses environment variables, you can organize them in a `.env` file:

**.env Example:**

```bash
# Database
DOC_DB_URL=sqlite+aiosqlite:///./db/documents.db
AUTO_CREATE_DATABASE=true

# External Services
DOCLING_SERVER_URL=http://localhost:5001/v1
DOCLING_HTTP_TIMEOUT=600

# Logging
LOG_LEVEL=INFO
LOG_FORMAT={name}|{asctime}|{levelname}|{message}

# Storage
FILE_STORE_TARGET=fs
FILE_STORE_DIR=file_store
LANCEDB_DIR=lancedb

# Worker Settings
INGEST_WORKER_CONCURRENCY=10
DOCLING_CONCURRENCY=3
WORKER_TASK_COUNT=5
WORKER_CHECKIN_INTERVAL=120
WORKER_CHECKIN_TIMEOUT=600
EMBED_BATCH_SIZE=1000

# Workflow Configuration
WORKFLOW_DIR=config/workflows
DEFAULT_WORKFLOW_ID=batch_split
PARAM_DIR=config/params
USER_PARAM_DIR=config/user_params
DEFAULT_PARAM_ID=default

# Features
DO_RAG=true
```

Load with:

```bash
export $(cat .env | xargs)
si-cli serve
```

---

## S3 Configuration Overview

This project supports S3 storage in two different contexts with different configuration methods:

### 1. LanceDB Vector Storage (`LANCEDB_DIR`)

**Purpose:** Stores vector embeddings for RAG retrieval
**Configuration Method:** Standard AWS environment variables
**Example:**

```bash
LANCEDB_DIR=s3://my-vector-bucket/lancedb
AWS_ACCESS_KEY_ID=your_key_id
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_REGION=us-east-1
```

### 2. Artifact Storage (`FILE_STORE_TARGET=s3`)

**Purpose:** Stores intermediate processing artifacts (documents, markdown, chunks, embeddings)
**Configuration Method:** Nested Pydantic settings with `__` delimiter
**Example:**

```bash
FILE_STORE_TARGET=s3
ARTIFACT_S3__BUCKET=my-artifact-bucket
ARTIFACT_S3__ACCESS_KEY_ID=your_key_id
ARTIFACT_S3__ACCESS_SECRET=your_secret_key
ARTIFACT_S3__REGION=us-east-1
ARTIFACT_S3__ENDPOINT_URL=http://127.0.0.1:8333
```

**Important Notes:**

- These are independent systems and can use different S3 buckets or providers
- LanceDB uses standard AWS SDK naming (`AWS_SECRET_ACCESS_KEY`)
- Artifact/Input S3 uses Pydantic nested naming (`ARTIFACT_S3__ACCESS_SECRET`)
- The field name difference is intentional to support Pydantic's nested configuration

---

## Nested Configuration (Advanced)

Pydantic Settings supports nested configuration using `__` delimiter for structured settings.

**Artifact S3 Configuration:**

```bash
ARTIFACT_S3__BUCKET=soliplex-artifacts
ARTIFACT_S3__ACCESS_KEY_ID=soliplex
ARTIFACT_S3__ACCESS_SECRET=soliplex
ARTIFACT_S3__REGION=xx
ARTIFACT_S3__ENDPOINT_URL=http://127.0.0.1:8333
```

**Input S3 Configuration:**

```bash
INPUT_S3__BUCKET=soliplex-inputs
INPUT_S3__ACCESS_KEY_ID=soliplex
INPUT_S3__ACCESS_SECRET=soliplex
INPUT_S3__REGION=xx
INPUT_S3__ENDPOINT_URL=http://127.0.0.1:8333
```

**Notes:**

- `ACCESS_SECRET` (not `SECRET_ACCESS_KEY`) is used for nested config fields
- Nested delimiter is `__` (double underscore)
- Both `INPUT_S3` and `ARTIFACT_S3` can point to different buckets/providers

See `src/soliplex/ingester/lib/config.py:7-16` for nested model definitions.

---

## Configuration Validation

### Validate Settings

Check configuration without starting services:

```bash
si-cli validate-settings
```

**Output:**

```text
doc_db_url='sqlite+aiosqlite:///./db/documents.db'
docling_server_url='http://localhost:5001/v1'
log_level='INFO'
...
```

**Validation Errors:**

```text
invalid settings
{'type': 'missing', 'loc': ('doc_db_url',), 'msg': 'Field required'}
```

---

## Environment-Specific Configuration

### Development

**dev.env:**

```bash
DOC_DB_URL=sqlite+aiosqlite:///./db/dev.db
LOG_LEVEL=DEBUG
INGEST_WORKER_CONCURRENCY=5
DO_RAG=false
```

### Staging

**staging.env:**

```bash
DOC_DB_URL=postgresql+psycopg://user:pass@staging-db:5432/soliplex
LOG_LEVEL=INFO
INGEST_WORKER_CONCURRENCY=10
DOCLING_SERVER_URL=http://docling-staging:5001/v1
DO_RAG=true
```

### Production

**production.env:**

```bash
DOC_DB_URL=postgresql+psycopg://user:pass@prod-db:5432/soliplex
LOG_LEVEL=WARNING
LOG_FORMAT=json
INGEST_WORKER_CONCURRENCY=20
DOCLING_CONCURRENCY=5
DOCLING_SERVER_URL=http://docling-prod:5001/v1
FILE_STORE_TARGET=s3
DO_RAG=true
WORKER_CHECKIN_INTERVAL=60
```

---

## Docker Configuration

### Docker Compose Example

**docker-compose.yml:**

```yaml
version: '3.8'

services:
  ingester:
    image: soliplex-ingester:latest
    environment:
      DOC_DB_URL: postgresql+psycopg://postgres:password@db:5432/soliplex
      DOCLING_SERVER_URL: http://docling:5001/v1
      LOG_LEVEL: INFO
      FILE_STORE_DIR: /data/files
      LANCEDB_DIR: /data/lancedb
      INGEST_WORKER_CONCURRENCY: 15
    volumes:
      - ./config/workflows:/app/config/workflows
      - ./config/params:/app/config/params
      - data-volume:/data
    depends_on:
      - db
      - docling

  db:
    image: postgres:16
    environment:
      POSTGRES_DB: soliplex
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: password
    volumes:
      - db-volume:/var/lib/postgresql/data

  docling:
    image: docling-server:latest
    ports:
      - "5001:5001"

volumes:
  db-volume:
  data-volume:
```

### Kubernetes ConfigMap

**configmap.yaml:**

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: soliplex-config
data:
  DOC_DB_URL: "postgresql+psycopg://user:pass@postgres-service:5432/soliplex"
  DOCLING_SERVER_URL: "http://docling-service:5001/v1"
  LOG_LEVEL: "INFO"
  INGEST_WORKER_CONCURRENCY: "20"
  WORKFLOW_DIR: "/config/workflows"
  PARAM_DIR: "/config/params"
  USER_PARAM_DIR: "/config/user_params"
```

---

## Performance Tuning

### High Throughput

```bash
INGEST_WORKER_CONCURRENCY=50
DOCLING_CONCURRENCY=10
WORKER_TASK_COUNT=20
EMBED_BATCH_SIZE=2000
```

**Notes:**

- Requires powerful hardware
- Monitor CPU, memory, and I/O
- Coordinate with external service capacity

### Resource Constrained

```bash
INGEST_WORKER_CONCURRENCY=5
DOCLING_CONCURRENCY=2
WORKER_TASK_COUNT=3
EMBED_BATCH_SIZE=500
```

**Notes:**

- Reduces memory and CPU usage
- Lower throughput but more stable
- Good for shared environments

### Batch Processing

```bash
INGEST_WORKER_CONCURRENCY=30
DOCLING_CONCURRENCY=8
WORKER_TASK_COUNT=10
WORKER_CHECKIN_INTERVAL=300
```

**Notes:**

- Optimized for processing large batches
- Reduces monitoring overhead
- Assumes long-running workers

---

## Secrets Management

### Using Environment Files

Keep secrets out of version control:

```bash
# .env.secrets (add to .gitignore)
DOC_DB_URL=postgresql+psycopg://user:$(cat /run/secrets/db_password)@db/soliplex
```

### Using Secret Management Tools

**AWS Secrets Manager:**

```bash
export DOC_DB_URL=$(aws secretsmanager get-secret-value --secret-id db-url --query SecretString --output text)
```

**HashiCorp Vault:**

```bash
export DOC_DB_URL=$(vault kv get -field=url secret/soliplex/db)
```

**Kubernetes Secrets:**

```yaml
env:
  - name: DOC_DB_URL
    valueFrom:
      secretKeyRef:
        name: db-credentials
        key: url
```

---

## Troubleshooting

### Configuration Not Loading

**Symptom:** Application uses default values

**Solutions:**

1. Verify environment variables are set: `env | grep DOC_`
2. Check for typos in variable names
3. Ensure `.env` file is in correct directory
4. Verify `.env` file is being loaded

### Validation Errors

**Symptom:** Application fails to start with validation error

**Solutions:**

1. Run `si-cli validate-settings` to see errors
2. Check required fields are set
3. Verify value types (e.g., integers for ports)
4. Check URL formats

### Connection Errors

**Symptom:** Cannot connect to database or Docling

**Solutions:**

1. Verify URLs are correct
2. Test connectivity: `curl http://docling-url/health`
3. Check network policies/firewall
4. Verify credentials

### File Permissions

**Symptom:** Cannot write to storage directories

**Solutions:**

1. Check directory exists and is writable
2. Verify application user permissions
3. Create directories if needed: `mkdir -p file_store lancedb`
4. Set ownership: `chown -R app-user file_store lancedb`

---

## Configuration Reference

| Variable | Type | Required | Default | Description |
|----------|------|----------|---------|-------------|
| `DOC_DB_URL` | str | Yes | - | Database connection URL |
| `AUTO_CREATE_DATABASE` | bool | No | `True` | Auto-create database tables on init |
| `DOCLING_SERVER_URL` | str | No | `http://localhost:5001/v1` | Docling parsing service URL |
| `DOCLING_CHUNK_SERVER_URL` | str | No | `http://localhost:5001/v1` | Docling chunking service URL |
| `DOCLING_HTTP_TIMEOUT` | int | No | `600` | Docling timeout (seconds) |
| `LOG_LEVEL` | str | No | `INFO` | Logging level |
| `LOG_FORMAT` | str | No | `{name}\|{asctime}\|{levelname}\|{message}` | Log format string (or `json`) |
| `FILE_STORE_TARGET` | str | No | `fs` | Storage backend type |
| `FILE_STORE_DIR` | str | No | `file_store` | Base storage directory |
| `LANCEDB_DIR` | str | No | `lancedb` | LanceDB directory (supports S3 URIs) |
| `DOCUMENT_STORE_DIR` | str | No | `raw` | Raw documents subdir |
| `PARSED_MARKDOWN_STORE_DIR` | str | No | `markdown` | Markdown subdir |
| `PARSED_JSON_STORE_DIR` | str | No | `json` | JSON subdir |
| `CHUNKS_STORE_DIR` | str | No | `chunks` | Chunks subdir |
| `EMBEDDINGS_STORE_DIR` | str | No | `embeddings` | Embeddings subdir |
| `FILE_PROTECTION_LEVEL` | str | No | `none` | File protection level (none/hash/hmac/encrypt) |
| `FILE_SECRET` | str | Conditional | - | Master secret for HMAC/encryption (required if protection is hmac or encrypt) |
| `FILE_COMPRESSION_ARTIFACTS` | list[str] | No | `[]` | Artifact types to compress at rest (filesystem only) |
| `FILE_COMPRESSION_LEVEL` | int | No | `3` | zstd compression level for compressed artifacts |
| `INGEST_QUEUE_CONCURRENCY` | int | No | `20` | Queue concurrency |
| `INGEST_WORKER_CONCURRENCY` | int | No | `10` | Legacy global concurrency ceiling |
| `DOCLING_CONCURRENCY` | int | No | `3` | Size of the dedicated `parse` consumer pool |
| `WORKER_TASK_COUNT` | int | No | `5` | Size of the `store` pool and catch-all `*` pool |
| `WORKER_CHECKIN_INTERVAL` | int | No | `120` | Heartbeat interval (sec) |
| `WORKER_CHECKIN_TIMEOUT` | int | No | `600` | Worker timeout (sec) |
| `EMBED_BATCH_SIZE` | int | No | `1000` | Embedding batch size |
| `OLLAMA_BASE_URL` | str | No | `http://ollama:11434` | Ollama server URL for embeddings |
| `OLLAMA_BASE_URL_DOCLING` | str | No | `http://ollama:11434` | Ollama server URL for Docling chunking (can differ for load distribution) |
| `WORKFLOW_DIR` | str | No | `config/workflows` | Workflow definitions dir |
| `DEFAULT_WORKFLOW_ID` | str | No | `batch_split` | Default workflow |
| `PARAM_DIR` | str | No | `config/params` | System parameter sets dir |
| `USER_PARAM_DIR` | str | No | `config/user_params` | User parameter sets dir |
| `DEFAULT_PARAM_ID` | str | No | `default` | Default parameter set |
| `AWS_ACCESS_KEY_ID` | str | Conditional | - | AWS access key (required for S3 LanceDB) |
| `AWS_SECRET_ACCESS_KEY` | str | Conditional | - | AWS secret key (required for S3 LanceDB) |
| `AWS_REGION` | str | Conditional | - | AWS region (required for S3 LanceDB) |
| `AWS_ENDPOINT` | str | No | - | S3 endpoint (for non-AWS providers) |
| `AWS_ALLOW_HTTP` | int | No | - | Allow HTTP for S3 (set to 1 for HTTP) |
| `ARTIFACT_S3__*` | nested | Conditional | - | Artifact S3 config (BUCKET, ACCESS_SECRET, etc.) |
| `INPUT_S3__*` | nested | Conditional | - | Input S3 config (BUCKET, ACCESS_SECRET, etc.) |
| `DO_RAG` | bool | No | `True` | Enable RAG integration |
| `API_KEY` | str | Conditional | - | Static API key (required if API_KEY_ENABLED) |
| `API_KEY_ENABLED` | bool | No | `False` | Enable API key authentication |
| `AUTH_TRUST_PROXY_HEADERS` | bool | No | `False` | Trust OAuth2 Proxy headers |
