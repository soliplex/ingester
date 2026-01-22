# Parameter Sets

Parameter sets define the configuration for document processing workflows in Soliplex Ingester. Each parameter set specifies how documents should be parsed, chunked, embedded, and stored.

## Table of Contents

- [Overview](#overview)
- [YAML Schema](#yaml-schema)
- [Configuration Sections](#configuration-sections)
- [Creating Parameter Sets](#creating-parameter-sets)
- [Managing Parameter Sets](#managing-parameter-sets)
- [Examples](#examples)
- [Best Practices](#best-practices)

---

## Overview

Parameter sets control every aspect of document processing:

- **Parsing:** How to extract text and structure from documents
- **Chunking:** How to split documents into semantic chunks
- **Embedding:** Which model to use for vector embeddings
- **Storage:** Where to store the resulting vector database

Parameter sets are stored as YAML files and can be created via:
- Configuration files in `config/params/` (built-in)
- REST API uploads (user-created)
- Web UI (if available)

---

## YAML Schema

### Complete Structure

```yaml
id: <string>              # Required: Unique identifier for this parameter set
name: <string>            # Optional: Human-readable name
config:                   # Required: Configuration sections
  parse:                  # Optional: Document parsing configuration
    do_ocr: <boolean>
    force_ocr: <boolean>
    ocr_engine: <string>
    ocr_lang: <string>
    pdf_backend: <string>
    table_mode: <string>

  chunk:                  # Optional: Document chunking configuration
    chunker: <string>
    chunk_size: <integer>
    text_context_radius: <integer>
    chunker_type: <string>

  embed:                  # Required: Embedding configuration
    provider: <string>
    model: <string>
    vector_dim: <integer>

  store:                  # Required: Storage configuration
    data_dir: <string>
```

### Field Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes | Unique identifier (alphanumeric, hyphens, underscores) |
| `name` | string | No | Display name for UI |
| `config` | object | Yes | Configuration sections |

---

## Configuration Sections

### Parse Configuration

Controls how documents are parsed and text is extracted.

```yaml
parse:
  do_ocr: false              # Enable OCR for images and scanned PDFs
  force_ocr: false           # Force OCR even if text layer exists
  ocr_engine: easyocr        # OCR engine: easyocr, tesseract
  ocr_lang: en               # OCR language code
  pdf_backend: pypdfium2     # PDF parsing backend: pypdfium2, pdfplumber
  table_mode: accurate       # Table extraction: accurate, fast
```

**Field Details:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `do_ocr` | boolean | `false` | Enable OCR for image-based PDFs |
| `force_ocr` | boolean | `false` | Force OCR even when text layer exists |
| `ocr_engine` | string | `easyocr` | OCR engine to use |
| `ocr_lang` | string | `en` | Language code for OCR (ISO 639-1) |
| `pdf_backend` | string | `pypdfium2` | PDF parsing library |
| `table_mode` | string | `accurate` | Table extraction mode |

**OCR Engines:**
- `easyocr` - Neural network-based OCR (recommended)
- `tesseract` - Traditional OCR engine

**PDF Backends:**
- `pypdfium2` - Fast, modern PDF parsing (recommended)
- `pdfplumber` - Feature-rich parsing with table support

**Table Modes:**
- `accurate` - Slower but more precise table extraction
- `fast` - Faster processing with acceptable accuracy

### Chunk Configuration

Controls how documents are split into chunks for vector search.

```yaml
chunk:
  chunker: docling-serve      # Chunking service: docling-serve, local
  chunk_size: 256             # Target chunk size in tokens
  text_context_radius: 0      # Context overlap in characters
  chunker_type: hybrid        # Chunking strategy: hybrid, hierarchical, token
```

**Field Details:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `chunker` | string | `docling-serve` | Chunking service to use |
| `chunk_size` | integer | `256` | Target chunk size in tokens |
| `text_context_radius` | integer | `0` | Character overlap between chunks |
| `chunker_type` | string | `hybrid` | Chunking strategy |

**Chunker Options:**
- `docling-serve` - Uses Docling service for semantic chunking (recommended)
- `local` - Local chunking without external service

**Chunker Types:**
- `hybrid` - Combines semantic and token-based splitting (recommended)
- `hierarchical` - Respects document structure (sections, paragraphs)
- `token` - Simple token-based splitting

**Chunk Size Guidelines:**
- **128-256 tokens:** Good for precise search, higher storage cost
- **256-512 tokens:** Balanced performance (recommended)
- **512-1024 tokens:** Better context, less precise search

**Text Context Radius:**
- **0:** No overlap (faster indexing, less redundancy)
- **50-100 chars:** Small overlap for continuity
- **100-200 chars:** Medium overlap (recommended for narratives)

### Embed Configuration

Controls vector embedding generation.

```yaml
embed:
  provider: ollama           # Embedding provider: ollama, openai, azure
  model: qwen3-embedding:4b  # Model identifier
  vector_dim: 2560           # Vector dimension
```

**Field Details:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `provider` | string | Yes | Embedding service provider |
| `model` | string | Yes | Model name or identifier |
| `vector_dim` | integer | Yes | Embedding vector dimension |

**Providers:**

#### Ollama
```yaml
embed:
  provider: ollama
  model: qwen3-embedding:4b
  vector_dim: 2560
```

**Environment variables:**
- `OLLAMA_BASE_URL` - Ollama server URL (e.g., `http://localhost:11434`)

**Popular models:**
- `nomic-embed-text` - 768 dimensions, excellent performance
- `mxbai-embed-large` - 1024 dimensions, high quality
- `qwen3-embedding:4b` - 2560 dimensions, multilingual

#### OpenAI
```yaml
embed:
  provider: openai
  model: text-embedding-3-small
  vector_dim: 1536
```

**Environment variables:**
- `OPENAI_API_KEY` - OpenAI API key

**Models:**
- `text-embedding-3-small` - 1536 dimensions, cost-effective
- `text-embedding-3-large` - 3072 dimensions, highest quality
- `text-embedding-ada-002` - 1536 dimensions (legacy)



**Important:** The `vector_dim` must match the actual dimension output by the model. Incorrect values will cause embedding errors.

### Store Configuration

Controls where the vector database is stored.

```yaml
store:
  data_dir: default_db       # Directory name within LANCEDB_DIR
```

**Field Details:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `data_dir` | string | Yes | Subdirectory name for this database |

**Storage Location:**

The full path is: `{LANCEDB_DIR}/{data_dir}/`

Example:
- `LANCEDB_DIR=/var/soliplex/lancedb`
- `data_dir=default_db`
- **Result:** `/var/soliplex/lancedb/default_db/`

**Best Practices:**
- Use descriptive names: `financial_reports`, `legal_docs`, `product_manuals`
- Separate databases by embedding model or chunk configuration
- Include version in name for model upgrades: `reports_v2`, `docs_ada002`

---

## Creating Parameter Sets

### Via Configuration File

1. **Create YAML file in `config/params/`:**

```bash
cd config/params
nano my_custom_params.yaml
```

2. **Define parameter set:**

```yaml
id: my_custom_params
name: My Custom Configuration
config:
  parse:
    do_ocr: true
    ocr_engine: easyocr
    table_mode: accurate
  chunk:
    chunker: docling-serve
    chunk_size: 512
    chunker_type: hybrid
  embed:
    provider: openai
    model: text-embedding-3-small
    vector_dim: 1536
  store:
    data_dir: custom_db
```

3. **Validate:**

```bash
si-cli validate-settings
si-cli list-param-sets
```

### Via REST API

**Create parameter set:**

```bash
curl -X POST "http://localhost:8000/api/v1/workflow/param-sets" \
  --data-urlencode "yaml_content=$(cat <<'EOF'
id: api_created_params
name: API Created Parameters
config:
  chunk:
    chunker: docling-serve
    chunk_size: 384
  embed:
    provider: ollama
    model: nomic-embed-text
    vector_dim: 768
  store:
    data_dir: api_db
EOF
)"
```

**Response:**
```json
{
  "message": "Parameter set created successfully",
  "id": "api_created_params",
  "file_path": "/path/to/params/api_created_params.yaml"
}
```

### Via Python

```python
import httpx
import yaml

# Define parameter set
params = {
    "id": "python_params",
    "name": "Python Created Parameters",
    "config": {
        "chunk": {
            "chunker": "docling-serve",
            "chunk_size": 256,
        },
        "embed": {
            "provider": "ollama",
            "model": "mxbai-embed-large",
            "vector_dim": 1024,
        },
        "store": {
            "data_dir": "python_db"
        }
    }
}

# Convert to YAML
yaml_content = yaml.dump(params, sort_keys=False)

# Upload via API
async with httpx.AsyncClient() as client:
    response = await client.post(
        "http://localhost:8000/api/v1/workflow/param-sets",
        data={"yaml_content": yaml_content}
    )
    print(response.json())
```

---

## Managing Parameter Sets

### List All Parameter Sets

**CLI:**
```bash
si-cli list-param-sets
```

**REST API:**
```bash
curl "http://localhost:8000/api/v1/workflow/param-sets"
```

**Response:**
```json
[
  {
    "id": "default",
    "name": "Default Parameters",
    "source": "app"
  },
  {
    "id": "my_custom_params",
    "name": "My Custom Configuration",
    "source": "user"
  }
]
```

**Source Types:**
- `app` - Built-in parameter sets (cannot be deleted)
- `user` - User-uploaded parameter sets (can be deleted)

### View Parameter Set

**CLI:**
```bash
si-cli dump-param-set default
```

**REST API:**
```bash
curl "http://localhost:8000/api/v1/workflow/param-sets/default"
```

Returns the raw YAML content.

### Delete Parameter Set

**Only user-uploaded parameter sets can be deleted.**

**REST API:**
```bash
curl -X DELETE "http://localhost:8000/api/v1/workflow/param-sets/my_custom_params"
```

**Response:**
```json
{
  "message": "Parameter set deleted successfully"
}
```

**Error if built-in:**
```json
{
  "error": "Cannot delete built-in parameter sets",
  "status_code": 403
}
```

### Query by Target Database

Find parameter sets that use a specific LanceDB directory:

```bash
curl "http://localhost:8000/api/v1/workflow/param_sets/target/default_db"
```

---

## Examples

### High-Quality Processing

For important documents where quality matters more than speed:

```yaml
id: high_quality
name: High Quality Processing
config:
  parse:
    do_ocr: true
    force_ocr: false
    ocr_engine: easyocr
    pdf_backend: pypdfium2
    table_mode: accurate
  chunk:
    chunker: docling-serve
    chunk_size: 384
    text_context_radius: 100
    chunker_type: hybrid
  embed:
    provider: openai
    model: text-embedding-3-large
    vector_dim: 3072
  store:
    data_dir: high_quality_db
```

### Fast Batch Processing

For large volumes where speed is critical:

```yaml
id: fast_batch
name: Fast Batch Processing
config:
  parse:
    do_ocr: false
    pdf_backend: pypdfium2
    table_mode: fast
  chunk:
    chunker: local
    chunk_size: 512
    text_context_radius: 0
    chunker_type: token
  embed:
    provider: ollama
    model: nomic-embed-text
    vector_dim: 768
  store:
    data_dir: fast_batch_db
```

### OCR-Heavy Documents

For scanned PDFs and images:

```yaml
id: ocr_focused
name: OCR-Focused Processing
config:
  parse:
    do_ocr: true
    force_ocr: true
    ocr_engine: easyocr
    ocr_lang: en
    pdf_backend: pypdfium2
    table_mode: accurate
  chunk:
    chunker: docling-serve
    chunk_size: 256
    text_context_radius: 50
    chunker_type: hierarchical
  embed:
    provider: ollama
    model: nomic-embed-text
    vector_dim: 768
  store:
    data_dir: ocr_docs_db
```

### S3 Storage

For using S3-compatible storage:

```yaml
id: s3_default
name: S3 Storage Configuration
config:
  chunk:
    chunker: docling-serve
    chunk_size: 256
  embed:
    provider: ollama
    model: qwen3-embedding:4b
    vector_dim: 2560
  store:
    data_dir: s3_lancedb
```

**Environment configuration:**
```bash
FILE_STORE_TARGET=s3
S3_ENDPOINT_URL=http://seaweedfs:8333
S3_BUCKET_NAME=soliplex-artifacts
```

### Multilingual Documents

For documents in multiple languages:

```yaml
id: multilingual
name: Multilingual Processing
config:
  parse:
    do_ocr: true
    ocr_engine: easyocr
    ocr_lang: en+es+fr  # Multiple languages
    table_mode: accurate
  chunk:
    chunker: docling-serve
    chunk_size: 384
    chunker_type: hybrid
  embed:
    provider: ollama
    model: qwen3-embedding:4b  # Multilingual model
    vector_dim: 2560
  store:
    data_dir: multilingual_db
```

---

## Best Practices

### Choosing Chunk Size

**Small chunks (128-256 tokens):**
- ✅ More precise search results
- ✅ Better for factual lookup
- ❌ Higher storage costs
- ❌ Less context per chunk

**Medium chunks (256-512 tokens):**
- ✅ Balanced performance (recommended)
- ✅ Good context and precision
- ✅ Reasonable storage costs

**Large chunks (512-1024 tokens):**
- ✅ More context per result
- ✅ Better for document understanding
- ❌ Less precise search
- ❌ May exceed embedding limits

### Selecting Embedding Models

**Consider:**
1. **Vector dimension:** Higher isn't always better
   - More dimensions = more storage, slower search
   - Quality matters more than size

2. **Cost:** OpenAI charges per token
   - Use `text-embedding-3-small` for cost-effectiveness
   - Use Ollama for free local embeddings

3. **Performance:** Test with your documents
   - Different models work better for different content
   - Domain-specific models may outperform general models

4. **Multilingual:** If you need multiple languages
   - `qwen3-embedding` - Excellent multilingual support
   - OpenAI models - Good multilingual coverage

### Parameter Set Versioning

When upgrading models or changing configurations:

1. **Create new parameter set with version suffix:**
   ```yaml
   id: reports_v2
   store:
     data_dir: reports_v2_db
   ```

2. **Keep old parameter sets:**
   - Allows comparison between versions
   - Old databases remain accessible

3. **Document changes:**
   ```yaml
   id: reports_v2
   name: Report Processing v2 (upgraded to text-embedding-3-small)
   ```

### Testing Parameter Sets

Before processing large batches:

1. **Create test parameter set:**
   ```yaml
   id: test_params
   store:
     data_dir: test_db
   ```

2. **Process small sample:**
   ```bash
   # Create test batch with 5-10 documents
   curl -X POST "http://localhost:8000/api/v1/batch/" \
     -d "source=test" -d "name=Parameter Test"

   # Start workflow with test parameters
   curl -X POST "http://localhost:8000/api/v1/batch/start-workflows" \
     -d "batch_id=1" -d "param_id=test_params"
   ```

3. **Evaluate results:**
   - Query precision
   - Processing time
   - Storage size
   - Embedding quality

4. **Iterate and deploy:**
   ```yaml
   id: production_params
   # Copy tested configuration
   ```

### Storage Organization

**Organize databases by use case:**

```yaml
# Financial documents
id: financial_docs
store:
  data_dir: financial_db

# Technical manuals
id: tech_manuals
store:
  data_dir: manuals_db

# Customer support
id: support_docs
store:
  data_dir: support_db
```

**Benefits:**
- Separate vector spaces
- Independent scaling
- Easier maintenance
- Better search relevance

---

## Troubleshooting

### Parameter Set Not Found

**Error:** `404 Not Found` when accessing parameter set

**Solutions:**
1. List available parameter sets:
   ```bash
   si-cli list-param-sets
   ```

2. Check parameter set ID matches exactly (case-sensitive)

3. Verify file exists in `config/params/` or user uploads

### Invalid YAML Syntax

**Error:** `400 Bad Request - Invalid YAML syntax`

**Solutions:**
1. Validate YAML syntax:
   ```bash
   python -c "import yaml; yaml.safe_load(open('params.yaml'))"
   ```

2. Check indentation (use spaces, not tabs)

3. Ensure all strings with special characters are quoted

### Embedding Dimension Mismatch

**Error:** `Embedding dimension mismatch`

**Solutions:**
1. Verify `vector_dim` matches model output:
   - Query model documentation
   - Test embedding generation

2. Common dimensions:
   - `text-embedding-3-small`: 1536
   - `text-embedding-3-large`: 3072
   - `nomic-embed-text`: 768
   - `qwen3-embedding:4b`: 2560

### Cannot Delete Parameter Set

**Error:** `403 Forbidden - Cannot delete built-in parameter sets`

**Solution:**
- Only parameter sets with `source: user` can be deleted
- Built-in parameter sets are protected
- Create a copy if you need to modify:
  ```bash
  # Copy built-in set
  curl "http://localhost:8000/api/v1/workflow/param-sets/default" > my_params.yaml

  # Modify and upload
  # Edit my_params.yaml, change id
  curl -X POST "http://localhost:8000/api/v1/workflow/param-sets" \
    --data-urlencode "yaml_content@my_params.yaml"
  ```

---

## Related Documentation

- **[API Reference](API.md)** - REST API endpoints for parameter management
- **[Workflows](WORKFLOWS.md)** - Using parameter sets in workflows
- **[Configuration](CONFIGURATION.md)** - Environment variables for embedding providers
- **[Getting Started](GETTING_STARTED.md)** - Quick start with parameter sets

---

## Additional Resources

- **HaikuRAG Documentation:** https://github.com/ggozad/haiku.rag
- **Docling Documentation:** https://docling-project.github.io/docling/
- **Ollama Models:** https://ollama.com/library
- **OpenAI Embeddings:** https://platform.openai.com/docs/guides/embeddings
