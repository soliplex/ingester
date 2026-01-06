import datetime

import pytest

from soliplex.ingester.lib.models import Database
from soliplex.ingester.lib.models import Document
from soliplex.ingester.lib.models import DocumentBatch
from soliplex.ingester.lib.models import DocumentURIHistory
from soliplex.ingester.lib.models import RunStep
from soliplex.ingester.lib.models import WorkflowRun
from soliplex.ingester.lib.models import doc_hash
from soliplex.ingester.lib.models import get_engine
from soliplex.ingester.lib.models import get_session


def test_get_session():
    session = get_session()
    assert session is not None


@pytest.mark.asyncio
async def test_database():
    db = Database()
    url = "sqlite+aiosqlite:///:memory:"
    assert db is not None
    await db.initialize(url)
    assert db._engine is not None


@pytest.mark.asyncio
async def test_database_env():
    db = Database()
    # passing none should use env
    url = None
    assert db is not None
    await db.initialize(url)
    assert db._engine is not None


@pytest.mark.asyncio
async def test_no_init():
    await Database.close()
    Database._initialized = False
    Database._engine = None
    db = Database()
    with pytest.raises(RuntimeError, match="Database not initialized"):
        db.engine()


@pytest.mark.asyncio
async def test_session_init():
    await Database.close()
    Database._initialized = False
    Database._engine = None
    async with get_session() as ses:
        assert ses
    assert Database._initialized


@pytest.mark.asyncio
async def test_engine_init():
    await Database.close()
    Database._initialized = False
    Database._engine = None
    await get_engine()
    assert Database._initialized


@pytest.mark.asyncio
async def test_database_reinit():
    url = "sqlite+aiosqlite:///:memory:"
    url2 = "invalid"
    db = Database()
    assert db is not None
    await db.initialize(url)
    e1 = db._engine
    # calling initialize again should be a noop
    await db.initialize(url2)
    e2 = db._engine
    assert e1 is e2


@pytest.mark.asyncio
async def test_database_reset():
    url = "sqlite+aiosqlite:///:memory:"
    url2 = "sqlite+aiosqlite:///:memory:"
    db = Database()
    assert db is not None
    await db.initialize(url)
    e1 = db._engine
    # reset should create a new engine even if the url is the same
    await db.reset(url2)
    e2 = db._engine
    assert e1 is not e2


def test_document_init():
    doc = Document(uri="test.txt", hash="test-hash")

    assert doc.hash is not None


def test_document_bytes_init_with_file_bytes():
    """Test DocumentBytes init with file_bytes to cover line 175"""
    from soliplex.ingester.lib.models import ArtifactType
    from soliplex.ingester.lib.models import DocumentBytes

    doc_bytes = DocumentBytes(
        hash="test-hash",
        artifact_type=ArtifactType.DOC.value,
        storage_root="test",
        file_bytes=b"test data",
        file_size=None,
    )
    assert doc_bytes.file_size == 9


def test_document_history_init():
    doc_history = DocumentURIHistory(
        doc_uri_id=0,
        version=1,
        process_date=datetime.date.today(),
        hash="test-hash",
    )

    assert doc_history.doc_uri_id == 0
    assert doc_history.version == 1
    assert doc_history.hash is not None
    assert doc_history.process_date is not None


def test_doc_hash():
    """Test doc_hash function"""
    data = b"test data"
    hash_result = doc_hash(data)
    assert hash_result.startswith("sha256-")
    assert len(hash_result) == 71


def test_document_batch_duration_with_completed_date():
    """Test DocumentBatch duration property when completed_date is set"""
    start = datetime.datetime(2024, 1, 1, 10, 0, 0)
    completed = datetime.datetime(2024, 1, 1, 10, 5, 0)
    batch = DocumentBatch(name="test", source="test", start_date=start, completed_date=completed)
    assert batch.duration == 300.0


def test_document_batch_duration_without_completed_date():
    """Test DocumentBatch duration property when completed_date is None (lines 77-79)"""
    start = datetime.datetime(2024, 1, 1, 10, 0, 0)
    batch = DocumentBatch(name="test", source="test", start_date=start, completed_date=None)
    assert batch.duration is None


def test_workflow_run_duration_with_completed_date():
    """Test WorkflowRun duration property when completed_date is set"""
    start = datetime.datetime(2024, 1, 1, 10, 0, 0)
    completed = datetime.datetime(2024, 1, 1, 10, 5, 0)
    run = WorkflowRun(
        workflow_definition_id="test",
        batch_id=1,
        doc_id="doc1",
        run_group_id=1,
        start_date=start,
        completed_date=completed,
    )
    assert run.duration == 300.0


def test_workflow_run_duration_without_completed_date():
    """Test WorkflowRun duration property when completed_date is None"""
    start = datetime.datetime(2024, 1, 1, 10, 0, 0)
    run = WorkflowRun(
        workflow_definition_id="test",
        batch_id=1,
        doc_id="doc1",
        run_group_id=1,
        start_date=start,
        completed_date=None,
    )
    assert run.duration is None


def test_run_step_duration_with_completed_date():
    """Test RunStep duration property when completed_date is set"""
    from soliplex.ingester.lib.models import RunStatus
    from soliplex.ingester.lib.models import WorkflowStepType

    start = datetime.datetime(2024, 1, 1, 10, 0, 0)
    completed = datetime.datetime(2024, 1, 1, 10, 5, 0)
    step = RunStep(
        workflow_run_id=1,
        workflow_step_number=1,
        workflow_step_name="test",
        step_type=WorkflowStepType.INGEST,
        status=RunStatus.COMPLETED,
        step_config_id=1,
        start_date=start,
        completed_date=completed,
    )
    assert step.duration == 300.0


def test_run_step_duration_without_completed_date():
    """Test RunStep duration property when completed_date is None"""
    from soliplex.ingester.lib.models import RunStatus
    from soliplex.ingester.lib.models import WorkflowStepType

    start = datetime.datetime(2024, 1, 1, 10, 0, 0)
    step = RunStep(
        workflow_run_id=1,
        workflow_step_number=1,
        workflow_step_name="test",
        step_type=WorkflowStepType.INGEST,
        status=RunStatus.PENDING,
        step_config_id=1,
        start_date=start,
        completed_date=None,
    )
    assert step.duration is None


@pytest.mark.asyncio
async def test_database_session_exception_rollback():
    """Test that session rolls back on exception (lines 94-96)"""
    await Database.reset("sqlite+aiosqlite:///:memory:")
    try:
        async with Database.session() as session:
            assert session
            raise ValueError("Test exception")  # noqa: TRY301
    except ValueError:
        pass  # Expected exception
    await Database.close()


@pytest.mark.asyncio
async def test_database_close_when_not_initialized():
    """Test Database.close() when engine is None (branch 103->exit)"""
    await Database.close()
    Database._engine = None
    Database._initialized = False
    await Database.close()  # Should not raise


@pytest.mark.asyncio
async def test_get_engine_when_already_initialized():
    """Test get_engine when database is already initialized (branch 123->125)"""
    await Database.reset("sqlite+aiosqlite:///:memory:")
    assert Database._initialized
    engine = await get_engine()
    assert engine is not None
    await Database.close()


@pytest.mark.asyncio
async def test_database_initialize_non_sqlite_url():
    """Test Database.initialize with non-sqlite URL (branch 64->67)"""
    from unittest.mock import AsyncMock
    from unittest.mock import MagicMock
    from unittest.mock import patch

    await Database.close()
    Database._initialized = False
    Database._engine = None

    mock_engine = MagicMock()
    mock_engine.dispose = AsyncMock()
    mock_conn = MagicMock()
    mock_conn.run_sync = AsyncMock()

    mock_begin_cm = MagicMock()
    mock_begin_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_begin_cm.__aexit__ = AsyncMock(return_value=None)
    mock_engine.begin.return_value = mock_begin_cm

    with patch("soliplex.ingester.lib.models.create_async_engine", return_value=mock_engine) as mock_create:
        await Database.initialize("postgresql+asyncpg://localhost/test")
        # Verify connect_args is empty for non-sqlite URL
        mock_create.assert_called_once_with("postgresql+asyncpg://localhost/test", connect_args={})

    await Database.close()


def test_document_bytes_with_explicit_file_size():
    """Test DocumentBytes when file_size is explicitly provided (branch 258->exit)"""
    from soliplex.ingester.lib.models import ArtifactType
    from soliplex.ingester.lib.models import DocumentBytes

    doc_bytes = DocumentBytes(
        hash="test-hash",
        artifact_type=ArtifactType.DOC.value,
        storage_root="test",
        file_bytes=b"test data",
        file_size=100,  # Explicit size, should not be overwritten
    )
    assert doc_bytes.file_size == 100


def test_enum_values():
    """Test enum values are accessible"""
    from soliplex.ingester.lib.models import ArtifactType
    from soliplex.ingester.lib.models import LifeCycleEvent
    from soliplex.ingester.lib.models import RunStatus
    from soliplex.ingester.lib.models import WorkflowStepType

    # ArtifactType values
    assert ArtifactType.DOC.value == "document"
    assert ArtifactType.PARSED_MD.value == "parsed_markdown"
    assert ArtifactType.PARSED_JSON.value == "parsed_json"
    assert ArtifactType.CHUNKS.value == "chunks"
    assert ArtifactType.EMBEDDINGS.value == "embeddings"
    assert ArtifactType.RAG.value == "rag"

    # WorkflowStepType values
    assert WorkflowStepType.INGEST == "ingest"
    assert WorkflowStepType.VALIDATE == "validate"
    assert WorkflowStepType.PARSE == "parse"
    assert WorkflowStepType.CHUNK == "chunk"
    assert WorkflowStepType.EMBED == "embed"
    assert WorkflowStepType.STORE == "store"
    assert WorkflowStepType.ENRICH == "enrich"
    assert WorkflowStepType.ROUTE == "route"

    # LifeCycleEvent values
    assert LifeCycleEvent.GROUP_START == "group_start"
    assert LifeCycleEvent.GROUP_END == "group_end"
    assert LifeCycleEvent.ITEM_START == "item_start"
    assert LifeCycleEvent.ITEM_END == "item_end"
    assert LifeCycleEvent.ITEM_FAILED == "item_failed"
    assert LifeCycleEvent.STEP_START == "step_start"
    assert LifeCycleEvent.STEP_END == "step_end"
    assert LifeCycleEvent.STEP_FAILED == "step_failed"

    # RunStatus values
    assert RunStatus.PENDING == "PENDING"
    assert RunStatus.RUNNING == "RUNNING"
    assert RunStatus.COMPLETED == "COMPLETED"
    assert RunStatus.ERROR == "ERROR"
    assert RunStatus.FAILED == "FAILED"


def test_artifact_mappings():
    """Test ARTIFACTS_FROM_STEPS and ARTIFACTS_TO_STEPS mappings"""
    from soliplex.ingester.lib.models import ARTIFACTS_FROM_STEPS
    from soliplex.ingester.lib.models import ARTIFACTS_TO_STEPS
    from soliplex.ingester.lib.models import ArtifactType
    from soliplex.ingester.lib.models import WorkflowStepType

    # Test ARTIFACTS_FROM_STEPS
    assert ArtifactType.DOC in ARTIFACTS_FROM_STEPS[WorkflowStepType.INGEST]
    assert ArtifactType.PARSED_MD in ARTIFACTS_FROM_STEPS[WorkflowStepType.PARSE]
    assert ArtifactType.PARSED_JSON in ARTIFACTS_FROM_STEPS[WorkflowStepType.PARSE]
    assert ArtifactType.CHUNKS in ARTIFACTS_FROM_STEPS[WorkflowStepType.CHUNK]
    assert ArtifactType.EMBEDDINGS in ARTIFACTS_FROM_STEPS[WorkflowStepType.EMBED]
    assert ArtifactType.RAG in ARTIFACTS_FROM_STEPS[WorkflowStepType.STORE]

    # Test ARTIFACTS_TO_STEPS
    assert ARTIFACTS_TO_STEPS[ArtifactType.DOC] == WorkflowStepType.INGEST
    assert ARTIFACTS_TO_STEPS[ArtifactType.PARSED_MD] == WorkflowStepType.PARSE
    assert ARTIFACTS_TO_STEPS[ArtifactType.PARSED_JSON] == WorkflowStepType.PARSE
    assert ARTIFACTS_TO_STEPS[ArtifactType.CHUNKS] == WorkflowStepType.CHUNK
    assert ARTIFACTS_TO_STEPS[ArtifactType.EMBEDDINGS] == WorkflowStepType.EMBED
    assert ARTIFACTS_TO_STEPS[ArtifactType.RAG] == WorkflowStepType.STORE


def test_sqlmodel_classes_instantiation():
    """Test instantiation of SQLModel classes"""
    from soliplex.ingester.lib.models import ConfigSet
    from soliplex.ingester.lib.models import ConfigSetItem
    from soliplex.ingester.lib.models import DocumentURI
    from soliplex.ingester.lib.models import LifeCycleEvent
    from soliplex.ingester.lib.models import LifecycleHistory
    from soliplex.ingester.lib.models import RunGroup
    from soliplex.ingester.lib.models import RunStatus
    from soliplex.ingester.lib.models import StepConfig
    from soliplex.ingester.lib.models import WorkerCheckin
    from soliplex.ingester.lib.models import WorkflowStepType

    # DocumentURI
    doc_uri = DocumentURI(
        doc_hash="test-hash",
        uri="file://test.txt",
        source="test",
        version=1,
    )
    assert doc_uri.uri == "file://test.txt"

    # RunGroup
    run_group = RunGroup(
        workflow_definition_id="test-workflow",
        param_definition_id="test-params",
        created_date=datetime.datetime.now(),
        status=RunStatus.PENDING,
    )
    assert run_group.workflow_definition_id == "test-workflow"

    # LifecycleHistory
    lifecycle = LifecycleHistory(
        event=LifeCycleEvent.GROUP_START,
        run_group_id=1,
        workflow_run_id=1,
        start_date=datetime.datetime.now(),
        status=RunStatus.PENDING,
    )
    assert lifecycle.event == LifeCycleEvent.GROUP_START

    # WorkerCheckin
    worker = WorkerCheckin(
        id="worker-1",
        first_checkin=datetime.datetime.now(),
        last_checkin=datetime.datetime.now(),
    )
    assert worker.id == "worker-1"

    # StepConfig
    step_config = StepConfig(
        step_type=WorkflowStepType.INGEST,
        config_json={"key": "value"},
        created_date=datetime.datetime.now(),
    )
    assert step_config.step_type == WorkflowStepType.INGEST

    # ConfigSet
    config_set = ConfigSet(
        yaml_id="test-yaml",
        yaml_contents="test: value",
        created_date=datetime.datetime.now(),
    )
    assert config_set.yaml_id == "test-yaml"

    # ConfigSetItem
    config_set_item = ConfigSetItem(
        config_set_id=1,
        config_id=1,
    )
    assert config_set_item.config_set_id == 1


def test_pydantic_models():
    """Test Pydantic BaseModel classes"""
    from soliplex.ingester.lib.models import EventHandler
    from soliplex.ingester.lib.models import PaginatedResponse
    from soliplex.ingester.lib.models import WorkflowDefinition
    from soliplex.ingester.lib.models import WorkflowParams
    from soliplex.ingester.lib.models import WorkflowRunWithSteps
    from soliplex.ingester.lib.models import WorkflowStepType

    # EventHandler
    handler = EventHandler(
        name="test_handler",
        retries=3,
        method="builtins.print",
        parameters={"key": "value"},
    )
    assert handler.name == "test_handler"
    assert handler.retries == 3

    # WorkflowDefinition
    workflow_def = WorkflowDefinition(
        id="test-workflow",
        name="Test Workflow",
        meta={"version": "1.0"},
        item_steps={
            WorkflowStepType.INGEST: EventHandler(
                name="ingest_handler",
                method="builtins.print",
                parameters={},
            )
        },
        lifecycle_events=None,
    )
    assert workflow_def.id == "test-workflow"
    assert workflow_def.name == "Test Workflow"

    # WorkflowParams
    params = WorkflowParams(
        id="test-params",
        name="Test Params",
        meta={"env": "test"},
        config={WorkflowStepType.INGEST: {"batch_size": 10}},
    )
    assert params.id == "test-params"
    assert params.name == "Test Params"

    # WorkflowRunWithSteps
    run = WorkflowRun(
        workflow_definition_id="test",
        batch_id=1,
        doc_id="doc1",
        run_group_id=1,
        start_date=datetime.datetime.now(),
    )
    run_with_steps = WorkflowRunWithSteps(
        workflow_run=run,
        steps=None,
    )
    assert run_with_steps.workflow_run.doc_id == "doc1"
    assert run_with_steps.steps is None

    # PaginatedResponse
    paginated = PaginatedResponse[str](
        items=["a", "b", "c"],
        total=100,
        page=1,
        rows_per_page=10,
        total_pages=10,
    )
    assert paginated.items == ["a", "b", "c"]
    assert paginated.total == 100
    assert paginated.page == 1


def test_workflow_definition_with_lifecycle_events():
    """Test WorkflowDefinition with lifecycle_events populated"""
    from soliplex.ingester.lib.models import EventHandler
    from soliplex.ingester.lib.models import LifeCycleEvent
    from soliplex.ingester.lib.models import WorkflowDefinition
    from soliplex.ingester.lib.models import WorkflowStepType

    handler = EventHandler(
        name="log_handler",
        method="builtins.print",
        parameters={"level": "info"},
    )

    workflow_def = WorkflowDefinition(
        id="test-workflow",
        name="Test Workflow",
        meta={"version": "1.0"},
        item_steps={
            WorkflowStepType.INGEST: handler,
        },
        lifecycle_events={
            LifeCycleEvent.GROUP_START: [handler],
            LifeCycleEvent.GROUP_END: [handler],
        },
    )
    assert workflow_def.lifecycle_events is not None
    assert LifeCycleEvent.GROUP_START in workflow_def.lifecycle_events


def test_workflow_params_minimal():
    """Test WorkflowParams with minimal fields"""
    from soliplex.ingester.lib.models import WorkflowParams
    from soliplex.ingester.lib.models import WorkflowStepType

    params = WorkflowParams(
        id="minimal-params",
        config={WorkflowStepType.PARSE: {"format": "json"}},
    )
    assert params.id == "minimal-params"
    assert params.name is None
    assert params.meta is None


def test_workflow_run_with_steps_populated():
    """Test WorkflowRunWithSteps with steps populated"""
    from soliplex.ingester.lib.models import RunStatus
    from soliplex.ingester.lib.models import WorkflowRunWithSteps
    from soliplex.ingester.lib.models import WorkflowStepType

    run = WorkflowRun(
        workflow_definition_id="test",
        batch_id=1,
        doc_id="doc1",
        run_group_id=1,
        start_date=datetime.datetime.now(),
    )
    step = RunStep(
        workflow_run_id=1,
        workflow_step_number=1,
        workflow_step_name="ingest",
        step_type=WorkflowStepType.INGEST,
        status=RunStatus.COMPLETED,
        step_config_id=1,
    )
    run_with_steps = WorkflowRunWithSteps(
        workflow_run=run,
        steps=[step],
    )
    assert len(run_with_steps.steps) == 1
    assert run_with_steps.steps[0].workflow_step_name == "ingest"
