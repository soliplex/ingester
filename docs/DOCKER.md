# Docker Deployment Guide

This guide covers deploying Soliplex Ingester using Docker Compose for production and development environments. While it can function on its own, it is mostly intended as a guide for creating customized deplolyments to match expected configurations.

## Table of Contents

- [Quick Start](#quick-start)
- [Service Overview](#service-overview)
- [Prerequisites](#prerequisites)
- [Configuration](#configuration)
- [Service Details](#service-details)
- [Authentication Setup](#authentication-setup)
- [Production Deployment](#production-deployment)
- [Monitoring and Maintenance](#monitoring-and-maintenance)
- [Troubleshooting](#troubleshooting)

---

## Quick Start

### Starting Services

1. **Navigate to docker directory:**
```bash
cd docker
```

2. **Start all services:**
```bash
docker-compose up -d
```

3. **Verify services are running:**
```bash
docker-compose ps
```

4. **Access the application:**
   - **Web UI**: http://localhost:8002
   - **API Documentation**: http://localhost:8002/docs
   - **PostgreSQL**: localhost:5432
   - **SeaweedFS**: http://localhost:8333 (S3) / http://localhost:9333 (Admin)

5. **View logs:**
```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f soliplex_ingester
```

6. **Stop services:**
```bash
docker-compose down
```

---

## Service Overview

### Architecture Diagram

```mermaid
graph TB
    subgraph "Docker Network (soliplex_net)"
        Ingester["Soliplex Ingester<br/>:8000→8002<br/>(API + Worker)"]

        Postgres[("PostgreSQL<br/>:5432<br/>(Database)")]

        HAProxy["HAProxy<br/>:5004<br/>(Load Balancer)"]

        Ollama["Ollama<br/>(GPU)<br/>(Embeddings)"]

        Docling1["Docling 1<br/>(GPU)"]
        Docling2["Docling 2<br/>(GPU)"]
        Docling3["Docling 3<br/>(GPU)"]

        SeaweedFS[("SeaweedFS<br/>:8333 (S3)<br/>:9333 (Admin)")]

        Ingester -->|Stores metadata| Postgres
        Ingester -->|Parse requests| HAProxy
        Ingester -->|Embeddings| Ollama
        Ingester -.->|Optional S3| SeaweedFS

        HAProxy -->|Round-robin<br/>Cookie-based| Docling1
        HAProxy -->|Load balanced| Docling2
        HAProxy -->|Load balanced| Docling3
    end

    User([User]) -->|http://localhost:8002| Ingester

    style Ingester fill:#4a90e2,stroke:#2e5c8a,color:#fff
    style Postgres fill:#336791,stroke:#224466,color:#fff
    style HAProxy fill:#3ba13b,stroke:#2d7a2d,color:#fff
    style Ollama fill:#ff6b6b,stroke:#cc5555,color:#fff
    style Docling1 fill:#f39c12,stroke:#c27d0e,color:#fff
    style Docling2 fill:#f39c12,stroke:#c27d0e,color:#fff
    style Docling3 fill:#f39c12,stroke:#c27d0e,color:#fff
    style SeaweedFS fill:#9b59b6,stroke:#7a4691,color:#fff
```

### Required Services

| Service | Purpose | Required |
|---------|---------|----------|
| **soliplex_ingester** | Main application (API + Worker) | Yes |
| **postgres** | Document and workflow database | Yes |

### Optional Services

| Service | Purpose | Required |
|---------|---------|----------|
| **haproxy** | Load balancer for Docling instances | No (but recommended for production) |
| **docling** (1-3) | PDF parsing with GPU acceleration | No (can parse without GPU or use external service) |
| **ollama_img** | Embedding generation with GPU | No (can use external embedding service) |
| **seaweedfs** | S3-compatible object storage | No (can use filesystem or cloud S3) |

### Resource Requirements

**Minimum (Development - No GPU):**
- CPU: 4 cores
- RAM: 8 GB
- Disk: 20 GB

**Recommended (Production with GPU):**
- CPU: 16+ cores
- RAM: 64+ GB
- GPU: NVIDIA GPU with 24+ GB VRAM (for Docling + Ollama)
- Disk: 100+ GB

---

## Prerequisites

### Required Software

1. **Docker Engine 20.10+**
```bash
docker --version
```

2. **Docker Compose 2.0+**
```bash
docker-compose --version
```

### For GPU Support

3. **NVIDIA Container Toolkit**

Install NVIDIA Container Toolkit for GPU support:

**Ubuntu/Debian:**
```bash
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo systemctl restart docker
```

**Verify GPU access:**
```bash
docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu22.04 nvidia-smi
```

---

## Configuration

### Environment Variables

Create a `.env` file in the `docker/` directory:

```bash
# Database Configuration
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_secure_password_here
POSTGRES_DB=soliplex_attrib
DOC_DB_URL=postgresql+psycopg://soliplex_attrib:soliplex_attrib@postgres:5432/soliplex_attrib

# Storage Configuration
FILE_STORE_TARGET=fs                    # Options: fs, db, s3
FILE_STORE_DIR=/var/soliplex/file_store
LANCEDB_DIR=/var/soliplex/lancedb

# Worker Configuration
WORKER_TASK_COUNT=10                    # Number of concurrent workflow steps
INGEST_WORKER_CONCURRENCY=20            # Document ingestion concurrency

# Docling Configuration
DOCLING_SERVER_URL=http://haproxy:5004/v1
DOCLING_HTTP_TIMEOUT=1200               # Timeout in seconds
DOCLING_CONCURRENCY=4                   # Concurrent Docling requests

# Ollama Configuration (if using Ollama for embeddings)
OLLAMA_BASE_URL=http://ollama_img:11434

# SeaweedFS Configuration (if using S3 storage)
S3_ENDPOINT_URL=http://seaweedfs:8333
S3_ACCESS_KEY_ID=your_access_key
S3_SECRET_ACCESS_KEY=your_secret_key
S3_BUCKET_NAME=soliplex-artifacts

# Logging
LOG_LEVEL=INFO                          # Options: DEBUG, INFO, WARNING, ERROR

# API Configuration
API_KEY_ENABLED=false                   # Enable API key authentication
```

See [.env.docker.example](../.env.docker.example) for a complete template.

### Volume Management

The docker-compose configuration creates persistent volumes:

```yaml
volumes:
  postgres_data:        # PostgreSQL database files
  seaweedfs_data:       # SeaweedFS object storage
  docling_artifacts:    # Docling temporary artifacts
  ollama_img_data:      # Ollama model files
```

**Local bind mounts:**
```yaml
./file_store:/var/soliplex/file_store   # Document artifacts
./lancedb:/var/soliplex/lancedb         # Vector database
```

**Backup volumes:**
```bash
# Backup PostgreSQL
docker-compose exec postgres pg_dump -U postgres soliplex_attrib > backup.sql

# Backup volumes
docker run --rm -v postgres_data:/data -v $(pwd):/backup alpine tar czf /backup/postgres_data.tar.gz -C /data .
```

**Restore volumes:**
```bash
docker run --rm -v postgres_data:/data -v $(pwd):/backup alpine sh -c "cd /data && tar xzf /backup/postgres_data.tar.gz"
```

### GPU Configuration

The docker-compose.yml configures GPU access for Docling and Ollama services.

**Important:** Adjust `device_ids` based on your hardware:

```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          device_ids: ['3']  # ← Change this to match your GPU ID
          capabilities: [gpu]
```

**Check available GPUs:**
```bash
nvidia-smi -L
```

**Example output:**
```
GPU 0: NVIDIA A100-SXM4-80GB
GPU 1: NVIDIA A100-SXM4-80GB
GPU 2: NVIDIA A100-SXM4-80GB
GPU 3: NVIDIA A100-SXM4-80GB
```

**Multiple services sharing one GPU:**

The default configuration runs 3 Docling instances + Ollama all on GPU 3. This works if:
- GPU has sufficient VRAM (24+ GB recommended)
- Memory limits are properly configured
- Workload is I/O bound (services wait on data)

**Distribute across GPUs:**
```yaml
# Docling on GPU 0
device_ids: ['0']

# Docling_2 on GPU 1
device_ids: ['1']

# Docling_3 on GPU 2
device_ids: ['2']

# Ollama on GPU 3
device_ids: ['3']
```

### Network Configuration

All services run on the `soliplex_net` bridge network for internal communication.

**Port Mappings:**

| Host Port | Container Port | Service | Purpose |
|-----------|---------------|---------|---------|
| 8002 | 8000 | soliplex_ingester | Web UI & API |
| 5432 | 5432 | postgres | Database |
| 5004 | 5004 | haproxy | Docling load balancer |
| 5000 | 5001 | docling | Direct Docling access |
| 5001 | 5001 | docling_2 | Direct Docling access |
| 8333 | 8333 | seaweedfs | S3 API |
| 9333 | 9333 | seaweedfs | Admin UI |

**Change ports if conflicts exist:**
```yaml
ports:
  - "8080:8000"  # Map to different host port
```

---

## Service Details

### Soliplex Ingester

**Configuration:**
```yaml
soliplex_ingester:
  image: soliplex_ingester:latest
  environment:
    DOC_DB_URL: postgresql+psycopg://soliplex_attrib:soliplex_attrib@postgres:5432/soliplex_attrib
    FILE_STORE_TARGET: fs
    FILE_STORE_DIR: /var/soliplex/file_store
    LANCEDB_DIR: /var/soliplex/lancedb
    WORKER_TASK_COUNT: 10
    DOCLING_SERVER_URL: http://haproxy:5004/v1
    DOCLING_HTTP_TIMEOUT: 1200
    DOCLING_CONCURRENCY: 4
  ports:
    - "8002:8000"
  volumes:
    - ./file_store:/var/soliplex/file_store
    - ./lancedb:/var/soliplex/lancedb
```

**Building the image:**
```bash
# From project root
docker build -t soliplex_ingester:latest .
```

**Scaling workers:**

The default configuration runs the API server with an integrated worker. For production, separate them:

```yaml
# API Server
soliplex_api:
  image: soliplex_ingester:latest
  command: si-cli serve --host 0.0.0.0 --workers 4
  # ... rest of config

# Workers (scale as needed)
soliplex_worker:
  image: soliplex_ingester:latest
  command: si-cli worker
  deploy:
    replicas: 3
  # ... rest of config
```

### PostgreSQL

**Configuration:**
```yaml
postgres:
  image: postgres:18-trixie
  environment:
    POSTGRES_USER: postgres
    POSTGRES_PASSWORD: postgres
    POSTGRES_INITDB_ARGS: "-A scram-sha-256"
  ports:
    - "5432:5432"
  volumes:
    - postgres_data:/var/lib/postgresql
    - ./pgsql/config/init.sql:/docker-entrypoint-initdb.d/init.sql
```

**Initialization Script:**

The `init.sql` script creates users and databases:

```sql
-- Create application user
CREATE USER soliplex_attrib WITH PASSWORD 'soliplex_attrib';

-- Create database
CREATE DATABASE soliplex_attrib OWNER soliplex_attrib;

-- Grant permissions
GRANT ALL PRIVILEGES ON DATABASE soliplex_attrib TO soliplex_attrib;
```

**Production Security:**

⚠️ **The example uses weak passwords for development.** For production:

1. Use strong, randomly generated passwords
2. Store credentials in Docker secrets or environment files
3. Restrict network access
4. Enable SSL/TLS connections

**Connection string format:**
```
postgresql+psycopg://user:password@host:port/database
```

### Docling Services

Docling converts PDF documents to markdown and structured JSON.

**Configuration (per instance):**
```yaml
docling:
  image: ghcr.io/docling-project/docling-serve-cu128
  environment:
    DOCLING_SERVE_ENG_LOC_NUM_WORKERS: 4
    DOCLING_SERVE_ARTIFACTS_PATH: "/artifacts"
    DOCLING_NUM_THREADS: 16
    UVICORN_WORKERS: 1
    PYTORCH_CUDA_ALLOC_CONF: "expandable_segments:True"
    DOCLING_SERVE_ENABLE_UI: 1
    DOCLING_SERVE_MAX_SYNC_WAIT: 9999
    NVIDIA_VISIBLE_DEVICES: "all"
    DOCLING_SERVE_ENABLE_REMOTE_SERVICES: True
  restart: "unless-stopped"
  runtime: "nvidia"
  volumes:
    - docling_artifacts:/artifacts
  deploy:
    resources:
      limits:
        memory: 32000M
      reservations:
        devices:
          - driver: nvidia
            device_ids: ['3']
            capabilities: [gpu]
```

**Key Environment Variables:**

- `DOCLING_SERVE_ENG_LOC_NUM_WORKERS: 4` - Layout analysis workers
- `DOCLING_NUM_THREADS: 16` - Processing threads
- `PYTORCH_CUDA_ALLOC_CONF: "expandable_segments:True"` - GPU memory optimization
- `DOCLING_SERVE_MAX_SYNC_WAIT: 9999` - Long timeout for large documents

**Memory Management:**

⚠️ **Docling is prone to memory leaks** with long-running processes.

**Mitigation strategies:**

1. **Memory limits:** `memory: 32000M` prevents OOM killing other services
2. **Restart policy:** `restart: unless-stopped` recovers from crashes
3. **Load balancing:** Multiple instances with HAProxy provide redundancy
4. **Health checks:** HAProxy routes around unhealthy instances

**Without GPU:**

Remove GPU configuration and use CPU-only image:
```yaml
docling:
  image: ghcr.io/docling-project/docling-serve  # CPU-only
  runtime: null  # Remove nvidia runtime
  deploy:
    resources:
      limits:
        memory: 16000M
      # Remove GPU reservation
```

### HAProxy Load Balancer

HAProxy distributes requests across multiple Docling instances.

**Configuration:**
```yaml
haproxy:
  image: docker.io/library/haproxy:3.3-alpine
  ports:
    - 5004:5004
  volumes:
    - ./haproxy/haproxy.cfg:/usr/local/etc/haproxy/haproxy.cfg
```

**HAProxy Configuration (haproxy/haproxy.cfg):**

```haproxy
global
    maxconn 256

defaults
    mode http
    timeout connect 5000ms
    timeout client 1200000ms
    timeout server 1200000ms

frontend docling_frontend
    bind *:5004
    default_backend docling_backend

backend docling_backend
    balance roundrobin
    cookie SERVERID insert indirect nocache
    server docling1 docling:5001 check cookie docling1
    server docling2 docling_2:5001 check cookie docling2
    server docling3 docling_3:5001 check cookie docling3
```

**Load Balancing Strategy:**

- **Round-robin:** Distributes requests evenly
- **Cookie-based persistence:** Same client goes to same server
- **Health checks:** Removes failed servers from rotation

**Why cookie-based persistence?**

Docling parsing is stateful for multi-step conversions. The ingester client uses cookies to ensure all requests for a document go to the same Docling instance.

### Ollama

Ollama provides embedding generation for vector search.

**Configuration:**
```yaml
ollama_img:
  image: ollama/ollama:latest
  container_name: ollama_img
  volumes:
    - ollama_img_data:/root/.ollama
  restart: always
  deploy:
    resources:
      limits:
        memory: 32000M
      reservations:
        devices:
          - driver: nvidia
            device_ids: ['3']
            capabilities: [gpu]
```

**Pull models:**
```bash
docker-compose exec ollama_img ollama pull nomic-embed-text
docker-compose exec ollama_img ollama pull llama2
```

**List installed models:**
```bash
docker-compose exec ollama_img ollama list
```

**Ingester Configuration:**

Set in `.env`:
```bash
OLLAMA_BASE_URL=http://ollama_img:11434
```

### SeaweedFS

SeaweedFS provides S3-compatible object storage for artifacts.

**Configuration:**
```yaml
seaweedfs:
  image: ghcr.io/chrislusf/seaweedfs
  command: server -s3 -s3.config=/config/config.json
  ports:
    - 8333:8333  # S3 API
    - 9333:9333  # Admin UI
  volumes:
    - seaweedfs_data:/data
    - ./seaweedfs/config:/config
```

**Initialization:**

The `seaweedfs-init` container runs setup:

```yaml
seaweedfs-init:
  image: ghcr.io/chrislusf/seaweedfs
  entrypoint: ["/bin/sh"]
  volumes:
    - ./seaweedfs/config/init.sh:/init.sh
  command: ["/init.sh"]
```

**Example init.sh:**
```bash
#!/bin/sh
# Wait for SeaweedFS to start
sleep 5

# Create bucket
curl -X PUT http://seaweedfs:8333/soliplex-artifacts

# Configure credentials (in config.json)
```

**Ingester Configuration:**

Set in `.env`:
```bash
FILE_STORE_TARGET=s3
S3_ENDPOINT_URL=http://seaweedfs:8333
S3_ACCESS_KEY_ID=your_access_key
S3_SECRET_ACCESS_KEY=your_secret_key
S3_BUCKET_NAME=soliplex-artifacts
```

**Alternative: Use Cloud S3**

```bash
FILE_STORE_TARGET=s3
S3_ENDPOINT_URL=https://s3.amazonaws.com
S3_ACCESS_KEY_ID=your_aws_key
S3_SECRET_ACCESS_KEY=your_aws_secret
S3_BUCKET_NAME=your-bucket-name
AWS_REGION=us-east-1
```

---

## Authentication Setup

For production deployments with authentication, use `docker-compose.auth.yml`.

### OAuth2 Proxy Stack

The auth configuration adds:
- **NGINX** - Reverse proxy with SSL termination
- **OAuth2 Proxy** - OIDC authentication
- **Soliplex Ingester** - Configured to trust proxy headers

**Start with authentication:**
```bash
docker-compose -f docker-compose.yml -f docker-compose.auth.yml up -d
```

### Configuration

1. **Create `.env.auth` file:**

See `docker/.env.auth.example`:

```bash
# OIDC Provider Configuration
OAUTH2_PROVIDER=oidc
OAUTH2_OIDC_ISSUER_URL=https://your-oidc-provider.com
OAUTH2_CLIENT_ID=your_client_id
OAUTH2_CLIENT_SECRET=your_client_secret
OAUTH2_REDIRECT_URL=https://your-domain.com/oauth2/callback

# OAuth2 Proxy Configuration
OAUTH2_COOKIE_SECRET=random_32_char_secret_here
OAUTH2_COOKIE_DOMAIN=your-domain.com

# Soliplex Ingester Configuration
AUTH_TRUST_PROXY_HEADERS=true
AUTH_USER_HEADER=X-Forwarded-User
AUTH_EMAIL_HEADER=X-Forwarded-Email
```

2. **Configure NGINX:**

Edit `docker/nginx/nginx.conf` for your domain and SSL certificates.

3. **Configure OAuth2 Proxy:**

Edit `docker/oauth2-proxy/oauth2-proxy.cfg` for your OIDC provider.

**See [AUTHENTICATION.md](AUTHENTICATION.md) for detailed setup instructions.**

---

## Production Deployment

### Security Best Practices

1. **Use Strong Passwords:**
```bash
# Generate secure password
openssl rand -base64 32
```

2. **Use Docker Secrets:**
```yaml
secrets:
  db_password:
    file: ./secrets/db_password.txt

services:
  postgres:
    secrets:
      - db_password
    environment:
      POSTGRES_PASSWORD_FILE: /run/secrets/db_password
```

3. **Restrict Network Access:**
```yaml
services:
  postgres:
    ports: []  # Don't expose to host
    networks:
      - soliplex_net  # Internal only
```

4. **Enable SSL/TLS:**

Use NGINX with Let's Encrypt certificates or cloud load balancer.

5. **Scan Images:**
```bash
docker scan soliplex_ingester:latest
```

### Scaling Configuration

**For high throughput:**

```yaml
services:
  soliplex_api:
    command: si-cli serve --host 0.0.0.0 --workers 8
    deploy:
      replicas: 2

  soliplex_worker:
    command: si-cli worker
    environment:
      WORKER_TASK_COUNT: 20
    deploy:
      replicas: 5

  docling:
    deploy:
      replicas: 5
      resources:
        limits:
          memory: 24000M
```

### Resource Limits

Set appropriate limits to prevent resource exhaustion:

```yaml
services:
  soliplex_ingester:
    deploy:
      resources:
        limits:
          cpus: '4.0'
          memory: 8G
        reservations:
          cpus: '2.0'
          memory: 4G
```

### Health Checks

Add health checks for automatic recovery:

```yaml
services:
  soliplex_ingester:
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/batch/"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
```

### Logging

**Configure logging drivers:**

```yaml
services:
  soliplex_ingester:
    logging:
      driver: "json-file"
      options:
        max-size: "100m"
        max-file: "5"
```

**Or use external logging:**

```yaml
logging:
  driver: "syslog"
  options:
    syslog-address: "tcp://192.168.0.1:514"
```

---

## Monitoring and Maintenance

### Health Checks

**Check service status:**
```bash
docker-compose ps
```

**View resource usage:**
```bash
docker stats
```

**Check specific service:**
```bash
docker-compose exec soliplex_ingester curl http://localhost:8000/api/v1/stats/durations
```

### Log Management

**View logs:**
```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f soliplex_ingester

# Last 100 lines
docker-compose logs --tail=100 docling

# Since timestamp
docker-compose logs --since 2026-01-22T10:00:00
```

**Export logs:**
```bash
docker-compose logs > logs.txt
```

### Database Maintenance

**Backup:**
```bash
docker-compose exec postgres pg_dump -U postgres soliplex_attrib | gzip > backup_$(date +%Y%m%d).sql.gz
```

**Restore:**
```bash
gunzip < backup_20260122.sql.gz | docker-compose exec -T postgres psql -U postgres soliplex_attrib
```

**Vacuum database:**
```bash
docker-compose exec postgres psql -U postgres -d soliplex_attrib -c "VACUUM ANALYZE;"
```

### Vector Database Maintenance

**Vacuum LanceDB:**
```bash
curl "http://localhost:8002/api/v1/lancedb/vacuum?db=default"
```

**Check database size:**
```bash
docker-compose exec soliplex_ingester du -sh /var/soliplex/lancedb
```

### Updates

**Update images:**
```bash
docker-compose pull
docker-compose up -d
```

**Update single service:**
```bash
docker-compose pull soliplex_ingester
docker-compose up -d soliplex_ingester
```

**Rebuild custom image:**
```bash
docker build -t soliplex_ingester:latest .
docker-compose up -d soliplex_ingester
```

---

## Troubleshooting

### Common Issues

#### Services Won't Start

**Check logs:**
```bash
docker-compose logs <service_name>
```

**Check configuration:**
```bash
docker-compose config
```

**Validate environment variables:**
```bash
docker-compose exec soliplex_ingester env | grep DOC_DB_URL
```

#### Database Connection Errors

**Error:** `connection to server at "postgres" (172.18.0.2), port 5432 failed`

**Solutions:**
1. Verify postgres is running: `docker-compose ps postgres`
2. Check network: `docker-compose exec soliplex_ingester ping postgres`
3. Verify credentials in `DOC_DB_URL`
4. Check postgres logs: `docker-compose logs postgres`

**Test connection:**
```bash
docker-compose exec soliplex_ingester psql "postgresql://soliplex_attrib:soliplex_attrib@postgres:5432/soliplex_attrib"
```

#### GPU Not Available

**Error:** `could not select device driver "" with capabilities: [[gpu]]`

**Solutions:**
1. Install NVIDIA Container Toolkit (see [Prerequisites](#prerequisites))
2. Restart Docker daemon: `sudo systemctl restart docker`
3. Verify GPU access: `docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu22.04 nvidia-smi`

**Check GPU usage:**
```bash
docker-compose exec docling nvidia-smi
```

#### Docling Memory Errors

**Error:** `CUDA out of memory` or container crashes

**Solutions:**
1. Reduce `DOCLING_NUM_THREADS` in docker-compose.yml
2. Reduce `DOCLING_SERVE_ENG_LOC_NUM_WORKERS`
3. Increase GPU memory by using dedicated GPU per instance
4. Process smaller documents or reduce concurrency

**Monitor memory:**
```bash
docker stats docling
```

#### Port Conflicts

**Error:** `bind: address already in use`

**Solutions:**
1. Check what's using the port:
```bash
# Linux/macOS
lsof -i :8002

# Windows
netstat -ano | findstr :8002
```

2. Change port mapping in docker-compose.yml:
```yaml
ports:
  - "8080:8000"  # Use different host port
```

#### Volume Permission Errors

**Error:** `permission denied` when accessing volumes

**Solutions:**
1. Check ownership:
```bash
ls -la ./file_store ./lancedb
```

2. Fix permissions:
```bash
sudo chown -R $(id -u):$(id -g) ./file_store ./lancedb
```

3. Or use Docker's user mapping:
```yaml
user: "${UID}:${GID}"
```

#### SeaweedFS Connection Errors

**Error:** `Unable to connect to S3 endpoint`

**Solutions:**
1. Verify SeaweedFS is running: `docker-compose ps seaweedfs`
2. Check initialization completed: `docker-compose logs seaweedfs-init`
3. Test S3 endpoint:
```bash
curl http://localhost:8333
```

4. Verify bucket exists:
```bash
docker-compose exec seaweedfs weed shell
> s3.bucket.list
```

#### HAProxy Routing Issues

**Symptom:** Requests timing out or routing to unhealthy Docling instances

**Debug:**
1. Check HAProxy stats (if enabled):
```
http://localhost:5004/stats
```

2. View HAProxy logs:
```bash
docker-compose logs haproxy
```

3. Test Docling instances directly:
```bash
curl http://localhost:5000/v1/health
curl http://localhost:5001/v1/health
```

4. Restart HAProxy:
```bash
docker-compose restart haproxy
```

### Performance Issues

#### Slow Document Processing

**Diagnose:**
```bash
# Check worker activity
curl http://localhost:8002/api/v1/workflow/steps?status=RUNNING

# Check CPU/memory
docker stats

# Check Docling queue
docker-compose logs docling | grep -i queue
```

**Solutions:**
1. Increase worker concurrency: `WORKER_TASK_COUNT=20`
2. Add more Docling instances
3. Increase `DOCLING_CONCURRENCY`
4. Scale workers: `docker-compose up -d --scale soliplex_worker=5`

#### High Memory Usage

**Monitor:**
```bash
docker stats --no-stream
```

**Solutions:**
1. Reduce concurrency settings
2. Increase memory limits
3. Add swap space (not recommended for production)
4. Use smaller batch sizes

#### Disk Space Issues

**Check usage:**
```bash
docker system df
```

**Clean up:**
```bash
# Remove unused images
docker image prune -a

# Remove stopped containers
docker container prune

# Remove unused volumes
docker volume prune

# Clean everything
docker system prune -a --volumes
```

### Recovery Procedures

#### Reset Everything

**⚠️ This deletes all data!**

```bash
docker-compose down -v
rm -rf ./file_store/* ./lancedb/*
docker-compose up -d
```

#### Reset Database Only

```bash
docker-compose down
docker volume rm docker_postgres_data
docker-compose up -d postgres
# Wait for postgres to initialize
docker-compose exec postgres psql -U postgres -f /docker-entrypoint-initdb.d/init.sql
docker-compose up -d
```

#### Restart Single Service

```bash
docker-compose restart soliplex_ingester
```

#### Force Recreate Service

```bash
docker-compose up -d --force-recreate soliplex_ingester
```

---

## Additional Resources

- **Soliplex Ingester Documentation:** [../README.md](../README.md)
- **API Reference:** [API.md](API.md)
- **Configuration Guide:** [CONFIGURATION.md](CONFIGURATION.md)
- **Authentication Guide:** [AUTHENTICATION.md](AUTHENTICATION.md)
- **Docling Documentation:** https://docling-project.github.io/docling/
- **Docker Compose Documentation:** https://docs.docker.com/compose/
- **NVIDIA Container Toolkit:** https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/

---

## Getting Help

If you encounter issues not covered in this guide:

1. Check the [main troubleshooting guide](../README.md#troubleshooting)
2. Review service-specific logs
3. Open an issue on GitHub with:
   - Output of `docker-compose ps`
   - Relevant logs from `docker-compose logs`
   - Your docker-compose.yml modifications
   - Environment details (OS, Docker version, GPU info)
