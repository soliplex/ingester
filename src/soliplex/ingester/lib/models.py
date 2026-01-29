import datetime
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import Enum
from typing import TypeVar

from pydantic import BaseModel
from pydantic import ImportString
from pydantic import computed_field
from sqlalchemy import JSON
from sqlalchemy import Column
from sqlalchemy import UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import Field
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession


class Database:
    """
    Database manager that handles engine lifecycle and session creation.

    Connection pooling is handled automatically by SQLAlchemy's engine.

    Usage:
        # Initialize once at application startup
        await Database.initialize()

        # Or with custom URL (for testing)
        await Database.initialize("sqlite+aiosqlite:///:memory:")

        # Get sessions anywhere in the app
        async with get_session() as session:
            ...

        # Cleanup at shutdown (optional but recommended)
        await Database.close()
    """

    _engine: AsyncEngine | None = None
    _initialized: bool = False

    @classmethod
    async def initialize(cls, url: str | None = None) -> None:
        """
        Initialize the database engine and create tables.

        Safe to call multiple times - will only initialize once unless reset() is called.

        Args:
            url: Database URL. If None, reads from settings.
        """
        if cls._initialized:
            return

        if url is None:
            from soliplex.ingester.lib.config import get_settings

            url = get_settings().doc_db_url

        connect_args = {}
        if "sqlite" in url:
            connect_args["check_same_thread"] = False

        cls._engine = create_async_engine(url, connect_args=connect_args)

        # Create all tables
        async with cls._engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        cls._initialized = True

    @classmethod
    def engine(cls) -> AsyncEngine:
        """Get the database engine. Raises if not initialized."""
        if cls._engine is None:
            raise RuntimeError("Database not initialized. Call 'await Database.initialize()' first.")
        return cls._engine

    @classmethod
    @asynccontextmanager
    async def session(cls) -> AsyncIterator[AsyncSession]:
        """Create a database session with automatic transaction management."""
        # Auto-initialize if needed (preserves backwards compatibility)
        if not cls._initialized:
            await cls.initialize()

        async with AsyncSession(cls._engine) as session:
            try:
                async with session.begin():
                    yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    @classmethod
    async def close(cls) -> None:
        """Close the database engine and release all connections."""
        if cls._engine is not None:
            await cls._engine.dispose()
            cls._engine = None
            cls._initialized = False

    @classmethod
    async def reset(cls, url: str | None = None) -> None:
        """
        Reset and reinitialize the database (primarily for testing).

        Args:
            url: New database URL. If None, reads from settings.
        """
        await cls.close()
        await cls.initialize(url)


# Backwards-compatible module-level functions
async def get_engine() -> AsyncEngine:
    """Get the database engine (backwards compatible)."""
    if not Database._initialized:
        await Database.initialize()
    return Database.engine()


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Get a database session (backwards compatible)."""
    async with Database.session() as session:
        yield session


def doc_hash(data: bytes) -> str:
    hasher = hashlib.sha256(usedforsecurity=False)
    hasher.update(data)
    hex_digest = hasher.hexdigest()
    return f"sha256-{hex_digest}"


class DocumentDB(SQLModel, table=True):
    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
    )
    doc_hash: str = Field(
        default=None,
        foreign_key="document.hash",
    )
    source: str = Field(default=None)
    db_name: str = Field(default=None)
    lancedb_dir: str = Field(default=None)
    rag_id: str | None = Field(default=None)
    chunk_count: int | None = Field(default=None)
    created_date: datetime.datetime = Field(default=None)


class DocumentBatch(SQLModel, table=True):
    """
    A batch of documents to be ingested
    """

    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
    )
    name: str = Field(default=None)
    source: str = Field(default=None)
    start_date: datetime.datetime = Field(default=None)
    completed_date: datetime.datetime = Field(nullable=True)
    batch_params: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))

    @computed_field
    @property
    def duration(self) -> float:
        if self.completed_date is None:
            return self.completed_date
        return (self.completed_date - self.start_date).total_seconds()


class DocumentURI(SQLModel, table=True):
    """
    A URI for a document that maps to the identifier/path on the source system.
    multiple documents may map to the same URI if they are identical
    """

    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
    )
    doc_hash: str = Field(
        default=None,
        foreign_key="document.hash",
    )
    uri: str = Field(default=None)
    source: str = Field(default=None)
    version: int = Field(default=1)
    batch_id: int = Field(nullable=True, foreign_key="documentbatch.id")
    __table_args__ = (UniqueConstraint("uri", "source", name="unq_uri_source"),)


class Document(SQLModel, table=True):
    hash: str = Field(default=None, primary_key=True)
    mime_type: str = Field(default=None)
    file_size: int = Field(nullable=True)
    doc_meta: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))

    def __init__(self, **kwargs):
        if "doc_meta" in kwargs:
            if isinstance(kwargs["doc_meta"], str):
                kwargs["doc_meta"] = json.loads(kwargs["doc_meta"])

        super().__init__(**kwargs)


class ArtifactType(Enum):
    DOC = "document"
    PARSED_MD = "parsed_markdown"
    PARSED_JSON = "parsed_json"
    CHUNKS = "chunks"
    EMBEDDINGS = "embeddings"
    RAG = "rag"


class WorkflowStepType(str, Enum):
    INGEST: str = "ingest"
    VALIDATE: str = "validate"
    PARSE: str = "parse"
    CHUNK: str = "chunk"
    EMBED: str = "embed"
    STORE: str = "store"
    ENRICH: str = "enrich"
    ROUTE: str = "route"


ARTIFACTS_FROM_STEPS = {
    WorkflowStepType.INGEST: [ArtifactType.DOC],
    WorkflowStepType.PARSE: [ArtifactType.PARSED_MD, ArtifactType.PARSED_JSON],
    WorkflowStepType.CHUNK: [ArtifactType.CHUNKS],
    WorkflowStepType.EMBED: [ArtifactType.EMBEDDINGS],
    WorkflowStepType.STORE: [ArtifactType.RAG],
}

ARTIFACTS_TO_STEPS = {
    ArtifactType.DOC: WorkflowStepType.INGEST,
    ArtifactType.PARSED_MD: WorkflowStepType.PARSE,
    ArtifactType.PARSED_JSON: WorkflowStepType.PARSE,
    ArtifactType.CHUNKS: WorkflowStepType.CHUNK,
    ArtifactType.EMBEDDINGS: WorkflowStepType.EMBED,
    ArtifactType.RAG: WorkflowStepType.STORE,
}


class LifeCycleEvent(str, Enum):
    GROUP_START: str = "group_start"
    GROUP_END: str = "group_end"
    ITEM_START: str = "item_start"
    ITEM_END: str = "item_end"
    ITEM_FAILED: str = "item_failed"
    STEP_START: str = "step_start"
    STEP_END: str = "step_end"
    STEP_FAILED: str = "step_failed"


class DocumentBytes(SQLModel, table=True):
    hash: str = Field(default=None, primary_key=True)
    artifact_type: str = Field(default=None, primary_key=True)
    storage_root: str = Field(default=None, primary_key=True)
    file_size: int = Field(nullable=True)
    file_bytes: bytes = Field(nullable=False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.file_size is None:
            self.file_size = len(self.file_bytes)


class DocumentURIHistory(SQLModel, table=True):
    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
    )
    doc_uri_id: int = Field(default=None, foreign_key="documenturi.id")
    version: int = Field(default=None)
    hash: str = Field(default=None)
    process_date: datetime.datetime = Field(default=None)
    action: str = Field(default=None)
    batch_id: int = Field(nullable=True, foreign_key="documentbatch.id")
    histmeta: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))


# ----------------- workflow related models ----------------------


class RunStatus(str, Enum):
    PENDING = "PENDING"  # hasn't started
    RUNNING = "RUNNING"  # currently running
    COMPLETED = "COMPLETED"  # success
    ERROR = "ERROR"  # failed but stil retrying
    FAILED = "FAILED"  # gave up


class RunGroup(SQLModel, table=True):
    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
    )
    name: str | None = Field(default=None, nullable=True)
    workflow_definition_id: str = Field(default=None)
    param_definition_id: str = Field(default=None)
    batch_id: int | None = Field(default=None, foreign_key="documentbatch.id", nullable=True)
    created_date: datetime.datetime = Field(default=None, allow_mutation=False)
    start_date: datetime.datetime = Field(default=None)
    completed_date: datetime.datetime | None = Field(nullable=True)
    status: RunStatus = Field(default=RunStatus.PENDING)
    status_date: datetime.datetime = Field(nullable=True)
    status_message: str = Field(default=None, nullable=True)
    status_meta: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))


class LifecycleHistory(SQLModel, table=True):
    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
    )
    event: LifeCycleEvent = Field(default=None)
    handler_name: str | None = Field(default=None, nullable=True)
    run_group_id: int = Field(default=None, foreign_key="rungroup.id")
    workflow_run_id: int = Field(default=None, foreign_key="workflowrun.id")
    step_id: int | None = Field(default=None, nullable=True)
    start_date: datetime.datetime = Field(default=None)
    completed_date: datetime.datetime | None = Field(nullable=True)

    status: RunStatus = Field(default=RunStatus.PENDING)
    status_date: datetime.datetime = Field(nullable=True)
    status_message: str = Field(default=None, nullable=True)
    status_meta: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))


class WorkflowRun(SQLModel, table=True):
    """
    a single instance of a workflow that processes one item
    """

    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
    )
    workflow_definition_id: str = Field(default=None)
    run_group_id: int = Field(default=None, foreign_key="rungroup.id")
    batch_id: int = Field(default=None, foreign_key="documentbatch.id")
    doc_id: str = Field(default=None, allow_mutation=False)
    priority: int = Field(default=0)
    created_date: datetime.datetime = Field(default=None, allow_mutation=False)
    start_date: datetime.datetime = Field(default=None)
    completed_date: datetime.datetime = Field(nullable=True)
    status: RunStatus = Field(default=RunStatus.PENDING)
    status_date: datetime.datetime = Field(nullable=True)
    status_message: str = Field(default=None, nullable=True)
    status_meta: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    run_params: dict[str, str | int | bool] = Field(default_factory=dict, sa_column=Column(JSON))

    @computed_field
    @property
    def duration(self) -> float:
        if self.completed_date is None:
            return self.completed_date
        return (self.completed_date - self.start_date).total_seconds()

    # __table_args__ = (
    #     UniqueConstraint(
    #         "workflow_definition_id", "batch_id", "doc_id",
    #         name="unq_run"
    #     ),
    # )


class RunStep(SQLModel, table=True):
    """
    a step in a workflow run
    """

    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
    )
    workflow_run_id: int = Field(default=None, foreign_key="workflowrun.id")
    workflow_step_number: int = Field(default=None, allow_mutation=False)
    workflow_step_name: str = Field(default=None, allow_mutation=False)
    step_config_id: int = Field(default=None, foreign_key="stepconfig.id")
    step_type: WorkflowStepType = Field(default=None, allow_mutation=False)
    is_last_step: bool = Field(default=False, allow_mutation=False)
    created_date: datetime.datetime = Field(default=None)
    priority: int = Field(default=0)
    start_date: datetime.datetime = Field(nullable=True)
    status_date: datetime.datetime = Field(nullable=True)
    completed_date: datetime.datetime = Field(nullable=True)
    retry: int = Field(default=0)
    retries: int = Field(default=1)
    status: RunStatus = Field(default=RunStatus.PENDING)
    status_message: str = Field(default=None, nullable=True)
    status_meta: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    worker_id: str = Field(default=None, nullable=True)

    @computed_field
    @property
    def duration(self) -> float:
        if self.completed_date is None:
            return self.completed_date
        return (self.completed_date - self.start_date).total_seconds()


class WorkerCheckin(SQLModel, table=True):
    id: str = Field(default=None, primary_key=True)
    first_checkin: datetime.datetime = Field(default=None)
    last_checkin: datetime.datetime = Field(default=None)
    __table_args__ = (UniqueConstraint("id", name="unq_worker"),)


class StepConfig(SQLModel, table=True):
    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
    )

    created_date: datetime.datetime | None = Field(default=None, allow_mutation=False)
    step_type: WorkflowStepType = Field(default=None, allow_mutation=False)
    config_json: dict[str, str | int | bool] | None = Field(
        default=None, sa_column=Column(JSON), allow_mutation=False
    )  # config for this step
    cuml_config_json: str | None = Field(default=None, allow_mutation=False)  # config for all previous steps
    # __table_args__ = (
    #     UniqueConstraint(
    #         "config_json", "step_type",
    #         name="unq_rag_config_step"
    #     ),
    # )


class ConfigSet(SQLModel, table=True):
    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
    )
    yaml_id: str = Field(default=None, allow_mutation=False)
    yaml_contents: str = Field(default=None, allow_mutation=False)
    created_date: datetime.datetime | None = Field(default=None, allow_mutation=False)


class ConfigSetItem(SQLModel, table=True):
    config_set_id: int = Field(
        default=None,
        foreign_key="configset.id",
        nullable=False,
        primary_key=True,
    )
    config_id: int = Field(
        default=None,
        foreign_key="stepconfig.id",
        nullable=False,
        primary_key=True,
    )
    # __table_args__ = (
    #     UniqueConstraint(
    #         "config_set_id", "config_id",
    #         name="unq_config_set_item"
    #     ),
    # )


# ---------------------- models used from yaml config, etc  ---------------------


class EventHandler(BaseModel):
    name: str
    retries: int = 1
    method: ImportString
    parameters: dict[str, str | int | float | bool]


class WorkflowDefinition(BaseModel):
    id: str
    name: str
    meta: dict[str, str]
    item_steps: dict[WorkflowStepType, EventHandler]
    lifecycle_events: dict[LifeCycleEvent, list[EventHandler]] | None


class WorkflowParams(BaseModel):
    id: str
    name: str | None = None
    meta: dict[str, str] | None = None
    config: dict[WorkflowStepType, dict[str, str | int | float | bool]]
    source: str = "app"


class DocumentInfo(BaseModel):
    """Document information for API responses"""

    uri: str | None = None
    source: str | None = None
    file_size: int | None = None
    mime_type: str | None = None


class WorkflowRunWithDetails(BaseModel):
    """Response model for workflow run with optional steps and document info"""

    workflow_run: WorkflowRun
    steps: list[RunStep] | None = None
    document_info: DocumentInfo | None = None


# Alias for backward compatibility
WorkflowRunWithSteps = WorkflowRunWithDetails


# Type variable for generic pagination
T = TypeVar("T")


class PaginatedResponse[T](BaseModel):
    """Generic paginated response model"""

    items: list[T]
    total: int
    page: int
    rows_per_page: int
    total_pages: int
