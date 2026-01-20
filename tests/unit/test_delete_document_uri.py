"""
Unit tests for delete_document_uri_by_uri functionality.

Tests the cascading deletion of DocumentURI records with conditional
document deletion based on reference count.
"""

import pytest
from sqlmodel import select

import soliplex.ingester.lib.operations as doc_ops
import soliplex.ingester.lib.wf.operations as wf_ops
from soliplex.ingester.lib.models import Document
from soliplex.ingester.lib.models import DocumentURI
from soliplex.ingester.lib.models import DocumentURIHistory
from soliplex.ingester.lib.models import LifeCycleEvent
from soliplex.ingester.lib.models import LifecycleHistory
from soliplex.ingester.lib.models import RunStatus
from soliplex.ingester.lib.models import RunStep
from soliplex.ingester.lib.models import WorkflowRun
from soliplex.ingester.lib.models import get_session


@pytest.mark.asyncio
async def test_delete_document_uri_single_reference(db):
    """Test deletion when only one URI references the document - full cascade."""
    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/delete_uri_test.pdf"
    test_bytes = b"test bytes for uri deletion"
    doc_uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    doc_hash = doc.hash
    doc_uri_id = doc_uri.id

    # Create workflow run and steps
    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Create lifecycle history
    await wf_ops.create_lifecycle_history(
        run_group_id=run_group.id,
        workflow_run_id=workflow_run.id,
        event=LifeCycleEvent.ITEM_START,
        status=RunStatus.RUNNING,
    )

    # Execute: Delete the DocumentURI
    result = await doc_ops.delete_document_uri_by_uri(test_uri, "test_source")

    # Verify statistics
    assert result["deleted_document_uris"] == 1
    assert result["deleted_uri_history"] >= 1
    assert result["deleted_documents"] == 1
    assert result["deleted_workflow_runs"] == 1
    assert result["deleted_run_steps"] == len(steps)
    assert result["deleted_lifecycle_history"] >= 1
    assert result["total_deleted"] == (
        result["deleted_document_uris"]
        + result["deleted_uri_history"]
        + result["deleted_documents"]
        + result["deleted_workflow_runs"]
        + result["deleted_run_steps"]
        + result["deleted_lifecycle_history"]
    )

    # Verify records actually deleted
    async with get_session() as session:
        # DocumentURI should be gone
        q = select(DocumentURI).where(DocumentURI.id == doc_uri_id)
        result_uri = await session.exec(q)
        assert result_uri.first() is None

        # Document should be gone
        q = select(Document).where(Document.hash == doc_hash)
        result_doc = await session.exec(q)
        assert result_doc.first() is None

        # WorkflowRun should be gone
        q = select(WorkflowRun).where(WorkflowRun.doc_id == doc_hash)
        result_runs = await session.exec(q)
        assert len(result_runs.all()) == 0

        # RunSteps should be gone
        q = select(RunStep).where(RunStep.workflow_run_id == workflow_run.id)
        result_steps = await session.exec(q)
        assert len(result_steps.all()) == 0

        # LifecycleHistory should be gone
        q = select(LifecycleHistory).where(LifecycleHistory.workflow_run_id == workflow_run.id)
        result_lifecycle = await session.exec(q)
        assert len(result_lifecycle.all()) == 0

        # DocumentURIHistory should be gone
        q = select(DocumentURIHistory).where(DocumentURIHistory.doc_uri_id == doc_uri_id)
        result_history = await session.exec(q)
        assert len(result_history.all()) == 0


@pytest.mark.asyncio
async def test_delete_document_uri_multiple_references(db):
    """Test deletion when multiple URIs reference the document - only URI deleted."""
    # Create test data with two URIs pointing to the same document
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_bytes = b"shared document bytes"

    # Create first URI
    test_uri1 = "/tmp/delete_uri_test1.pdf"
    doc_uri1, doc = await doc_ops.create_document_from_uri(
        test_uri1, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    # Create second URI pointing to the same document (same bytes = same hash)
    test_uri2 = "/tmp/delete_uri_test2.pdf"
    doc_uri2, doc2 = await doc_ops.create_document_from_uri(
        test_uri2, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    # Verify both URIs point to same document
    assert doc.hash == doc2.hash
    doc_hash = doc.hash
    doc_uri1_id = doc_uri1.id
    doc_uri2_id = doc_uri2.id

    # Create workflow run for the document
    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    workflow_run, steps = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Execute: Delete only the first DocumentURI
    result = await doc_ops.delete_document_uri_by_uri(test_uri1, "test_source")

    # Verify statistics - document and workflow records should NOT be deleted
    assert result["deleted_document_uris"] == 1
    assert result["deleted_uri_history"] >= 1
    assert result["deleted_documents"] == 0  # Document preserved
    assert result["deleted_workflow_runs"] == 0  # WorkflowRun preserved
    assert result["deleted_run_steps"] == 0  # Steps preserved
    assert result["deleted_lifecycle_history"] == 0  # History preserved

    # Verify first URI deleted but second URI and document remain
    async with get_session() as session:
        # First DocumentURI should be gone
        q = select(DocumentURI).where(DocumentURI.id == doc_uri1_id)
        result_uri1 = await session.exec(q)
        assert result_uri1.first() is None

        # Second DocumentURI should still exist
        q = select(DocumentURI).where(DocumentURI.id == doc_uri2_id)
        result_uri2 = await session.exec(q)
        assert result_uri2.first() is not None

        # Document should still exist
        q = select(Document).where(Document.hash == doc_hash)
        result_doc = await session.exec(q)
        assert result_doc.first() is not None

        # WorkflowRun should still exist
        q = select(WorkflowRun).where(WorkflowRun.doc_id == doc_hash)
        result_runs = await session.exec(q)
        assert len(result_runs.all()) == 1

        # RunSteps should still exist
        q = select(RunStep).where(RunStep.workflow_run_id == workflow_run.id)
        result_steps = await session.exec(q)
        assert len(result_steps.all()) == len(steps)


@pytest.mark.asyncio
async def test_delete_document_uri_not_found(db):
    """Test that DocumentURINotFoundError is raised for non-existent URI."""
    with pytest.raises(
        doc_ops.DocumentURINotFoundError,
        match="DocumentURI not found for uri=/nonexistent/path.pdf, source=test_source",
    ):
        await doc_ops.delete_document_uri_by_uri("/nonexistent/path.pdf", "test_source")


@pytest.mark.asyncio
async def test_delete_document_uri_wrong_source(db):
    """Test that deletion fails when source doesn't match."""
    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/delete_source_test.pdf"
    test_bytes = b"test bytes"
    await doc_ops.create_document_from_uri(test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id)

    # Try to delete with wrong source
    with pytest.raises(
        doc_ops.DocumentURINotFoundError,
        match="DocumentURI not found for uri=/tmp/delete_source_test.pdf, source=wrong_source",
    ):
        await doc_ops.delete_document_uri_by_uri(test_uri, "wrong_source")


@pytest.mark.asyncio
async def test_delete_document_uri_no_workflow_runs(db):
    """Test deletion of URI without any associated workflow runs."""
    # Create test data without workflow runs
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/delete_no_workflow.pdf"
    test_bytes = b"test bytes no workflow"
    doc_uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    doc_hash = doc.hash

    # Execute: Delete the DocumentURI
    result = await doc_ops.delete_document_uri_by_uri(test_uri, "test_source")

    # Verify statistics
    assert result["deleted_document_uris"] == 1
    assert result["deleted_uri_history"] >= 1
    assert result["deleted_documents"] == 1
    assert result["deleted_workflow_runs"] == 0
    assert result["deleted_run_steps"] == 0
    assert result["deleted_lifecycle_history"] == 0

    # Verify document deleted
    async with get_session() as session:
        q = select(Document).where(Document.hash == doc_hash)
        result_doc = await session.exec(q)
        assert result_doc.first() is None


@pytest.mark.asyncio
async def test_delete_document_uri_idempotency(db):
    """Test that deleting an already-deleted URI raises NotFoundError."""
    # Create and delete a DocumentURI
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/delete_idempotent.pdf"
    test_bytes = b"test bytes idempotent"
    await doc_ops.create_document_from_uri(test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id)

    # First deletion should succeed
    result = await doc_ops.delete_document_uri_by_uri(test_uri, "test_source")
    assert result["deleted_document_uris"] == 1

    # Second deletion should fail
    with pytest.raises(
        doc_ops.DocumentURINotFoundError,
        match=f"DocumentURI not found for uri={test_uri}, source=test_source",
    ):
        await doc_ops.delete_document_uri_by_uri(test_uri, "test_source")


@pytest.mark.asyncio
async def test_delete_document_uri_preserves_other_uris(db):
    """Test that deleting one URI doesn't affect unrelated URIs."""
    # Create two completely separate documents with different URIs
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")

    test_uri1 = "/tmp/preserve_test1.pdf"
    test_bytes1 = b"first document bytes"
    doc_uri1, doc1 = await doc_ops.create_document_from_uri(
        test_uri1, "test_source", "application/pdf", test_bytes1, batch_id=batch_id
    )

    test_uri2 = "/tmp/preserve_test2.pdf"
    test_bytes2 = b"second document bytes different"
    doc_uri2, doc2 = await doc_ops.create_document_from_uri(
        test_uri2, "test_source", "application/pdf", test_bytes2, batch_id=batch_id
    )

    doc_uri2_id = doc_uri2.id
    doc2_hash = doc2.hash

    # Delete first URI
    await doc_ops.delete_document_uri_by_uri(test_uri1, "test_source")

    # Verify second URI and document are preserved
    async with get_session() as session:
        q = select(DocumentURI).where(DocumentURI.id == doc_uri2_id)
        result_uri2 = await session.exec(q)
        assert result_uri2.first() is not None

        q = select(Document).where(Document.hash == doc2_hash)
        result_doc2 = await session.exec(q)
        assert result_doc2.first() is not None


@pytest.mark.asyncio
async def test_delete_document_uri_statistics_accuracy(db):
    """Test that deletion statistics are accurate."""
    # Create test data with known quantities
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/stats_accuracy.pdf"
    test_bytes = b"test bytes for stats"
    doc_uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    # Create multiple workflow runs
    run_group = await wf_ops.create_run_group(workflow_definition_id="batch", batch_id=batch_id, param_id="test_base")
    workflow_run1, steps1 = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)
    workflow_run2, steps2 = await wf_ops.create_workflow_run(run_group=run_group, doc_id=doc.hash)

    # Create lifecycle history for each run
    await wf_ops.create_lifecycle_history(
        run_group_id=run_group.id,
        workflow_run_id=workflow_run1.id,
        event=LifeCycleEvent.ITEM_START,
        status=RunStatus.RUNNING,
    )
    await wf_ops.create_lifecycle_history(
        run_group_id=run_group.id,
        workflow_run_id=workflow_run2.id,
        event=LifeCycleEvent.ITEM_START,
        status=RunStatus.RUNNING,
    )

    # Execute: Delete the DocumentURI
    result = await doc_ops.delete_document_uri_by_uri(test_uri, "test_source")

    # Verify exact statistics
    assert result["deleted_document_uris"] == 1
    assert result["deleted_documents"] == 1
    assert result["deleted_workflow_runs"] == 2
    assert result["deleted_run_steps"] == len(steps1) + len(steps2)
    assert result["deleted_lifecycle_history"] == 2
    assert result["total_deleted"] == (
        1  # document_uris
        + result["deleted_uri_history"]
        + 1  # documents
        + 2  # workflow_runs
        + len(steps1)
        + len(steps2)  # run_steps
        + 2  # lifecycle_history
    )
