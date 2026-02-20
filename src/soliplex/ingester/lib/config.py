from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic import model_validator
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


class S3Settings(BaseSettings):
    bucket: str = "default"
    endpoint_url: str = "default"
    access_key_id: str = "default"
    access_secret: SecretStr = "default"
    region: str = "default"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="__", env_nested_max_split=1, secrets_dir="/run/secrets")
    doc_db_url: SecretStr
    doc_db_password: SecretStr | None = None
    docling_server_url: str = "http://localhost:5001/v1"
    docling_chunk_server_url: str = "http://localhost:5001/v1"
    auto_create_database: bool = True
    docling_http_timeout: int = 600
    log_level: str = "INFO"
    file_store_target: str = "fs"
    file_store_dir: str = "file_store"
    lancedb_dir: str = "lancedb"
    document_store_dir: str = "raw"
    parsed_markdown_store_dir: str = "markdown"
    parsed_json_store_dir: str = "json"
    chunks_store_dir: str = "chunks"
    embeddings_store_dir: str = "embeddings"
    stop_phrases: list[str] = []

    ingest_queue_concurrency: int = 20
    ingest_worker_concurrency: int = 10
    docling_concurrency: int = 3
    input_s3: S3Settings = S3Settings()
    artifact_s3: S3Settings = S3Settings()
    workflow_dir: str = "config/workflows"
    default_workflow_id: str = "batch_split"
    param_dir: str = "config/params"
    user_param_dir: str = "config/user_params"
    default_param_id: str = "default"

    @model_validator(mode="after")
    def validate_param_dirs(self) -> "Settings":
        if Path(self.param_dir).resolve() == Path(self.user_param_dir).resolve():
            raise ValueError(
                f"user_param_dir must be different from param_dir, both resolve to '{Path(self.param_dir).resolve()}'"
            )
        return self

    @model_validator(mode="after")
    def substitute_db_password(self) -> "Settings":
        """Substitute XXXX placeholder in doc_db_url with doc_db_password if specified."""
        # Only substitute if password is provided
        if self.doc_db_password is None:
            return self

        doc_db_url_str = self.doc_db_url.get_secret_value()
        doc_db_password_str = self.doc_db_password.get_secret_value()

        # Only substitute if password is non-empty and XXXX placeholder exists in URL
        if doc_db_password_str and "XXXX" in doc_db_url_str:
            updated_url = doc_db_url_str.replace("XXXX", doc_db_password_str)
            self.doc_db_url = SecretStr(updated_url)

        return self

    @model_validator(mode="after")
    def validate_production_auth(self) -> "Settings":
        """Ensure authentication is enabled when in production mode."""
        if self.production_mode and not (self.api_key_enabled or self.auth_trust_proxy_headers):
            raise ValueError(
                "Production mode requires authentication to be enabled. "
                "Set API_KEY_ENABLED=true or AUTH_TRUST_PROXY_HEADERS=true"
            )
        return self

    worker_checkin_interval: int = 120
    worker_checkin_timeout: int = 600
    worker_task_count: int = 5
    embed_batch_size: int = 1000
    ollama_base_url: str = "http://ollama_img:11434"

    do_rag: bool = True  # used for testing to turn off haiku rag

    # Debug settings
    debug: bool = False  # Enable verbose error messages (disable for production)

    # Production settings
    production_mode: bool = False  # Enable production security requirements (mandatory authentication)

    # Authentication settings
    api_key: SecretStr | None = None  # Static API key for programmatic access
    api_key_enabled: bool = False  # Enable API key authentication
    auth_trust_proxy_headers: bool = False  # Trust X-Auth-Request-* headers from OAuth2 Proxy

    # Rate limiting settings
    rate_limit_ingest: str = "200/minute"  # Rate limit for document ingestion endpoint

    # Security middleware settings
    allowed_origins: str = "*"  # CORS allowed origins (comma-separated or "*")
    trusted_hosts: str = "*"  # Trusted hosts for TrustedHostMiddleware (comma-separated or "*")
    enable_hsts: bool = False  # Enable HTTP Strict Transport Security (only use with HTTPS)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
