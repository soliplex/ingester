"""
Unit tests for workflow document info functionality.

Tests the ability to fetch and include Document and DocumentURI information
with workflow runs via the get_document_info_for_workflow_runs function.
"""

import datetime

import pytest
import pytest_asyncio

from soliplex.ingester.lib.models import Database
from soliplex.ingester.lib.models import Document
from soliplex.ingester.lib.models import DocumentBatch
from soliplex.ingester.lib.models import DocumentInfo
from soliplex.ingester.lib.models import DocumentURI
from soliplex.ingester.lib.models import RunGroup
from soliplex.ingester.lib.models import RunStatus
from soliplex.ingester.lib.models import WorkflowRun
from soliplex.ingester.lib.models import WorkflowRunWithDetails
from soliplex.ingester.lib.models import get_session
from soliplex.ingester.lib.wf.operations import get_document_info_for_workflow_runs
from soliplex.ingester.lib.wf.operations import get_workflows
from soliplex.ingester.lib.wf.operations import get_workflows_for_status


@pytest_asyncio.fixture
async def sample_data(db: Database):
    """Create sample test data with documents, URIs, batches, and workflow runs."""
    async with get_session() as session:
        # Create a batch
        batch = DocumentBatch(
            name="Test Batch",
            source="test-source",
            start_date=datetime.datetime.now(datetime.UTC),
        )
        session.add(batch)
        await session.flush()
        await session.refresh(batch)

        # Create documents
        doc1 = Document(
            hash="sha256-abc123",
            mime_type="application/pdf",
            file_size=1024000,
            doc_meta={"page_count": "10"},
        )
        doc2 = Document(
            hash="sha256-def456",
            mime_type="text/plain",
            file_size=512,
            doc_meta={},
        )
        session.add(doc1)
        session.add(doc2)
        await session.flush()

        # Create document URIs
        uri1 = DocumentURI(
            doc_hash="sha256-abc123",
            uri="/path/to/document1.pdf",
            source="test-source",
            batch_id=batch.id,
        )
        uri2 = DocumentURI(
            doc_hash="sha256-def456",
            uri="/path/to/document2.txt",
            source="test-source",
            batch_id=batch.id,
        )
        session.add(uri1)
        session.add(uri2)
        await session.flush()

        # Create run group
        run_group = RunGroup(
            workflow_definition_id="batch",
            param_definition_id="default",
            batch_id=batch.id,
            created_date=datetime.datetime.now(datetime.UTC),
            start_date=datetime.datetime.now(datetime.UTC),
            status=RunStatus.RUNNING,
            status_date=datetime.datetime.now(datetime.UTC),
        )
        session.add(run_group)
        await session.flush()
        await session.refresh(run_group)

        # Create workflow runs
        run1 = WorkflowRun(
            workflow_definition_id="batch",
            run_group_id=run_group.id,
            batch_id=batch.id,
            doc_id="sha256-abc123",
            created_date=datetime.datetime.now(datetime.UTC),
            start_date=datetime.datetime.now(datetime.UTC),
            status=RunStatus.RUNNING,
            status_date=datetime.datetime.now(datetime.UTC),
            run_params={"param_id": "default", "source": "test-source"},
        )
        run2 = WorkflowRun(
            workflow_definition_id="batch",
            run_group_id=run_group.id,
            batch_id=batch.id,
            doc_id="sha256-def456",
            created_date=datetime.datetime.now(datetime.UTC),
            start_date=datetime.datetime.now(datetime.UTC),
            status=RunStatus.COMPLETED,
            completed_date=datetime.datetime.now(datetime.UTC),
            status_date=datetime.datetime.now(datetime.UTC),
            run_params={"param_id": "default", "source": "test-source"},
        )
        session.add(run1)
        session.add(run2)
        await session.flush()
        await session.refresh(run1)
        await session.refresh(run2)

        # Expunge all objects before committing
        session.expunge(batch)
        session.expunge(doc1)
        session.expunge(doc2)
        session.expunge(uri1)
        session.expunge(uri2)
        session.expunge(run_group)
        session.expunge(run1)
        session.expunge(run2)
        await session.commit()

        return {
            "batch": batch,
            "documents": [doc1, doc2],
            "uris": [uri1, uri2],
            "run_group": run_group,
            "workflow_runs": [run1, run2],
        }


@pytest.mark.asyncio
async def test_document_info_model():
    """Test DocumentInfo model serialization and validation."""
    doc_info = DocumentInfo(
        uri="/path/to/file.pdf",
        source="test-source",
        file_size=1024,
        mime_type="application/pdf",
    )

    assert doc_info.uri == "/path/to/file.pdf"
    assert doc_info.source == "test-source"
    assert doc_info.file_size == 1024
    assert doc_info.mime_type == "application/pdf"

    # Test JSON serialization
    json_data = doc_info.model_dump()
    assert json_data["uri"] == "/path/to/file.pdf"
    assert json_data["file_size"] == 1024


@pytest.mark.asyncio
async def test_document_info_nullable_fields():
    """Test DocumentInfo with null fields."""
    doc_info = DocumentInfo()

    assert doc_info.uri is None
    assert doc_info.source is None
    assert doc_info.file_size is None
    assert doc_info.mime_type is None


@pytest.mark.asyncio
async def test_workflow_run_with_details_model():
    """Test WorkflowRunWithDetails model structure."""
    # Create a mock workflow run
    workflow_run = WorkflowRun(
        id=1,
        workflow_definition_id="batch",
        run_group_id=1,
        batch_id=1,
        doc_id="sha256-test",
        created_date=datetime.datetime.now(datetime.UTC),
        status=RunStatus.PENDING,
        run_params={},
    )

    doc_info = DocumentInfo(
        uri="/test.pdf",
        source="test",
        file_size=100,
        mime_type="application/pdf",
    )

    details = WorkflowRunWithDetails(
        workflow_run=workflow_run,
        steps=None,
        document_info=doc_info,
    )

    assert details.workflow_run.id == 1
    assert details.steps is None
    assert details.document_info.uri == "/test.pdf"


@pytest.mark.asyncio
async def test_get_document_info_for_workflow_runs(sample_data):
    """Test fetching document info for workflow runs."""
    workflow_runs = sample_data["workflow_runs"]

    doc_info_map = await get_document_info_for_workflow_runs(workflow_runs)

    # Should have info for both documents
    assert len(doc_info_map) == 2

    # Check first document info
    info1 = doc_info_map["sha256-abc123"]
    assert info1.uri == "/path/to/document1.pdf"
    assert info1.source == "test-source"
    assert info1.file_size == 1024000
    assert info1.mime_type == "application/pdf"

    # Check second document info
    info2 = doc_info_map["sha256-def456"]
    assert info2.uri == "/path/to/document2.txt"
    assert info2.source == "test-source"
    assert info2.file_size == 512
    assert info2.mime_type == "text/plain"


@pytest.mark.asyncio
async def test_get_document_info_empty_list(db: Database):
    """Test fetching document info with empty workflow list."""
    doc_info_map = await get_document_info_for_workflow_runs([])

    assert doc_info_map == {}


@pytest.mark.asyncio
async def test_get_workflows_with_doc_info(sample_data):
    """Test get_workflows with include_doc_info=True."""
    batch_id = sample_data["batch"].id

    items, total = await get_workflows(batch_id, include_doc_info=True)

    assert total == 2
    assert len(items) == 2

    # Items should be WorkflowRunWithDetails
    for item in items:
        assert isinstance(item, WorkflowRunWithDetails)
        assert item.document_info is not None
        assert item.document_info.uri is not None
        assert item.document_info.source == "test-source"


@pytest.mark.asyncio
async def test_get_workflows_without_doc_info(sample_data):
    """Test get_workflows with include_doc_info=False (default)."""
    batch_id = sample_data["batch"].id

    items, total = await get_workflows(batch_id, include_doc_info=False)

    assert total == 2
    assert len(items) == 2

    # Items should be plain WorkflowRun
    for item in items:
        assert isinstance(item, WorkflowRun)
        assert not hasattr(item, "document_info") or item.document_info is None


@pytest.mark.asyncio
async def test_get_workflows_for_status_with_doc_info(sample_data):
    """Test get_workflows_for_status with include_doc_info=True."""
    batch_id = sample_data["batch"].id

    items, total = await get_workflows_for_status(RunStatus.RUNNING, batch_id, include_doc_info=True)

    assert total == 1
    assert len(items) == 1

    item = items[0]
    assert isinstance(item, WorkflowRunWithDetails)
    assert item.document_info is not None
    assert item.document_info.mime_type == "application/pdf"


@pytest.mark.asyncio
async def test_get_workflows_for_status_without_doc_info(sample_data):
    """Test get_workflows_for_status with include_doc_info=False."""
    batch_id = sample_data["batch"].id

    items, total = await get_workflows_for_status(RunStatus.COMPLETED, batch_id, include_doc_info=False)

    assert total == 1
    assert len(items) == 1

    item = items[0]
    assert isinstance(item, WorkflowRun)


@pytest.mark.asyncio
async def test_get_workflows_with_both_steps_and_doc_info(sample_data):
    """Test get_workflows with both include_steps and include_doc_info."""
    batch_id = sample_data["batch"].id

    items, total = await get_workflows(batch_id, include_steps=True, include_doc_info=True)

    assert total == 2
    assert len(items) == 2

    for item in items:
        assert isinstance(item, WorkflowRunWithDetails)
        assert item.document_info is not None
        # Steps should be empty list (no steps created in sample data)
        assert item.steps == []


@pytest.mark.asyncio
async def test_document_info_missing_document(db: Database):
    """Test handling when Document record doesn't exist."""
    now = datetime.datetime.now(datetime.UTC)
    async with get_session() as session:
        # Create batch and run without corresponding Document
        batch = DocumentBatch(
            name="Test Batch",
            source="test",
            start_date=now,
        )
        session.add(batch)
        await session.flush()
        await session.refresh(batch)

        run_group = RunGroup(
            workflow_definition_id="batch",
            param_definition_id="default",
            batch_id=batch.id,
            created_date=now,
            start_date=now,
            status=RunStatus.RUNNING,
            status_date=now,
        )
        session.add(run_group)
        await session.flush()
        await session.refresh(run_group)

        # Create workflow run without a Document
        run = WorkflowRun(
            workflow_definition_id="batch",
            run_group_id=run_group.id,
            batch_id=batch.id,
            doc_id="sha256-nonexistent",
            created_date=now,
            start_date=now,
            status=RunStatus.PENDING,
            run_params={},
        )
        session.add(run)
        await session.flush()
        await session.refresh(run)
        session.expunge(run)
        await session.commit()

    doc_info_map = await get_document_info_for_workflow_runs([run])

    # Should still return info, but with None values for Document fields
    assert "sha256-nonexistent" in doc_info_map
    info = doc_info_map["sha256-nonexistent"]
    assert info.file_size is None
    assert info.mime_type is None
    assert info.uri is None  # No DocumentURI either
    assert info.source is None
