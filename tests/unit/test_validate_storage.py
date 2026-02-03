"""
Unit tests for validate_storage and delete_documents_by_hashes functionality.

Tests the batch validation of document storage and cascading deletion
of documents by their hashes.
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
async def test_delete_documents_by_hashes_single_document(db):
    """Test deletion of a single document by hash with full cascade."""
    # Create test data
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/validate_test.pdf"
    test_bytes = b"test bytes for validation"
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

    # Execute: Delete the document by hash
    result = await doc_ops.delete_documents_by_hashes([doc_hash])

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
async def test_delete_documents_by_hashes_multiple_documents(db):
    """Test deletion of multiple documents by hashes."""
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")

    # Create multiple documents
    test_uri1 = "/tmp/multi_test1.pdf"
    test_bytes1 = b"first document bytes"
    doc_uri1, doc1 = await doc_ops.create_document_from_uri(
        test_uri1, "test_source", "application/pdf", test_bytes1, batch_id=batch_id
    )

    test_uri2 = "/tmp/multi_test2.pdf"
    test_bytes2 = b"second document bytes"
    doc_uri2, doc2 = await doc_ops.create_document_from_uri(
        test_uri2, "test_source", "application/pdf", test_bytes2, batch_id=batch_id
    )

    test_uri3 = "/tmp/multi_test3.pdf"
    test_bytes3 = b"third document bytes"
    doc_uri3, doc3 = await doc_ops.create_document_from_uri(
        test_uri3, "test_source", "application/pdf", test_bytes3, batch_id=batch_id
    )

    # Delete first two documents
    result = await doc_ops.delete_documents_by_hashes([doc1.hash, doc2.hash])

    # Verify statistics
    assert result["deleted_document_uris"] == 2
    assert result["deleted_documents"] == 2

    # Verify first two documents deleted, third remains
    async with get_session() as session:
        q = select(Document).where(Document.hash == doc1.hash)
        assert (await session.exec(q)).first() is None

        q = select(Document).where(Document.hash == doc2.hash)
        assert (await session.exec(q)).first() is None

        q = select(Document).where(Document.hash == doc3.hash)
        assert (await session.exec(q)).first() is not None


@pytest.mark.asyncio
async def test_delete_documents_by_hashes_empty_list(db):
    """Test deletion with empty list returns zeros."""
    result = await doc_ops.delete_documents_by_hashes([])

    assert result["deleted_document_uris"] == 0
    assert result["deleted_uri_history"] == 0
    assert result["deleted_documents"] == 0
    assert result["deleted_workflow_runs"] == 0
    assert result["deleted_run_steps"] == 0
    assert result["deleted_lifecycle_history"] == 0
    assert result["total_deleted"] == 0


@pytest.mark.asyncio
async def test_delete_documents_by_hashes_nonexistent(db):
    """Test deletion of non-existent hashes returns zeros."""
    result = await doc_ops.delete_documents_by_hashes(["nonexistent_hash_123", "nonexistent_hash_456"])

    assert result["deleted_document_uris"] == 0
    assert result["deleted_documents"] == 0
    assert result["total_deleted"] == 0


@pytest.mark.asyncio
async def test_delete_documents_by_hashes_preserves_other_documents(db):
    """Test that deleting some documents doesn't affect others."""
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")

    # Create two separate documents
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

    doc2_hash = doc2.hash
    doc_uri2_id = doc_uri2.id

    # Delete only first document
    await doc_ops.delete_documents_by_hashes([doc1.hash])

    # Verify second document is preserved
    async with get_session() as session:
        q = select(DocumentURI).where(DocumentURI.id == doc_uri2_id)
        result_uri2 = await session.exec(q)
        assert result_uri2.first() is not None

        q = select(Document).where(Document.hash == doc2_hash)
        result_doc2 = await session.exec(q)
        assert result_doc2.first() is not None


@pytest.mark.asyncio
async def test_delete_documents_by_hashes_multiple_uris_same_document(db):
    """Test deletion when multiple URIs point to the same document."""
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_bytes = b"shared document bytes"

    # Create two URIs pointing to the same document (same bytes = same hash)
    test_uri1 = "/tmp/shared_test1.pdf"
    doc_uri1, doc1 = await doc_ops.create_document_from_uri(
        test_uri1, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    test_uri2 = "/tmp/shared_test2.pdf"
    doc_uri2, doc2 = await doc_ops.create_document_from_uri(
        test_uri2, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    # Verify both URIs point to same document
    assert doc1.hash == doc2.hash
    doc_hash = doc1.hash

    # Delete the document by hash
    result = await doc_ops.delete_documents_by_hashes([doc_hash])

    # Both URIs should be deleted along with the document
    assert result["deleted_document_uris"] == 2
    assert result["deleted_documents"] == 1

    # Verify all deleted
    async with get_session() as session:
        q = select(DocumentURI).where(DocumentURI.doc_hash == doc_hash)
        result_uris = await session.exec(q)
        assert len(result_uris.all()) == 0

        q = select(Document).where(Document.hash == doc_hash)
        result_doc = await session.exec(q)
        assert result_doc.first() is None


@pytest.mark.asyncio
async def test_delete_documents_by_hashes_with_multiple_workflow_runs(db):
    """Test deletion of document with multiple workflow runs."""
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/multi_workflow.pdf"
    test_bytes = b"test bytes for multi workflow"
    doc_uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    # Create multiple workflow runs for the same document
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

    # Delete the document
    result = await doc_ops.delete_documents_by_hashes([doc.hash])

    # Verify statistics
    assert result["deleted_documents"] == 1
    assert result["deleted_workflow_runs"] == 2
    assert result["deleted_run_steps"] == len(steps1) + len(steps2)
    assert result["deleted_lifecycle_history"] == 2


@pytest.mark.asyncio
async def test_delete_documents_by_hashes_no_workflow_runs(db):
    """Test deletion of document without any workflow runs."""
    batch_id = await doc_ops.new_batch("test_source", "Test Batch")
    test_uri = "/tmp/no_workflow.pdf"
    test_bytes = b"test bytes no workflow"
    doc_uri, doc = await doc_ops.create_document_from_uri(
        test_uri, "test_source", "application/pdf", test_bytes, batch_id=batch_id
    )

    doc_hash = doc.hash

    # Delete without creating any workflow runs
    result = await doc_ops.delete_documents_by_hashes([doc_hash])

    # Verify statistics
    assert result["deleted_document_uris"] == 1
    assert result["deleted_documents"] == 1
    assert result["deleted_workflow_runs"] == 0
    assert result["deleted_run_steps"] == 0
    assert result["deleted_lifecycle_history"] == 0

    # Verify document deleted
    async with get_session() as session:
        q = select(Document).where(Document.hash == doc_hash)
        result_doc = await session.exec(q)
        assert result_doc.first() is None
