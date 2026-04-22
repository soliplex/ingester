import json
import logging
import logging.handlers
import os
import time
from datetime import UTC
from datetime import datetime
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic import model_validator
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

logger = logging.getLogger(__name__)


class ProtectionLevel(StrEnum):
    """Controls integrity/confidentiality protection for file-stored artifacts."""

    NONE = "none"
    HASH = "hash"
    HMAC = "hmac"
    ENCRYPT = "encrypt"


class LLMProvider(StrEnum):
    OPENAI = "openai"
    OLLAMA = "ollama"


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
    log_format: str = "{name}|{asctime}|{levelname}|{message}"

    # SMTP email alert settings (handler only added when smtp_host is set)
    smtp_host: str | None = None
    smtp_port: int = 25
    smtp_from: str | None = None
    smtp_to: list[str] | None = None
    smtp_subject: str = "Soliplex Ingester Log Alert"
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_use_tls: bool = False
    smtp_log_level: str = "ERROR"
    smtp_cooldown: int = 30
    file_store_target: str = "fs"
    file_store_dir: str = "file_store"
    file_protection_level: ProtectionLevel = ProtectionLevel.NONE
    file_secret: SecretStr | None = None
    lancedb_dir: str = "lancedb"
    lancedb_hmac_key: SecretStr | None = None
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

    @model_validator(mode="after")
    def export_openai_api_key(self) -> "Settings":
        """Export openai_api_key to os.environ if not already set in the environment.

        This lets downstream code (haiku.rag, OpenAI SDK) that reads
        OPENAI_API_KEY directly from the process env pick up the value
        whether it was supplied via env var or via /run/secrets/openai_api_key.
        An existing OPENAI_API_KEY env var is authoritative and never overwritten.
        """
        if self.openai_api_key is None:
            return self
        if os.environ.get("OPENAI_API_KEY"):
            return self
        os.environ["OPENAI_API_KEY"] = self.openai_api_key.get_secret_value()
        return self

    @model_validator(mode="after")
    def validate_file_protection(self) -> "Settings":
        """Ensure FILE_SECRET is set when protection level requires it."""
        if self.file_protection_level in (ProtectionLevel.HMAC, ProtectionLevel.ENCRYPT):
            if not self.file_secret or not self.file_secret.get_secret_value():
                raise ValueError(f"FILE_SECRET is required when FILE_PROTECTION_LEVEL={self.file_protection_level.value}")
        return self

    worker_checkin_interval: int = 120
    worker_checkin_timeout: int = 600
    worker_task_count: int = 5
    embed_batch_size: int = 1000
    ollama_base_url: str | None = "http://ollama:11434"
    # if None, use ollama_base_url, otherwise use this. if vllm append /v1 due to limitations in haiku
    embed_llm_url: str | None = None

    do_rag: bool = True  # used for testing to turn off haiku rag

    # Debug settings
    debug: bool = False  # Enable verbose error messages (disable for production)

    # Production settings
    production_mode: bool = False  # Enable production security requirements (mandatory authentication)

    # Optional OpenAI API key. When set (directly or via /run/secrets/openai_api_key),
    # exported to os.environ["OPENAI_API_KEY"] so haiku.rag / OpenAI SDK can pick it up.
    openai_api_key: SecretStr | None = None

    # Authentication settings
    api_key: SecretStr | None = None  # Static API key for programmatic access
    api_key_enabled: bool = False  # Enable API key authentication
    auth_trust_proxy_headers: bool = False  # Trust X-Auth-Request-* headers from OAuth2 Proxy

    # Rate limiting settings
    rate_limit_ingest: str = "1000/minute"  # Rate limit for document ingestion endpoint

    # Security middleware settings
    allowed_origins: str = "*"  # CORS allowed origins (comma-separated or "*")
    trusted_hosts: str = "*"  # Trusted hosts for TrustedHostMiddleware (comma-separated or "*")
    enable_hsts: bool = False  # Enable HTTP Strict Transport Security (only use with HTTPS)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


class JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON object."""

    _BUILTIN_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)))

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S")
        obj: dict = {
            "timestamp": ts,
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            obj["exception"] = self.formatException(record.exc_info)
        for key, val in record.__dict__.items():
            if key not in self._BUILTIN_ATTRS:
                obj[key] = val
        return json.dumps(obj, default=str)


class _ThrottledSMTPHandler(logging.handlers.SMTPHandler):
    """SMTPHandler that suppresses emails within a cooldown period."""

    def __init__(self, *args, cooldown: int = 30, **kwargs):
        super().__init__(*args, **kwargs)
        self._cooldown = cooldown
        self._last_emit: float = 0

    def emit(self, record):
        now = time.monotonic()
        if now - self._last_emit < self._cooldown:
            return
        self._last_emit = now
        super().emit(record)


def _add_smtp_handler(settings: Settings) -> None:
    """Attach an SMTPHandler to the root logger if SMTP settings are configured."""
    if not (settings.smtp_host and settings.smtp_from and settings.smtp_to):
        return
    try:
        credentials = None
        if settings.smtp_username and settings.smtp_password:
            credentials = (
                settings.smtp_username,
                settings.smtp_password.get_secret_value(),
            )
        secure = () if settings.smtp_use_tls else None
        handler = _ThrottledSMTPHandler(
            mailhost=(settings.smtp_host, settings.smtp_port),
            fromaddr=settings.smtp_from,
            toaddrs=settings.smtp_to,
            subject=settings.smtp_subject,
            credentials=credentials,
            secure=secure,
            cooldown=settings.smtp_cooldown,
        )
        handler.setLevel(settings.smtp_log_level)
        if settings.log_format == "json":
            handler.setFormatter(JsonFormatter())
        else:
            handler.setFormatter(
                logging.Formatter(
                    fmt=settings.log_format,
                    datefmt="%Y-%m-%dT%H:%M:%S",
                    style="{",
                )
            )
        logging.getLogger().addHandler(handler)
    except Exception:
        logger.warning(
            "Failed to configure SMTP log handler",
            exc_info=True,
        )


def configure_logging(settings: Settings) -> None:
    """Configure the root logger from *settings*.

    When ``settings.log_format`` equals ``"json"``, a
    `JsonFormatter` is installed; otherwise the value is
    used as a ``str.format``-style pattern.
    """
    root = logging.getLogger()
    root.setLevel(settings.log_level)

    handler = logging.StreamHandler()
    if settings.log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                fmt=settings.log_format,
                datefmt="%Y-%m-%dT%H:%M:%S",
                style="{",
            )
        )

    root.handlers.clear()
    root.addHandler(handler)
    _add_smtp_handler(settings)
